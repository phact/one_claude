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
    SYSTEM = "system"  # stop_hook_summary, etc.
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


@dataclass(slots=True)
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

    # For system type
    system_subtype: str | None = None  # e.g., "stop_hook_summary"
    system_data: dict[str, Any] | None = None  # Hook info, etc.

    # Raw data for debugging
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TreeNode:
    """A message with tree display metadata."""

    message: Message
    depth: int  # Nesting depth (0 = root)
    branch_index: int  # Which branch (0 = first/main, 1+ = forks)
    is_fork_point: bool  # True if this message has multiple children
    prefix: str  # Visual prefix for tree display (e.g., "│  ", "├─ ")


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

    def get_tree_nodes(self) -> list[TreeNode]:
        """Get all messages as tree nodes with branch visualization info.

        Returns messages in depth-first order with visual prefixes for
        rendering a tree structure (like git log --graph).
        """
        result: list[TreeNode] = []
        conversation_types = (MessageType.USER, MessageType.ASSISTANT)

        def get_naive_ts(msg: Message) -> datetime:
            ts = msg.timestamp
            return ts.replace(tzinfo=None) if ts.tzinfo else ts

        def traverse(
            uuid: str,
            depth: int,
            prefix_stack: list[str],
            branch_index: int,
        ) -> None:
            msg = self.messages.get(uuid)
            if not msg:
                return

            children = self.get_children(uuid)
            # Sort children by timestamp
            children.sort(key=get_naive_ts)

            # Only count conversation children for fork detection
            # (ignore file-history-snapshot and summary which create fake forks)
            conversation_children = [c for c in children if c.type in conversation_types]

            # Check if this is a real fork or just parallel tool calls
            is_fork = False
            if len(conversation_children) > 1:
                # Parallel tool calls have tool_result children - not a real fork
                has_tool_result = any(
                    c.type == MessageType.USER and c.tool_result is not None
                    for c in conversation_children
                )
                is_fork = not has_tool_result

            # Build the visual prefix
            if depth == 0:
                prefix = ""
            else:
                prefix = "".join(prefix_stack)

            result.append(TreeNode(
                message=msg,
                depth=depth,
                branch_index=branch_index,
                is_fork_point=is_fork,
                prefix=prefix,
            ))

            # Traverse children
            for i, child in enumerate(children):
                is_last = (i == len(children) - 1)

                if depth == 0:
                    # First level - no prefix yet
                    new_prefix_stack = []
                else:
                    new_prefix_stack = prefix_stack.copy()

                if is_fork:
                    # At a fork point
                    if is_last:
                        new_prefix_stack.append("└─ ")
                    else:
                        new_prefix_stack.append("├─ ")
                elif len(new_prefix_stack) > 0:
                    # Continue the line
                    # Replace the last prefix element for continuation
                    if new_prefix_stack and new_prefix_stack[-1] == "├─ ":
                        new_prefix_stack[-1] = "│  "
                    elif new_prefix_stack and new_prefix_stack[-1] == "└─ ":
                        new_prefix_stack[-1] = "   "

                traverse(child.uuid, depth + 1, new_prefix_stack, i)

        # Start from root messages
        root_messages = [self.messages[u] for u in self.root_uuids if u in self.messages]
        root_messages.sort(key=get_naive_ts)

        for i, root in enumerate(root_messages):
            traverse(root.uuid, 0, [], i)

        return result

    def get_leaves(self) -> list[Message]:
        """Get all leaf messages (messages with no children)."""
        leaves = []
        for msg in self.messages.values():
            if msg.uuid not in self.children or not self.children[msg.uuid]:
                leaves.append(msg)
        return leaves

    def get_conversation_leaves(self) -> list[Message]:
        """Get leaf messages that are actual conversation endpoints.

        Only counts ASSISTANT messages as leaves since conversations
        naturally end with Claude's response. User messages without
        children are usually interruptions (hooks, user cancels), not
        intentional branch endpoints.

        Excludes:
        - USER messages (interruptions, not real endpoints)
        - file-history-snapshot and summary messages (metadata, not conversation)
        - ASSISTANT messages that lead to more conversation through SYSTEM messages
        """
        leaves = []
        for msg in self.messages.values():
            # Only assistant messages can be real conversation leaves
            if msg.type != MessageType.ASSISTANT:
                continue
            # Check if this message leads to any conversation (directly or through SYSTEM)
            if not self._has_conversation_continuation(msg.uuid):
                leaves.append(msg)
        return leaves

    def _has_conversation_continuation(self, uuid: str, visited: set[str] | None = None) -> bool:
        """Check if a message leads to more conversation.

        Traces through SYSTEM messages to find USER/ASSISTANT continuations.
        """
        if visited is None:
            visited = set()
        if uuid in visited:
            return False
        visited.add(uuid)

        child_uuids = self.children.get(uuid, [])
        for child_uuid in child_uuids:
            child = self.messages.get(child_uuid)
            if not child:
                continue
            # Direct conversation child
            if child.type in (MessageType.USER, MessageType.ASSISTANT):
                return True
            # SYSTEM message - trace through it
            if child.type == MessageType.SYSTEM:
                if self._has_conversation_continuation(child_uuid, visited):
                    return True
        return False

    def get_branch_count(self) -> int:
        """Count the number of distinct conversation branches.

        Counts real fork points (rewind branches), not parallel tool calls.
        A fork with tool_result children is parallel execution, not a real branch.
        """
        conversation_types = (MessageType.USER, MessageType.ASSISTANT)
        real_forks = 0

        for msg in self.messages.values():
            children = self.get_children(msg.uuid)
            conv_children = [c for c in children if c.type in conversation_types]

            if len(conv_children) > 1:
                # Check if this is parallel tool calls (has tool_result children)
                has_tool_result = any(
                    c.type == MessageType.USER and c.tool_result is not None
                    for c in conv_children
                )
                if not has_tool_result:
                    real_forks += 1

        # 0 forks = 1 branch, 1 fork = 2 branches, etc.
        return real_forks + 1

    def get_fork_point_for_leaf(self, leaf_uuid: str) -> tuple[str | None, list[str]]:
        """Find the fork point for a leaf and its sibling leaves.

        Returns:
            Tuple of (fork_point_uuid, sibling_leaf_uuids).
            fork_point_uuid is None if this leaf has no fork point (single path).
            sibling_leaf_uuids are the other leaves that share the same fork point.
        """
        conversation_types = (MessageType.USER, MessageType.ASSISTANT)

        # Walk from leaf to root, find first fork point
        path = self.get_linear_path(leaf_uuid)
        if not path:
            return None, []

        for msg in reversed(path):
            children = self.get_children(msg.uuid)
            conv_children = [c for c in children if c.type in conversation_types]

            if len(conv_children) > 1:
                # Check if this is a real fork (not parallel tool calls)
                has_tool_result = any(
                    c.type == MessageType.USER and c.tool_result is not None
                    for c in conv_children
                )
                if not has_tool_result:
                    # This is a fork point - find sibling leaves
                    all_leaves = self.get_conversation_leaves()
                    siblings = []
                    for leaf in all_leaves:
                        if leaf.uuid != leaf_uuid:
                            # Check if this leaf passes through this fork point
                            leaf_path = self.get_linear_path(leaf.uuid)
                            if any(m.uuid == msg.uuid for m in leaf_path):
                                siblings.append(leaf.uuid)
                    return msg.uuid, siblings

        return None, []

    def is_fork_point(self, uuid: str) -> bool:
        """Check if a message is a fork point (has multiple conversation branches)."""
        conversation_types = (MessageType.USER, MessageType.ASSISTANT)
        children = self.get_children(uuid)
        conv_children = [c for c in children if c.type in conversation_types]

        if len(conv_children) <= 1:
            return False

        # Check if parallel tool calls
        has_tool_result = any(
            c.type == MessageType.USER and c.tool_result is not None
            for c in conv_children
        )
        return not has_tool_result


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

    # Agent/subagent tracking
    is_agent: bool = False  # True if this is a subagent session (agent-XXXX)
    parent_session_id: str | None = None  # Parent session ID if this is an agent
    child_agent_ids: list[str] = field(default_factory=list)  # Agent session IDs spawned from this session


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


