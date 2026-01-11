"""File restoration from checkpoints."""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import orjson

from one_claude.core.file_history import FileHistoryManager
from one_claude.core.models import Message, MessageType, Session
from one_claude.core.parser import extract_file_paths_from_message
from one_claude.core.scanner import ClaudeScanner
from one_claude.teleport.sandbox import TeleportSandbox


@dataclass
class RestorePoint:
    """A point in time that can be restored."""

    message_uuid: str
    timestamp: datetime
    description: str
    file_count: int


@dataclass
class TeleportSession:
    """An active teleport session."""

    id: str
    session: Session
    restore_point: str
    sandbox: TeleportSandbox
    files_restored: dict[str, str]  # path -> hash
    created_at: datetime


class FileRestorer:
    """Restores file state from checkpoints into sandbox."""

    def __init__(self, scanner: ClaudeScanner):
        self.scanner = scanner
        self.file_history = FileHistoryManager(scanner.file_history_dir)
        self._path_cache: dict[str, dict[str, str]] = {}  # session_id -> {hash: path}

    def get_restorable_points(self, session: Session) -> list[RestorePoint]:
        """Get list of points that can be restored (any message can be a restore point)."""
        tree = self.scanner.load_session_messages(session)
        checkpoints = self.file_history.get_checkpoints_for_session(session.id)

        points = []
        seen_messages = set()

        # Walk through messages and create restore points
        for msg in tree.all_messages():
            if msg.uuid in seen_messages:
                continue
            seen_messages.add(msg.uuid)

            # Any message can be a restore point
            # But prioritize ones with file operations for the description
            if msg.type == MessageType.FILE_HISTORY_SNAPSHOT:
                points.append(
                    RestorePoint(
                        message_uuid=msg.uuid,
                        timestamp=msg.timestamp,
                        description="File snapshot",
                        file_count=len(checkpoints),
                    )
                )
            elif msg.type == MessageType.ASSISTANT and msg.tool_uses:
                file_tools = [t for t in msg.tool_uses if t.name in ("Write", "Edit")]
                if file_tools:
                    desc = f"{len(file_tools)} file(s) modified"
                    points.append(
                        RestorePoint(
                            message_uuid=msg.uuid,
                            timestamp=msg.timestamp,
                            description=desc,
                            file_count=len(file_tools),
                        )
                    )
            elif msg.type == MessageType.USER:
                # User messages as restore points too
                content_preview = ""
                if isinstance(msg.content, str):
                    content_preview = msg.content[:30] + "..." if len(msg.content) > 30 else msg.content
                elif isinstance(msg.content, list):
                    for item in msg.content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text = item.get("text", "")
                            content_preview = text[:30] + "..." if len(text) > 30 else text
                            break
                points.append(
                    RestorePoint(
                        message_uuid=msg.uuid,
                        timestamp=msg.timestamp,
                        description=content_preview or "User message",
                        file_count=0,
                    )
                )

        # Sort by timestamp descending
        points.sort(key=lambda p: p.timestamp, reverse=True)
        return points[:50]  # Limit to 50 restore points

    def build_path_mapping(self, session: Session) -> dict[str, str]:
        """Build mapping from path hashes to original paths."""
        if session.id in self._path_cache:
            return self._path_cache[session.id]

        tree = self.scanner.load_session_messages(session)
        mapping: dict[str, str] = {}

        for msg in tree.all_messages():
            paths = extract_file_paths_from_message(msg)
            for path in paths:
                path_hash = self._compute_hash(path)
                if path_hash not in mapping:
                    mapping[path_hash] = path

        self._path_cache[session.id] = mapping
        return mapping

    def _compute_hash(self, path: str) -> str:
        """Compute path hash matching Claude Code's format."""
        return hashlib.sha256(path.encode()).hexdigest()[:16]

    def _truncate_jsonl_to_message(
        self,
        jsonl_path: Path,
        target_uuid: str,
    ) -> bytes:
        """Read JSONL and truncate to include only messages up to target_uuid.

        Returns the truncated JSONL content as bytes.
        If target_uuid is empty, returns all lines (for "latest" teleport).
        """
        lines_to_keep = []

        with open(jsonl_path, "rb") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    data = orjson.loads(line)
                except Exception:
                    continue

                lines_to_keep.append(line)

                # If we have a specific target, stop when we find it
                if target_uuid:
                    msg_uuid = data.get("uuid", "")
                    if msg_uuid == target_uuid:
                        break

        return b"\n".join(lines_to_keep) + b"\n"

    def _get_file_history_for_session(self, session: Session) -> dict[str, bytes]:
        """Get all file history checkpoints for a session.

        Returns a dict of {filename: content} for files to copy.
        File-history structure is flat: file-history/<session_id>/<hash>@v1
        """
        file_history_files: dict[str, bytes] = {}

        # Get the file-history directory for this session
        fh_session_dir = self.scanner.file_history_dir / session.id
        if not fh_session_dir.exists():
            return file_history_files

        # Walk through all checkpoint files (flat structure)
        for cp_file in fh_session_dir.iterdir():
            if cp_file.is_dir():
                continue
            try:
                content = cp_file.read_bytes()
                # Just use the filename (e.g., "hash@v1")
                file_history_files[cp_file.name] = content
            except Exception:
                continue

        return file_history_files

    async def restore_to_sandbox(
        self,
        session: Session,
        message_uuid: str | None = None,
        mode: str = "docker",
    ) -> TeleportSession:
        """
        Restore files to sandbox at specified point.

        Args:
            session: Session to restore
            message_uuid: Message to restore to (latest if None)
            mode: Execution mode - "local", "docker", or "microvm"
        """
        import shutil

        # Create sandbox with project path (use display path for actual filesystem path)
        sandbox = TeleportSandbox(
            session_id=session.id,
            project_path=session.project_display,
            mode=mode,
        )
        await sandbox.start()

        files_restored: dict[str, str] = {}
        is_latest = not message_uuid or message_uuid == ""

        # For sandbox modes (docker/microvm), copy project directory
        # Local mode runs in-place so doesn't need copying
        if mode != "local":
            project_dir = Path(session.project_display)
            if project_dir.exists() and project_dir.is_dir():
                # Copy all project files to sandbox workspace
                for item in project_dir.rglob("*"):
                    if item.is_file():
                        rel_path = item.relative_to(project_dir)
                        try:
                            content = item.read_bytes()
                            abs_path = str(project_dir / rel_path)
                            await sandbox.write_file(abs_path, content)
                            files_restored[abs_path] = "original"
                        except (PermissionError, OSError):
                            continue

        # If rewinding (not latest), apply checkpoint file states
        if not is_latest:
            checkpoints = self.file_history.get_checkpoints_for_session(session.id)
            path_mapping = self.build_path_mapping(session)

            for path_hash, versions in checkpoints.items():
                if not versions:
                    continue

                # Use latest version (versions are sorted by version number)
                # TODO: Filter by target timestamp if we add mtime to FileCheckpoint
                applicable_version = versions[-1] if versions else None

                if not applicable_version:
                    continue

                # Resolve original path
                original_path = path_mapping.get(path_hash)
                if not original_path:
                    continue

                # Read checkpoint content
                try:
                    content = applicable_version.read_content()
                except Exception:
                    continue

                # Overwrite with checkpoint version
                await sandbox.write_file(original_path, content)
                files_restored[original_path] = path_hash

        # Setup claude config directory
        source_claude_dir = self.scanner.claude_dir
        # Inside sandbox, CWD is /workspace/<original-path>, so Claude will look for
        # projects/-workspace-<original-path>/. We need to match that.
        # Claude escapes both / and _ as - in project directory names.
        sandbox_project_path = f"/workspace{session.project_display}"
        project_dir_name = sandbox_project_path.replace("/", "-").replace("_", "-")

        # Create truncated JSONL
        jsonl_content = self._truncate_jsonl_to_message(
            session.jsonl_path,
            message_uuid or "",
        )

        # Get all file history for the session
        file_history_files = self._get_file_history_for_session(session)

        # Setup the claude config in sandbox
        sandbox.setup_claude_config(
            source_claude_dir=source_claude_dir,
            project_dir_name=project_dir_name,
            jsonl_content=jsonl_content,
            file_history_files=file_history_files,
        )

        return TeleportSession(
            id=str(uuid.uuid4()),
            session=session,
            restore_point=message_uuid or "latest",
            sandbox=sandbox,
            files_restored=files_restored,
            created_at=datetime.now(),
        )

    async def cleanup(self, teleport_session: TeleportSession) -> None:
        """Clean up a teleport session."""
        await teleport_session.sandbox.stop()
