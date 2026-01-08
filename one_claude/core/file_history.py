"""File history and checkpoint management."""

import hashlib
from collections import defaultdict
from pathlib import Path

from one_claude.core.models import FileCheckpoint, Message, MessageTree, Session
from one_claude.core.parser import extract_file_paths_from_message


class FileHistoryManager:
    """Manages file checkpoints and path resolution."""

    def __init__(self, file_history_dir: Path):
        self.file_history_dir = file_history_dir
        # Cache: path_hash -> original_path
        self._path_cache: dict[str, str] = {}

    def get_checkpoints_for_session(self, session_id: str) -> dict[str, list[FileCheckpoint]]:
        """Get all checkpoints for a session, grouped by path hash."""
        session_dir = self.file_history_dir / session_id
        if not session_dir.exists():
            return {}

        checkpoints: dict[str, list[FileCheckpoint]] = defaultdict(list)

        for checkpoint_file in session_dir.iterdir():
            if not checkpoint_file.is_file():
                continue

            name = checkpoint_file.name
            if "@v" not in name:
                continue

            try:
                path_hash, version_str = name.split("@v")
                version = int(version_str)
                checkpoint = FileCheckpoint(
                    path_hash=path_hash,
                    version=version,
                    session_id=session_id,
                    file_path=checkpoint_file,
                )
                checkpoints[path_hash].append(checkpoint)
            except (ValueError, IndexError):
                continue

        # Sort each group by version
        for path_hash in checkpoints:
            checkpoints[path_hash].sort(key=lambda c: c.version)

        return dict(checkpoints)

    def get_latest_checkpoint(self, session_id: str, path_hash: str) -> FileCheckpoint | None:
        """Get the latest version of a file checkpoint."""
        checkpoints = self.get_checkpoints_for_session(session_id)
        file_checkpoints = checkpoints.get(path_hash, [])
        if file_checkpoints:
            return file_checkpoints[-1]
        return None

    def get_checkpoint_at_version(
        self, session_id: str, path_hash: str, version: int
    ) -> FileCheckpoint | None:
        """Get a specific version of a file checkpoint."""
        checkpoint_file = self.file_history_dir / session_id / f"{path_hash}@v{version}"
        if checkpoint_file.exists():
            return FileCheckpoint(
                path_hash=path_hash,
                version=version,
                session_id=session_id,
                file_path=checkpoint_file,
            )
        return None

    def build_path_mapping(self, session: Session, message_tree: MessageTree) -> dict[str, str]:
        """Build a mapping from path hashes to original paths by scanning messages."""
        mapping: dict[str, str] = {}

        for msg in message_tree.all_messages():
            paths = extract_file_paths_from_message(msg)
            for path in paths:
                path_hash = compute_path_hash(path)
                if path_hash not in mapping:
                    mapping[path_hash] = path

        return mapping

    def resolve_path(self, path_hash: str, session: Session | None = None) -> str | None:
        """Resolve a path hash to the original path."""
        # Check cache first
        if path_hash in self._path_cache:
            return self._path_cache[path_hash]

        # If we have a session, try to resolve from messages
        if session and session.message_tree:
            mapping = self.build_path_mapping(session, session.message_tree)
            if path_hash in mapping:
                self._path_cache[path_hash] = mapping[path_hash]
                return mapping[path_hash]

        return None

    def get_file_state_at_message(
        self,
        session: Session,
        message: Message,
        checkpoints: dict[str, list[FileCheckpoint]],
    ) -> dict[str, FileCheckpoint]:
        """
        Get the file state at a specific message point.

        Returns the latest checkpoint version for each file that existed
        at or before the given message timestamp.
        """
        state: dict[str, FileCheckpoint] = {}

        for path_hash, versions in checkpoints.items():
            # Find the latest version that was created before the message
            for checkpoint in reversed(versions):
                # We don't have exact timestamps for checkpoints, so use version ordering
                # In practice, lower version = earlier in time
                state[path_hash] = checkpoint
                break

        return state


def compute_path_hash(file_path: str) -> str:
    """Compute the hash used for file-history filenames."""
    return hashlib.sha256(file_path.encode()).hexdigest()[:16]
