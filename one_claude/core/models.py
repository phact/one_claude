"""Core data models for one_claude."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class MessageType(Enum):
    """Type of message in a session."""

    USER = "user"
    ASSISTANT = "assistant"
    SUMMARY = "summary"
    FILE_HISTORY_SNAPSHOT = "file-history-snapshot"


class UserType(Enum):
    """Type of user message."""

    EXTERNAL = "external"
    INTERNAL = "internal"


@dataclass
class ToolUse:
    """Represents a tool invocation within an assistant message."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResult:
    """Tool execution result within a user message."""

    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass
class ThinkingBlock:
    """Claude's extended thinking block."""

    content: str
    signature: str = ""


@dataclass
class Message:
    """A single message in a session."""

    uuid: str
    parent_uuid: str | None
    type: MessageType
    timestamp: datetime
    session_id: str
    cwd: str

    # Content - varies by type
    text_content: str = ""
    tool_uses: list[ToolUse] = field(default_factory=list)
    tool_result: ToolResult | None = None

    # Metadata
    git_branch: str | None = None
    version: str | None = None
    is_sidechain: bool = False

    # User-specific
    user_type: UserType | None = None

    # Assistant-specific
    model: str | None = None
    request_id: str | None = None
    thinking: ThinkingBlock | None = None

    # For summary type
    summary_text: str | None = None

    # For file-history-snapshot
    snapshot_data: dict[str, Any] | None = None

    # Raw data for debugging
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class MessageTree:
    """Tree structure of messages supporting branches via uuid/parentUuid."""

    messages: dict[str, Message]  # uuid -> Message
    root_uuids: list[str]  # Messages with parent_uuid=None
    children: dict[str, list[str]]  # parent_uuid -> child uuids

    def get_message(self, uuid: str) -> Message | None:
        """Get message by UUID."""
        return self.messages.get(uuid)

    def get_children(self, uuid: str) -> list[Message]:
        """Get child messages of a message."""
        child_uuids = self.children.get(uuid, [])
        return [self.messages[u] for u in child_uuids if u in self.messages]

    def get_linear_path(self, leaf_uuid: str) -> list[Message]:
        """Reconstruct conversation from root to leaf."""
        path = []
        current = self.messages.get(leaf_uuid)
        while current:
            path.append(current)
            if current.parent_uuid:
                current = self.messages.get(current.parent_uuid)
            else:
                break
        return list(reversed(path))

    def get_main_thread(self) -> list[Message]:
        """Get the main conversation thread (non-sidechain messages)."""
        # Start from roots, follow non-sidechain path
        messages = []
        for root_uuid in self.root_uuids:
            msg = self.messages.get(root_uuid)
            if msg and not msg.is_sidechain:
                self._collect_main_thread(msg, messages)
                break
        return messages

    def _collect_main_thread(self, msg: Message, result: list[Message]) -> None:
        """Recursively collect main thread messages."""
        result.append(msg)
        children = self.get_children(msg.uuid)
        # Prefer non-sidechain children
        for child in children:
            if not child.is_sidechain:
                self._collect_main_thread(child, result)
                return
        # If all are sidechains, take first
        if children:
            self._collect_main_thread(children[0], result)

    def all_messages(self) -> list[Message]:
        """Get all messages in chronological order."""
        def get_naive_ts(msg: Message) -> datetime:
            ts = msg.timestamp
            return ts.replace(tzinfo=None) if ts.tzinfo else ts
        return sorted(self.messages.values(), key=get_naive_ts)


@dataclass
class FileCheckpoint:
    """A file state checkpoint."""

    path_hash: str  # First 16 chars of SHA256 of absolute path
    version: int
    session_id: str
    file_path: Path  # Path to checkpoint file in file-history
    original_path: str | None = None  # Resolved original path if known

    def read_content(self) -> bytes:
        """Read the checkpoint file content."""
        return self.file_path.read_bytes()


@dataclass
class Session:
    """A Claude Code session."""

    id: str
    project_path: str  # Escaped form (e.g., -home-tato-Desktop-project)
    project_display: str  # Human-readable (e.g., /home/tato/Desktop/project)
    jsonl_path: Path

    # Derived metadata
    created_at: datetime
    updated_at: datetime
    message_count: int
    checkpoint_count: int = 0

    # Optional enrichments
    title: str | None = None
    summary: str | None = None
    embedding: list[float] | None = None
    tags: list[str] = field(default_factory=list)

    # Loaded on demand
    message_tree: MessageTree | None = None

    # File checkpoints for this session
    checkpoints: list[FileCheckpoint] = field(default_factory=list)


@dataclass
class Project:
    """A Claude Code project (collection of sessions)."""

    path: str  # Escaped form
    display_path: str  # Human-readable
    sessions: list[Session] = field(default_factory=list)

    @property
    def session_count(self) -> int:
        """Number of sessions in this project."""
        return len(self.sessions)

    @property
    def latest_session(self) -> Session | None:
        """Most recently updated session."""
        if not self.sessions:
            return None
        return max(self.sessions, key=lambda s: s.updated_at)
