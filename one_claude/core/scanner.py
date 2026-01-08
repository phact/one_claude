"""Scanner for discovering Claude Code sessions in ~/.claude."""

import hashlib
import re
from datetime import datetime
from pathlib import Path

from one_claude.core.models import FileCheckpoint, MessageTree, Project, Session
from one_claude.core.parser import SessionParser


class ClaudeScanner:
    """Scans ~/.claude for sessions and file history."""

    def __init__(self, claude_dir: Path | None = None):
        self.claude_dir = claude_dir or Path.home() / ".claude"
        self.projects_dir = self.claude_dir / "projects"
        self.file_history_dir = self.claude_dir / "file-history"
        self.parser = SessionParser()

    def scan_all(self) -> list[Project]:
        """Discover all projects and their sessions."""
        projects = []

        if not self.projects_dir.exists():
            return projects

        for project_dir in sorted(self.projects_dir.iterdir()):
            if project_dir.is_dir():
                project = self._scan_project(project_dir)
                if project.sessions:
                    projects.append(project)

        return projects

    def _scan_project(self, project_dir: Path) -> Project:
        """Scan a single project directory."""
        escaped_path = project_dir.name
        display_path = self._unescape_path(escaped_path)

        project = Project(path=escaped_path, display_path=display_path)

        # Find all session JSONL files
        for jsonl_file in sorted(project_dir.glob("*.jsonl")):
            session = self._scan_session_file(jsonl_file, project)
            if session:
                project.sessions.append(session)

        # Link agent sessions to their parents
        sessions_by_id = {s.id: s for s in project.sessions}
        for session in project.sessions:
            if session.is_agent and session.parent_session_id:
                parent = sessions_by_id.get(session.parent_session_id)
                if parent:
                    parent.child_agent_ids.append(session.id)

        # Sort sessions by updated_at descending
        project.sessions.sort(key=lambda s: s.updated_at, reverse=True)

        return project

    def _scan_session_file(self, jsonl_path: Path, project: Project) -> Session | None:
        """Scan a single session JSONL file for metadata."""
        try:
            stat = jsonl_path.stat()
            updated_at = datetime.fromtimestamp(stat.st_mtime)

            # Extract session ID from filename
            session_id = jsonl_path.stem

            # Detect if this is an agent session by filename pattern
            is_agent = session_id.startswith("agent-")
            parent_session_id: str | None = None

            # Quick scan for message count and first timestamp
            message_count = 0
            checkpoint_count = 0
            first_timestamp: datetime | None = None
            first_user_message = ""

            with open(jsonl_path, "rb") as f:
                import orjson

                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = orjson.loads(line)

                        # Check for parent session ID in agent sessions
                        if is_agent and parent_session_id is None:
                            parent_session_id = data.get("sessionId")

                        msg_type = data.get("type")
                        if msg_type == "file-history-snapshot":
                            checkpoint_count += 1
                        elif msg_type in ("user", "assistant", "summary"):
                            message_count += 1

                            # Get first timestamp
                            if first_timestamp is None:
                                ts_str = data.get("timestamp", "")
                                if ts_str:
                                    try:
                                        first_timestamp = datetime.fromisoformat(
                                            ts_str.replace("Z", "+00:00")
                                        )
                                    except ValueError:
                                        pass

                            # Get first user message for title
                            if msg_type == "user" and not first_user_message:
                                message_data = data.get("message", {})
                                content = message_data.get("content", "")
                                if isinstance(content, str):
                                    first_user_message = content
                                elif isinstance(content, list):
                                    for block in content:
                                        if isinstance(block, dict) and block.get("type") == "text":
                                            first_user_message = block.get("text", "")
                                            break
                    except Exception:
                        continue

            if message_count == 0:
                return None

            created_at = first_timestamp or updated_at

            # Generate title from first user message
            title = self._generate_title(first_user_message)

            return Session(
                id=session_id,
                project_path=project.path,
                project_display=project.display_path,
                jsonl_path=jsonl_path,
                created_at=created_at,
                updated_at=updated_at,
                message_count=message_count,
                checkpoint_count=checkpoint_count,
                title=title,
                is_agent=is_agent,
                parent_session_id=parent_session_id,
            )

        except Exception:
            return None

    def _generate_title(self, first_message: str) -> str:
        """Generate a short title from the first user message."""
        if not first_message:
            return "Untitled Session"

        # Clean up and truncate
        title = first_message.strip()
        title = re.sub(r"\s+", " ", title)  # Normalize whitespace

        if len(title) > 200:
            title = title[:197] + "..."

        return title or "Untitled Session"

    def _unescape_path(self, escaped: str) -> str:
        """Convert escaped path back to real path."""
        # -home-tato-Desktop-project -> /home/tato/Desktop/project
        if escaped.startswith("-"):
            return "/" + escaped[1:].replace("-", "/")
        return escaped.replace("-", "/")

    def load_session_messages(self, session: Session) -> MessageTree:
        """Load full message tree for a session."""
        if session.message_tree is not None:
            return session.message_tree

        tree = self.parser.parse_file(session.jsonl_path)
        session.message_tree = tree
        return tree

    def get_file_checkpoints(self, session_id: str) -> list[FileCheckpoint]:
        """Get all file checkpoints for a session."""
        checkpoints = []
        session_history_dir = self.file_history_dir / session_id

        if not session_history_dir.exists():
            return checkpoints

        # Parse checkpoint files: <hash>@v<version>
        checkpoint_pattern = re.compile(r"^([a-f0-9]{16})@v(\d+)$")

        for checkpoint_file in sorted(session_history_dir.iterdir()):
            if checkpoint_file.is_file():
                match = checkpoint_pattern.match(checkpoint_file.name)
                if match:
                    path_hash = match.group(1)
                    version = int(match.group(2))
                    checkpoints.append(
                        FileCheckpoint(
                            path_hash=path_hash,
                            version=version,
                            session_id=session_id,
                            file_path=checkpoint_file,
                        )
                    )

        return checkpoints

    def get_sessions_flat(self, include_agents: bool = False) -> list[Session]:
        """Get all sessions across all projects as a flat list."""
        sessions = []
        for project in self.scan_all():
            for session in project.sessions:
                if include_agents or not session.is_agent:
                    sessions.append(session)
        # Sort by updated_at descending
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    def get_session_by_id(self, session_id: str) -> Session | None:
        """Get a session by its ID."""
        for project in self.scan_all():
            for session in project.sessions:
                if session.id == session_id:
                    return session
        return None

    def get_agent_sessions(self, parent_session_id: str) -> list[Session]:
        """Get all agent sessions for a parent session."""
        agents = []
        for project in self.scan_all():
            for session in project.sessions:
                if session.is_agent and session.parent_session_id == parent_session_id:
                    agents.append(session)
        agents.sort(key=lambda s: s.created_at)
        return agents


def compute_path_hash(file_path: str) -> str:
    """Compute the hash used for file-history filenames."""
    return hashlib.sha256(file_path.encode()).hexdigest()[:16]
