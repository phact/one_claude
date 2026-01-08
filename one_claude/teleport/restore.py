"""File restoration from checkpoints."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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
        """Get list of points that can be restored."""
        tree = self.scanner.load_session_messages(session)
        checkpoints = self.file_history.get_checkpoints_for_session(session.id)

        if not checkpoints:
            return []

        points = []
        seen_messages = set()

        # Walk through messages and find ones with file operations
        for msg in tree.all_messages():
            if msg.uuid in seen_messages:
                continue
            seen_messages.add(msg.uuid)

            # Look for file-history-snapshot or assistant messages with file edits
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

        # Sort by timestamp descending
        points.sort(key=lambda p: p.timestamp, reverse=True)
        return points[:20]  # Limit to 20 restore points

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

    async def restore_to_sandbox(
        self,
        session: Session,
        message_uuid: str | None = None,
    ) -> TeleportSession:
        """
        Restore files to sandbox at specified point.

        Args:
            session: Session to restore
            message_uuid: Message to restore to (latest if None)
        """
        # Create sandbox (auto-detects microsandbox vs local mode)
        sandbox = TeleportSandbox(session.id, session.project_display)

        await sandbox.start()

        # Get file checkpoints
        checkpoints = self.file_history.get_checkpoints_for_session(session.id)
        path_mapping = self.build_path_mapping(session)

        files_restored: dict[str, str] = {}

        # Restore each file (latest version for now)
        for path_hash, versions in checkpoints.items():
            if not versions:
                continue

            # Get latest version
            latest = versions[-1]

            # Resolve original path
            original_path = path_mapping.get(path_hash)
            if not original_path:
                # Can't restore without knowing original path
                continue

            # Read checkpoint content
            try:
                content = latest.read_content()
            except Exception:
                continue

            # Construct sandbox path
            # Strip leading / and prepend workspace
            relative_path = original_path.lstrip("/")
            sandbox_path = f"{sandbox.working_dir}/{relative_path}"

            # Write to sandbox
            await sandbox.write_file(sandbox_path, content)
            files_restored[original_path] = path_hash

        import uuid

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
