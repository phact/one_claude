"""JSONL parsing for Claude Code session files."""

from datetime import datetime
from pathlib import Path
from typing import Any

import orjson

from one_claude.core.models import (
    Message,
    MessageTree,
    MessageType,
    ThinkingBlock,
    ToolResult,
    ToolUse,
    UserType,
)


class SessionParser:
    """Parses Claude Code session JSONL files."""

    def parse_file(self, path: Path) -> MessageTree:
        """Parse a JSONL file into a MessageTree."""
        messages: dict[str, Message] = {}
        root_uuids: list[str] = []
        children: dict[str, list[str]] = {}
        summaries: list[Message] = []  # Summaries to insert later

        with open(path, "rb") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = orjson.loads(line)
                    msg = self.parse_record(data)
                    if msg:
                        messages[msg.uuid] = msg
                        if msg.parent_uuid is None:
                            root_uuids.append(msg.uuid)
                        else:
                            if msg.parent_uuid not in children:
                                children[msg.parent_uuid] = []
                            children[msg.parent_uuid].append(msg.uuid)

                        # Track summaries for chain linking
                        if msg.type == MessageType.SUMMARY:
                            summaries.append(msg)
                except orjson.JSONDecodeError:
                    continue

        # Link orphaned chains via summaries
        self._link_orphaned_chains(messages, root_uuids, children, summaries)

        # Fix checkpoint timestamps (inherit from parent message)
        self._fix_checkpoint_timestamps(messages)

        return MessageTree(messages=messages, root_uuids=root_uuids, children=children)

    def _fix_checkpoint_timestamps(self, messages: dict[str, Message]) -> None:
        """Give checkpoints the timestamp of their parent message."""
        for msg in messages.values():
            if msg.type == MessageType.FILE_HISTORY_SNAPSHOT and msg.parent_uuid:
                parent = messages.get(msg.parent_uuid)
                if parent:
                    # Set checkpoint timestamp slightly after parent
                    msg.timestamp = parent.timestamp

    def _link_orphaned_chains(
        self,
        messages: dict[str, Message],
        root_uuids: list[str],
        children: dict[str, list[str]],
        summaries: list[Message],
    ) -> None:
        """Link orphaned message chains via summaries.

        When Claude compacts a conversation, it:
        1. Removes old messages
        2. Creates summary messages with leafUuid pointing to the last summarized message
        3. New messages reference deleted parents

        This method connects orphaned chains to the appropriate summary.
        """
        if not summaries:
            return

        # Find orphans: messages whose parent doesn't exist
        orphan_uuids = []
        for uuid, msg in messages.items():
            if msg.parent_uuid and msg.parent_uuid not in messages:
                orphan_uuids.append(uuid)

        if not orphan_uuids:
            return

        # Sort summaries by timestamp (most recent last)
        def get_naive_ts(msg: Message) -> datetime:
            ts = msg.timestamp
            return ts.replace(tzinfo=None) if ts.tzinfo else ts
        summaries.sort(key=get_naive_ts)

        # For each orphan, find the most recent summary before it
        for orphan_uuid in orphan_uuids:
            orphan = messages[orphan_uuid]
            orphan_ts = orphan.timestamp.replace(tzinfo=None) if orphan.timestamp.tzinfo else orphan.timestamp

            # Find the best summary to link to
            best_summary = None
            for summary in summaries:
                summary_ts = summary.timestamp.replace(tzinfo=None) if summary.timestamp.tzinfo else summary.timestamp
                if summary_ts <= orphan_ts:
                    best_summary = summary

            if best_summary:
                # Link orphan to summary
                orphan.parent_uuid = best_summary.uuid
                if best_summary.uuid not in children:
                    children[best_summary.uuid] = []
                children[best_summary.uuid].append(orphan_uuid)

                # Remove from root_uuids if it was there
                if orphan_uuid in root_uuids:
                    root_uuids.remove(orphan_uuid)

    def parse_record(self, data: dict[str, Any]) -> Message | None:
        """Parse a single JSONL record into a Message."""
        msg_type_str = data.get("type")
        if not msg_type_str:
            return None

        try:
            msg_type = MessageType(msg_type_str)
        except ValueError:
            return None

        uuid = data.get("uuid", "")

        # Summary messages use leafUuid instead of uuid
        # Generate a synthetic uuid for summaries
        if not uuid and msg_type == MessageType.SUMMARY:
            leaf_uuid = data.get("leafUuid", "")
            if leaf_uuid:
                uuid = f"summary-{leaf_uuid}"  # Synthetic UUID

        # File-history-snapshot messages use messageId instead of uuid
        if not uuid and msg_type == MessageType.FILE_HISTORY_SNAPSHOT:
            message_id = data.get("messageId", "")
            if message_id:
                uuid = f"checkpoint-{message_id}"  # Synthetic UUID

        if not uuid:
            return None

        # Parse timestamp
        timestamp_str = data.get("timestamp", "")
        try:
            timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            timestamp = datetime.now()

        msg = Message(
            uuid=uuid,
            parent_uuid=data.get("parentUuid"),
            type=msg_type,
            timestamp=timestamp,
            session_id=data.get("sessionId", ""),
            cwd=data.get("cwd", ""),
            git_branch=data.get("gitBranch"),
            version=data.get("version"),
            is_sidechain=data.get("isSidechain", False),
            raw=data,
        )

        # Parse type-specific content
        if msg_type == MessageType.USER:
            self._parse_user_message(msg, data)
        elif msg_type == MessageType.ASSISTANT:
            self._parse_assistant_message(msg, data)
        elif msg_type == MessageType.SUMMARY:
            self._parse_summary_message(msg, data)
        elif msg_type == MessageType.FILE_HISTORY_SNAPSHOT:
            self._parse_snapshot_message(msg, data)

        return msg

    def _parse_user_message(self, msg: Message, data: dict[str, Any]) -> None:
        """Parse user message content."""
        user_type_str = data.get("userType")
        if user_type_str:
            try:
                msg.user_type = UserType(user_type_str)
            except ValueError:
                pass

        message_data = data.get("message", {})
        content = message_data.get("content", "")

        if isinstance(content, str):
            msg.text_content = content
        elif isinstance(content, list):
            # Handle content blocks (text, tool_result, etc.)
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type", "")
                    if block_type == "text":
                        text_parts.append(block.get("text", ""))
                    elif block_type == "tool_result":
                        msg.tool_result = ToolResult(
                            tool_use_id=block.get("tool_use_id", ""),
                            content=self._extract_tool_result_content(block.get("content", "")),
                            is_error=block.get("is_error", False),
                        )
                elif isinstance(block, str):
                    text_parts.append(block)
            msg.text_content = "\n".join(text_parts)

    def _extract_tool_result_content(self, content: Any) -> str:
        """Extract text content from tool result."""
        if isinstance(content, str):
            return content
        elif isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
        return str(content)

    def _parse_assistant_message(self, msg: Message, data: dict[str, Any]) -> None:
        """Parse assistant message content."""
        msg.model = data.get("model")
        msg.request_id = data.get("requestId")

        message_data = data.get("message", {})
        content = message_data.get("content", [])

        if isinstance(content, str):
            msg.text_content = content
        elif isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type", "")
                    if block_type == "text":
                        text_parts.append(block.get("text", ""))
                    elif block_type == "tool_use":
                        tool_use = ToolUse(
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            input=block.get("input", {}),
                        )
                        msg.tool_uses.append(tool_use)
                    elif block_type == "thinking":
                        msg.thinking = ThinkingBlock(
                            content=block.get("thinking", ""),
                            signature=block.get("signature", ""),
                        )
                elif isinstance(block, str):
                    text_parts.append(block)
            msg.text_content = "\n".join(text_parts)

    def _parse_summary_message(self, msg: Message, data: dict[str, Any]) -> None:
        """Parse summary message content."""
        msg.summary_text = data.get("summary", "")
        if not msg.summary_text:
            message_data = data.get("message", {})
            content = message_data.get("content", "")
            if isinstance(content, str):
                msg.summary_text = content

        # Set parent_uuid to leafUuid - this links summary to the chain it summarizes
        leaf_uuid = data.get("leafUuid")
        if leaf_uuid:
            msg.parent_uuid = leaf_uuid

    def _parse_snapshot_message(self, msg: Message, data: dict[str, Any]) -> None:
        """Parse file-history-snapshot message."""
        msg.snapshot_data = data.get("snapshot", data)

        # Set parent_uuid to messageId - links checkpoint to triggering message
        message_id = data.get("messageId")
        if message_id:
            msg.parent_uuid = message_id


def extract_file_paths_from_message(msg: Message) -> list[str]:
    """Extract file paths mentioned in tool uses within a message."""
    paths = []
    for tool_use in msg.tool_uses:
        if tool_use.name in ("Read", "Write", "Edit", "Glob", "Grep"):
            file_path = tool_use.input.get("file_path") or tool_use.input.get("path")
            if file_path:
                paths.append(file_path)
    return paths