@dataclass
class ConversationPath:
    """A linear conversation path from root to leaf.

    This represents a single linear conversation thread. Unlike Session
    (which maps 1:1 to a JSONL file), a ConversationPath:
    - May span multiple JSONL files (if conversation was compacted)
    - Represents exactly one branch path (no tree structure)

    Multiple ConversationPaths can share the same underlying JSONL file
    if that file contains branches (from /rewind).
    """

    id: str  # Unique ID (typically the leaf message UUID)
    leaf_uuid: str  # The leaf message this path ends at

    # Path may span multiple JSONL files (compaction creates chains)
    jsonl_files: list[Path] = field(default_factory=list)  # Ordered oldest to newest

    # Metadata
    project_path: str = ""  # Escaped form
    project_display: str = ""  # Human-readable
    title: str = ""  # From first user message
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    message_count: int = 0

    # Branch info for tree display in list view
    fork_point_uuid: str | None = None  # Where this branched from another path
    sibling_leaf_uuids: list[str] = field(default_factory=list)  # Other branches from same fork
    depth: int = 0  # Nesting level in tree (0 = root conversation)
    tree_prefix: str = ""  # Visual prefix (├── └── │)

    # Last user message (for display context)
    last_user_message: str = ""

    # Loaded on demand
    messages: list[Message] | None = None  # Linear list from root to leaf

    def get_fork_siblings(self) -> list[str]:
        """Get UUIDs of other leaves that share the same fork point."""
        return self.sibling_leaf_uuids
