"""Scanner for discovering Claude Code sessions in ~/.claude."""

import hashlib
import re
from datetime import datetime
from pathlib import Path

from one_claude.core.models import (
    ConversationPath,
    FileCheckpoint,
    Message,
    MessageTree,
    MessageType,
    Project,
    Session,
)
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
            has_real_messages = False  # Has user/assistant, not just summary
            first_timestamp: datetime | None = None
            first_user_message = ""

            # Track for branch detection
            parent_child_count: dict[str, int] = {}  # parent_uuid -> child count
            tool_result_parents: set[str] = set()  # Parents with tool_result children

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
                            if msg_type in ("user", "assistant"):
                                has_real_messages = True

                                # Track parent-child relationships for branch detection
                                parent_uuid = data.get("parentUuid")
                                if parent_uuid:
                                    parent_child_count[parent_uuid] = (
                                        parent_child_count.get(parent_uuid, 0) + 1
                                    )

                                    # Check if this is a tool_result (parallel tool call)
                                    if msg_type == "user":
                                        message_data = data.get("message", {})
                                        content = message_data.get("content", [])
                                        if isinstance(content, list):
                                            for block in content:
                                                if isinstance(block, dict) and block.get("type") == "tool_result":
                                                    tool_result_parents.add(parent_uuid)
                                                    break

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

            # Count real forks (exclude parallel tool calls)
            real_forks = sum(
                1
                for parent, count in parent_child_count.items()
                if count > 1 and parent not in tool_result_parents
            )
            branch_count = real_forks + 1

            # Skip sessions with no messages or only summaries (can't be resumed)
            if message_count == 0 or not has_real_messages:
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
        """Convert escaped path back to real path.

        Claude Code encodes:
        - / as -
        - _ as -
        - . (leading dot) as - (so .local becomes -local, giving --)

        We try different combinations to find the actual path.
        """
        if not escaped.startswith("-"):
            return escaped.replace("-", "/")

        # Handle -- which encodes /. (hidden dirs like .local)
        # Replace -- with /. before processing
        normalized = escaped.replace("--", "-.")

        # Start with simple replacement
        path = "/" + normalized[1:].replace("-", "/")

        # If path exists, return it
        if Path(path).exists():
            return path

        # Try to find the real path by checking segments
        segments = normalized[1:].split("-")
        resolved = "/"

        i = 0
        while i < len(segments):
            seg = segments[i]

            # Try this segment alone
            candidate = resolved + seg
            if Path(candidate).exists():
                resolved = candidate + "/"
                i += 1
                continue

            # Try joining with next segments using underscore
            found = False
            for j in range(i + 2, len(segments) + 1):
                joined = "_".join(segments[i:j])
                candidate = resolved + joined
                if Path(candidate).exists():
                    resolved = candidate + "/"
                    i = j
                    found = True
                    break

            if not found:
                # No match found, just use slash
                resolved = resolved + seg + "/"
                i += 1

        return resolved.rstrip("/")

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

    def scan_conversation_paths(
        self,
        tree_cache: dict[str, MessageTree] | None = None,
        include_agents: bool = False,
    ) -> list[ConversationPath]:
        """Scan all sessions and return ConversationPaths.

        Each ConversationPath represents a linear path from root to a leaf.
        Branched sessions produce multiple paths. Compacted sessions are
        combined into single paths.

        Args:
            tree_cache: Optional pre-loaded message tree cache (session_id -> tree)
            include_agents: Whether to include agent sessions

        Returns:
            List of ConversationPaths, sorted by updated_at descending.
        """
        tree_cache = tree_cache or {}
        sessions = self.get_sessions_flat(include_agents=include_agents)

        # Build a map of session_id -> session for easy lookup
        sessions_by_id: dict[str, Session] = {s.id: s for s in sessions}

        # Track which sessions continue from other sessions via summary leafUuid
        # continuation_target[new_session_id] = old_session_id
        continuation_target: dict[str, str] = {}

        # Track the specific leafUuid each session continues from
        # continuation_leaf[new_session_id] = leaf_uuid_in_parent
        continuation_leaf: dict[str, str] = {}

        # Track which sessions are continued by other sessions
        # continued_by[old_session_id] = [new_session_ids]
        continued_by: dict[str, list[str]] = {}

        # Build a map of message_uuid -> session_id for efficient continuation lookup
        # This avoids O(n²) lookups
        uuid_to_session: dict[str, str] = {}
        for session in sessions:
            tree = tree_cache.get(session.id) or self.load_session_messages(session)
            tree_cache[session.id] = tree
            for uuid in tree.messages.keys():
                uuid_to_session[uuid] = session.id

        # Now find continuations efficiently via the map
        for session in sessions:
            leaf_uuid = self._get_continuation_leaf_uuid(session.jsonl_path)
            if leaf_uuid and leaf_uuid in uuid_to_session:
                parent_id = uuid_to_session[leaf_uuid]
                if parent_id != session.id:
                    continuation_target[session.id] = parent_id
                    continuation_leaf[session.id] = leaf_uuid
                    if parent_id not in continued_by:
                        continued_by[parent_id] = []
                    continued_by[parent_id].append(session.id)

        # Build conversation paths
        paths: list[ConversationPath] = []

        # Process only "head" sessions (not continued by another session)
        head_sessions = [s for s in sessions if s.id not in continued_by]

        # Group head sessions by their continuation point (parent_id, leaf_uuid)
        # Only sessions continuing from the same point are true siblings
        # continuation_point[head_session_id] = (parent_session_id, leaf_uuid)
        continuation_point: dict[str, tuple[str, str]] = {}
        for session in head_sessions:
            if session.id in continuation_target:
                parent_id = continuation_target[session.id]
                leaf_uuid = continuation_leaf[session.id]
                continuation_point[session.id] = (parent_id, leaf_uuid)

        # Group heads by their continuation point
        # heads_by_point[(parent_id, leaf_uuid)] = [head_session_ids]
        heads_by_point: dict[tuple[str, str], list[str]] = {}
        for head_id, point in continuation_point.items():
            if point not in heads_by_point:
                heads_by_point[point] = []
            heads_by_point[point].append(head_id)

        for session in head_sessions:
            # Build the chain of JSONL files (newest first)
            jsonl_chain: list[Path] = [session.jsonl_path]
            current_id = session.id
            while current_id in continuation_target:
                prev_id = continuation_target[current_id]
                prev_session = sessions_by_id.get(prev_id)
                if prev_session:
                    jsonl_chain.append(prev_session.jsonl_path)
                    current_id = prev_id
                else:
                    break

            # Reverse to get oldest first
            jsonl_chain = list(reversed(jsonl_chain))

            # Load the head session tree
            head_tree = tree_cache.get(session.id) or self.load_session_messages(session)
            tree_cache[session.id] = head_tree

            # Find all conversation leaves in the head session
            leaves = head_tree.get_conversation_leaves()

            if not leaves:
                # No conversation leaves, skip
                continue

            # Find cross-session siblings (other head sessions from same continuation point)
            point = continuation_point.get(session.id)
            sibling_head_ids = []
            if point:
                sibling_head_ids = [
                    h for h in heads_by_point.get(point, [])
                    if h != session.id
                ]

            # Collect leaves from sibling head sessions
            cross_session_siblings: list[str] = []
            for sibling_id in sibling_head_ids:
                sibling_session = sessions_by_id.get(sibling_id)
                if sibling_session:
                    sibling_tree = tree_cache.get(sibling_id) or self.load_session_messages(sibling_session)
                    tree_cache[sibling_id] = sibling_tree
                    for sibling_leaf in sibling_tree.get_conversation_leaves():
                        cross_session_siblings.append(sibling_leaf.uuid)

            # Create a ConversationPath for each leaf
            for leaf in leaves:
                # Check for within-session forks first
                fork_point_uuid, within_siblings = head_tree.get_fork_point_for_leaf(leaf.uuid)

                # Combine within-session and cross-session siblings
                all_siblings = list(within_siblings) + cross_session_siblings

                # If this session continues from another and has no within-session fork,
                # use the continuation point as the fork point
                if not fork_point_uuid and session.id in continuation_leaf:
                    fork_point_uuid = continuation_leaf[session.id]

                # Get the linear path for this leaf
                path_messages = head_tree.get_linear_path(leaf.uuid)

                # Get title from first user message (will be updated for branches later)
                title = self._get_title_from_path(path_messages)

                # Compute timestamps
                created_at = path_messages[0].timestamp if path_messages else session.created_at
                updated_at = leaf.timestamp

                conv_path = ConversationPath(
                    id=leaf.uuid,
                    leaf_uuid=leaf.uuid,
                    jsonl_files=jsonl_chain,
                    project_path=session.project_path,
                    project_display=session.project_display,
                    title=title,
                    created_at=created_at,
                    updated_at=updated_at,
                    message_count=len(path_messages),
                    fork_point_uuid=fork_point_uuid,
                    sibling_leaf_uuids=all_siblings,
                    last_user_message=self._get_last_user_message(path_messages),
                )
                paths.append(conv_path)

        # Also process sessions that ARE continued by others - they may have
        # their own leaves that are not on any continuation path
        continued_sessions = [s for s in sessions if s.id in continued_by]

        # Build set of leaves already handled by head sessions
        handled_leaves: set[str] = {p.leaf_uuid for p in paths}

        for session in continued_sessions:
            tree = tree_cache.get(session.id) or self.load_session_messages(session)
            tree_cache[session.id] = tree

            leaves = tree.get_conversation_leaves()
            if not leaves:
                continue

            for leaf in leaves:
                # Skip if this leaf is already handled by a head session path
                if leaf.uuid in handled_leaves:
                    continue

                # This leaf is unique to this session
                path_to_leaf = tree.get_linear_path(leaf.uuid)
                fork_point_uuid, siblings = tree.get_fork_point_for_leaf(leaf.uuid)
                title = self._get_title_from_path(path_to_leaf)
                created_at = path_to_leaf[0].timestamp if path_to_leaf else session.created_at
                updated_at = leaf.timestamp

                conv_path = ConversationPath(
                    id=leaf.uuid,
                    leaf_uuid=leaf.uuid,
                    jsonl_files=[session.jsonl_path],  # Just this session
                    project_path=session.project_path,
                    project_display=session.project_display,
                    title=title,
                    created_at=created_at,
                    updated_at=updated_at,
                    message_count=len(path_to_leaf),
                    fork_point_uuid=fork_point_uuid,
                    sibling_leaf_uuids=list(siblings),
                    last_user_message=self._get_last_user_message(path_to_leaf),
                )
                paths.append(conv_path)

        # Sort and group with proper nesting
        paths = self._sort_and_group_paths(paths, tree_cache)

        return paths

    def _get_continuation_leaf_uuid(self, jsonl_path: Path) -> str | None:
        """Check if a session starts with a summary and get its leafUuid.

        Returns the leafUuid if this session is a continuation of another,
        None otherwise.
        """
        try:
            import orjson

            with open(jsonl_path, "rb") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = orjson.loads(line)
                        if data.get("type") == "summary":
                            return data.get("leafUuid")
                        # If first message is not summary, not a continuation
                        return None
                    except Exception:
                        continue
        except Exception:
            pass
        return None

    def _get_title_from_path(
        self, messages: list[Message], fork_point_uuid: str | None = None
    ) -> str:
        """Get title from first user message in a path.

        If fork_point_uuid is provided, uses the first user message AFTER the fork
        point - this gives branched conversations distinct titles.
        """
        past_fork = fork_point_uuid is None  # If no fork, start immediately

        for msg in messages:
            # Skip until we're past the fork point
            if not past_fork:
                if msg.uuid == fork_point_uuid:
                    past_fork = True
                continue

            if msg.type == MessageType.USER and msg.text_content:
                # Skip continuation markers
                if msg.text_content.startswith("This session is being continued"):
                    continue
                return self._generate_title(msg.text_content)
            if msg.type == MessageType.SUMMARY and msg.summary_text:
                # Use beginning of summary as title if no user message
                return self._generate_title(msg.summary_text)

        # Fallback to first user message if nothing found after fork
        if fork_point_uuid:
            return self._get_title_from_path(messages, fork_point_uuid=None)
        return "Untitled"

    def _get_last_user_message(self, messages: list[Message], max_len: int = 30) -> str:
        """Get the last user message from a path, truncated."""
        for msg in reversed(messages):
            if msg.type == MessageType.USER and msg.text_content:
                text = msg.text_content.strip()
                text = " ".join(text.split())  # Normalize whitespace
                if len(text) > max_len:
                    return text[: max_len - 3] + "..."
                return text
        return ""

    def _sort_and_group_paths(
        self,
        paths: list[ConversationPath],
        tree_cache: dict[str, MessageTree] | None = None,
    ) -> list[ConversationPath]:
        """Sort paths with proper nesting based on fork relationships.

        Builds a tree where a path is nested under another if its fork point
        is on that path's message chain. Siblings at each level get tree prefixes.
        """
        if not paths:
            return paths

        tree_cache = tree_cache or {}

        # Build map from leaf_uuid to path
        by_leaf: dict[str, ConversationPath] = {p.leaf_uuid: p for p in paths}

        # Lazily load linear path UUIDs only when needed
        # (loading all paths upfront uses too much memory for long chains)
        path_linear_uuids_cache: dict[str, set[str]] = {}

        def get_path_linear_uuids(leaf_uuid: str) -> set[str]:
            """Get UUIDs on a path's linear message chain, with caching."""
            if leaf_uuid not in path_linear_uuids_cache:
                path = by_leaf[leaf_uuid]
                messages = self.load_conversation_path_messages(path, tree_cache)
                path_linear_uuids_cache[leaf_uuid] = {m.uuid for m in messages}
            return path_linear_uuids_cache[leaf_uuid]

        # Build parent-child relationships
        # A path is a child of another if:
        # 1. Its fork_point is on that path's chain, OR
        # 2. They share a fork point, but one goes through the OLDER child (main line)
        path_parent: dict[str, str | None] = {p.leaf_uuid: None for p in paths}
        path_children: dict[str, list[str]] = {p.leaf_uuid: [] for p in paths}

        # For paths sharing a fork point, determine which is main vs branch
        # by checking which child at the fork point was created first
        # fork_point -> (main_path_leaf_uuid, [branch_leaf_uuids])
        fork_main_branch: dict[str, tuple[str | None, list[str]]] = {}

        # Group paths by fork point
        paths_by_fork: dict[str, list[ConversationPath]] = {}
        for path in paths:
            if path.fork_point_uuid:
                if path.fork_point_uuid not in paths_by_fork:
                    paths_by_fork[path.fork_point_uuid] = []
                paths_by_fork[path.fork_point_uuid].append(path)

        # For each fork point with multiple paths, find main vs branches
        for fork_uuid, fork_paths in paths_by_fork.items():
            if len(fork_paths) <= 1:
                continue

            # Load the tree containing the fork point
            fork_tree: MessageTree | None = None
            for path in fork_paths:
                for jsonl_file in path.jsonl_files:
                    session_id = jsonl_file.stem
                    if session_id in tree_cache:
                        tree = tree_cache[session_id]
                    else:
                        tree = self.parser.parse_file(jsonl_file)
                        tree_cache[session_id] = tree
                    if fork_uuid in tree.messages:
                        fork_tree = tree
                        break
                if fork_tree:
                    break

            if not fork_tree:
                continue

            # Get children of fork point and their timestamps
            fork_children = fork_tree.get_children(fork_uuid)
            if len(fork_children) < 2:
                continue

            # Sort children by timestamp (oldest first = main line)
            def get_naive_ts(msg: Message) -> datetime:
                ts = msg.timestamp
                return ts.replace(tzinfo=None) if ts.tzinfo else ts

            fork_children.sort(key=get_naive_ts)
            oldest_child_uuid = fork_children[0].uuid

            # Find which path goes through the oldest child (main line)
            main_path: ConversationPath | None = None
            branch_paths: list[ConversationPath] = []

            for path in fork_paths:
                # Check if this path's LINEAR path goes through the oldest child
                path_uuids = get_path_linear_uuids(path.leaf_uuid)

                if oldest_child_uuid in path_uuids:
                    main_path = path
                else:
                    branch_paths.append(path)

            if main_path and branch_paths:
                fork_main_branch[fork_uuid] = (main_path.leaf_uuid, [b.leaf_uuid for b in branch_paths])

        # Set parent-child for fork main/branch relationships
        # Also update titles for branch paths to show first message after fork
        for fork_uuid, (main_leaf, branch_leaves) in fork_main_branch.items():
            if main_leaf:
                for branch_leaf in branch_leaves:
                    path_parent[branch_leaf] = main_leaf
                    path_children[main_leaf].append(branch_leaf)

                    # Update branch title to show first message after fork point
                    branch_path = by_leaf[branch_leaf]
                    branch_messages = self.load_conversation_path_messages(
                        branch_path, tree_cache
                    )
                    branch_path.title = self._get_title_from_path(
                        branch_messages, fork_point_uuid=fork_uuid
                    )

        # For paths with fork_point but no parent yet, check if another path
        # contains that fork_point in its chain (cross-file relationship)
        # Build chain UUIDs and session sets for ALL paths (any can be a parent)
        path_chain_uuids: dict[str, set[str]] = {}
        path_chain_sessions: dict[str, set[str]] = {}  # leaf_uuid -> set of session_ids in chain
        for path in paths:
                all_uuids: set[str] = set()
                session_ids: set[str] = set()
                for jsonl_file in path.jsonl_files:
                    session_id = jsonl_file.stem
                    session_ids.add(session_id)
                    if session_id in tree_cache:
                        tree = tree_cache[session_id]
                    else:
                        tree = self.parser.parse_file(jsonl_file)
                        tree_cache[session_id] = tree
                    all_uuids.update(tree.messages.keys())
                path_chain_uuids[path.leaf_uuid] = all_uuids
                path_chain_sessions[path.leaf_uuid] = session_ids

        # Build session set for each path
        all_path_sessions: dict[str, set[str]] = {}
        for path in paths:
            all_path_sessions[path.leaf_uuid] = {f.stem for f in path.jsonl_files}

        # Find parent for paths with fork_point but no parent
        for path in paths:
            if not path.fork_point_uuid:
                continue
            if path_parent[path.leaf_uuid] is not None:
                continue  # Already has parent

            my_sessions = all_path_sessions[path.leaf_uuid]

            # Find all paths that contain this fork point in their chain
            candidates: list[tuple[str, int]] = []  # (leaf_uuid, chain_length)
            for other_leaf, other_uuids in path_chain_uuids.items():
                if other_leaf == path.leaf_uuid:
                    continue  # Skip self
                if path.fork_point_uuid not in other_uuids:
                    continue

                other_sessions = path_chain_sessions[other_leaf]

                # For same-session nested forks, check LINEAR path containment
                if my_sessions == other_sessions:
                    # Check if fork_point is on other's LINEAR path (not just in session)
                    other_linear_uuids = get_path_linear_uuids(other_leaf)
                    if path.fork_point_uuid not in other_linear_uuids:
                        continue
                    # Skip if both paths have the same fork_point (they're siblings, not parent-child)
                    other_path = by_leaf[other_leaf]
                    if other_path.fork_point_uuid == path.fork_point_uuid:
                        continue
                    # Also ensure this path's fork_point is AFTER other's fork_point
                    # (nested fork must be downstream)
                    if other_path.fork_point_uuid:
                        my_linear_uuids = get_path_linear_uuids(path.leaf_uuid)
                        if other_path.fork_point_uuid not in my_linear_uuids:
                            continue
                    candidates.append((other_leaf, len(other_linear_uuids)))
                else:
                    # Cross-session: don't consider if child's sessions are subset
                    if my_sessions <= other_sessions:
                        continue
                    candidates.append((other_leaf, len(other_sessions)))

            if candidates:
                # Pick the candidate with the shortest chain (most direct parent)
                candidates.sort(key=lambda x: x[1])
                best_parent = candidates[0][0]
                path_parent[path.leaf_uuid] = best_parent
                path_children[best_parent].append(path.leaf_uuid)

        # Find root paths (no parent)
        root_paths = [p for p in paths if path_parent[p.leaf_uuid] is None]

        # Group root paths: sibling groups (share fork point) vs standalone
        root_sibling_groups: dict[str, list[str]] = {}  # fork_point -> [leaf_uuids]
        standalone_roots: list[str] = []

        for p in root_paths:
            if p.fork_point_uuid:
                if p.fork_point_uuid not in root_sibling_groups:
                    root_sibling_groups[p.fork_point_uuid] = []
                root_sibling_groups[p.fork_point_uuid].append(p.leaf_uuid)
            else:
                standalone_roots.append(p.leaf_uuid)

        # Promote groups of 1 back to standalone
        for fork_uuid, group in list(root_sibling_groups.items()):
            if len(group) == 1:
                standalone_roots.append(group[0])
                del root_sibling_groups[fork_uuid]

        # Handle nested fork groups: if a sibling group's paths all go through
        # another sibling group's fork point, nest it under that group's main branch
        for fork_uuid, group in list(root_sibling_groups.items()):
            # Get the linear path of one member to find ancestor fork points
            sample_leaf = group[0]
            sample_linear_uuids = get_path_linear_uuids(sample_leaf)

            # Check if this group's paths go through another group's fork point
            for other_fork, other_group in list(root_sibling_groups.items()):
                if other_fork == fork_uuid:
                    continue
                # If other_fork is on our linear path, we're nested under that group
                if other_fork in sample_linear_uuids:
                    # Find the "main" branch of the other group (longest path)
                    main_leaf = max(
                        other_group,
                        key=lambda lid: len(get_path_linear_uuids(lid)),
                    )
                    # Make all members of this group children of that main branch
                    for leaf in group:
                        path_parent[leaf] = main_leaf
                        path_children[main_leaf].append(leaf)
                    # Remove this group from root sibling groups
                    del root_sibling_groups[fork_uuid]
                    break

        # Also handle standalone roots with fork_point (single-member fork groups)
        # These may need to be nested under other fork groups
        standalone_with_fork = [
            lid for lid in standalone_roots
            if by_leaf[lid].fork_point_uuid is not None
        ]
        for leaf in standalone_with_fork:
            path = by_leaf[leaf]
            leaf_linear_uuids = get_path_linear_uuids(leaf)

            # Check if this leaf's path goes through another standalone's fork point
            for other_leaf in list(standalone_roots):
                if other_leaf == leaf:
                    continue
                other_path = by_leaf[other_leaf]
                if not other_path.fork_point_uuid:
                    continue
                # If other's fork_point is on our path, we're nested under them
                if other_path.fork_point_uuid in leaf_linear_uuids:
                    # Only nest if we're DOWNSTREAM (our fork is after theirs)
                    other_linear_uuids = get_path_linear_uuids(other_leaf)
                    if path.fork_point_uuid not in other_linear_uuids:
                        # Our fork_point is NOT on their path, so we diverged after
                        path_parent[leaf] = other_leaf
                        path_children[other_leaf].append(leaf)
                        standalone_roots.remove(leaf)

                        # Update title to show first message after parent's fork point
                        path_messages = self.load_conversation_path_messages(
                            path, tree_cache
                        )
                        path.title = self._get_title_from_path(
                            path_messages, fork_point_uuid=other_path.fork_point_uuid
                        )
                        break

        # Build the final ordered list with proper nesting
        result: list[ConversationPath] = []

        def render_tree(
            path_ids: list[str],
            depth: int,
            prefix_stack: list[str],
            is_sibling_group: bool = False,
        ) -> None:
            """Recursively render paths with tree prefixes."""
            # Sort by timestamp descending (most recent first)
            sorted_ids = sorted(
                path_ids,
                key=lambda pid: by_leaf[pid].updated_at,
                reverse=True,
            )

            for i, pid in enumerate(sorted_ids):
                path = by_leaf[pid]
                is_first = (i == 0)
                is_last = (i == len(sorted_ids) - 1)

                # Set depth
                path.depth = depth

                # Check if this path has children
                children = path_children.get(pid, [])
                has_children = len(children) > 0

                # Build prefix
                if depth == 0 and not is_sibling_group:
                    # Root level - show indicator if has children
                    if has_children:
                        path.tree_prefix = "● "  # Parent with children
                    else:
                        path.tree_prefix = ""
                else:
                    # Continuation lines from ancestors
                    base_prefix = "".join(prefix_stack)

                    # This node's connector
                    # ├─ connects upward to parent and shows more siblings below
                    # └─ connects upward to parent and is the last sibling
                    if len(sorted_ids) == 1:
                        connector = "└── "  # Single child
                    elif is_last:
                        connector = "└─ "
                    else:
                        connector = "├─ "  # First or middle

                    path.tree_prefix = base_prefix + connector

                result.append(path)
                if children:
                    # Build prefix for children
                    if depth == 0 and not is_sibling_group:
                        # Children of root get indentation to show hierarchy
                        child_prefix_stack = ["  "]  # 2-space indent under parent
                    else:
                        child_prefix_stack = prefix_stack.copy()
                        # Add continuation line if not last sibling
                        if is_last or len(sorted_ids) == 1:
                            child_prefix_stack.append("   ")
                        else:
                            child_prefix_stack.append("│  ")

                    render_tree(children, depth + 1, child_prefix_stack, False)

        # Build render groups: (timestamp, is_sibling_group, path_ids)
        render_groups: list[tuple[datetime, bool, list[str]]] = []

        # Add standalone roots
        for pid in standalone_roots:
            render_groups.append((by_leaf[pid].updated_at, False, [pid]))

        # Add sibling groups
        for group in root_sibling_groups.values():
            group_ts = max(by_leaf[pid].updated_at for pid in group)
            render_groups.append((group_ts, True, group))

        # Sort by timestamp descending
        render_groups.sort(key=lambda g: g[0], reverse=True)

        # Render all groups
        for _, is_sibling_group, path_ids in render_groups:
            render_tree(path_ids, 0, [], is_sibling_group)

        return result

    def load_conversation_path_messages(
        self,
        path: ConversationPath,
        tree_cache: dict[str, MessageTree] | None = None,
    ) -> list[Message]:
        """Load messages for a ConversationPath.

        Args:
            path: The conversation path to load
            tree_cache: Optional cache of already-loaded trees

        Returns:
            Linear list of messages from root to leaf.
        """
        if path.messages is not None:
            return path.messages

        tree_cache = tree_cache or {}

        # For now, we only support single-file paths
        # TODO: Handle multi-file paths (compacted sessions)
        if path.jsonl_files:
            # Use the last (newest) file
            newest_file = path.jsonl_files[-1]
            session_id = newest_file.stem

            # Load tree
            if session_id in tree_cache:
                tree = tree_cache[session_id]
            else:
                tree = self.parser.parse_file(newest_file)
                tree_cache[session_id] = tree

            # Get linear path
            messages = tree.get_linear_path(path.leaf_uuid)
            path.messages = messages
            return messages

        return []

    def load_conversation_path_with_tree(
        self,
        path: ConversationPath,
        tree_cache: dict[str, MessageTree] | None = None,
    ) -> tuple[list[Message], MessageTree | None]:
        """Load messages and tree for a ConversationPath.

        Returns both the linear message list and the full tree to avoid
        double-parsing when fork point detection is needed.

        Args:
            path: The conversation path to load
            tree_cache: Optional cache of already-loaded trees

        Returns:
            Tuple of (messages, tree). Tree may be None if no files.
        """
        if not path.jsonl_files:
            return [], None

        tree_cache = tree_cache or {}

        # Use the last (newest) file
        newest_file = path.jsonl_files[-1]
        session_id = newest_file.stem

        # Load tree
        if session_id in tree_cache:
            tree = tree_cache[session_id]
        else:
            tree = self.parser.parse_file(newest_file)
            tree_cache[session_id] = tree

        # Get linear path (also caches on path.messages)
        if path.messages is not None:
            messages = path.messages
        else:
            messages = tree.get_linear_path(path.leaf_uuid)
            path.messages = messages

        return messages, tree


def compute_path_hash(file_path: str) -> str:
    """Compute the hash used for file-history filenames."""
    return hashlib.sha256(file_path.encode()).hexdigest()[:16]
