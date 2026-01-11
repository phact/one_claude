"""JSONL parsing for Claude Code session files."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import simdjson

# Cache UTC timezone for faster timestamp parsing
_UTC = timezone.utc

from one_claude.core.models import (
    Message,
    MessageTree,
    MessageType,
    ThinkingBlock,
    ToolResult,
    ToolUse,
    UserType,
)


# simdjson type helpers
def _is_array(obj: Any) -> bool:
    return type(obj).__name__ == "Array"


def _is_object(obj: Any) -> bool:
    return type(obj).__name__ == "Object"


def _to_str(val: Any) -> str | None:
    """Convert simdjson value to Python str."""
    if val is None:
        return None
    return str(val)


def _to_dict(obj: Any) -> dict:
    """Convert simdjson Object to Python dict."""
    if obj is None:
        return {}
    if _is_object(obj):
        return obj.as_dict()
    if isinstance(obj, dict):
        return obj
    return {}


def _to_list(obj: Any) -> list:
    """Convert simdjson Array to Python list."""
    if obj is None:
        return []
    if _is_array(obj):
        return obj.as_list()
    if isinstance(obj, list):
        return obj
    return []


class SessionParser:
    """Parses Claude Code session JSONL files."""

    def __init__(self):
        self._parser = simdjson.Parser()

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
                    doc = self._parser.parse(line)
                    msg = self._parse_record_direct(doc)
                    del doc  # Release before next parse
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
                except (ValueError, KeyError):
                    continue

        # Link orphaned chains via summaries
        self._link_orphaned_chains(messages, root_uuids, children, summaries)

        # Fix checkpoint timestamps (inherit from parent message)
        self._fix_checkpoint_timestamps(messages)

        return MessageTree(messages=messages, root_uuids=root_uuids, children=children)

    def _parse_record_direct(self, doc: Any) -> Message | None:
        """Parse a simdjson doc directly into a Message."""
        msg_type_str = _to_str(doc.get("type"))
        if not msg_type_str:
            return None

        try:
            msg_type = MessageType(msg_type_str)
        except ValueError:
            return None

        uuid = _to_str(doc.get("uuid")) or ""

        # Summary messages use leafUuid instead of uuid
        if not uuid and msg_type == MessageType.SUMMARY:
            leaf_uuid = _to_str(doc.get("leafUuid")) or ""
            if leaf_uuid:
                uuid = f"summary-{leaf_uuid}"

        # File-history-snapshot messages use messageId instead of uuid
        if not uuid and msg_type == MessageType.FILE_HISTORY_SNAPSHOT:
            message_id = _to_str(doc.get("messageId")) or ""
            if message_id:
                uuid = f"checkpoint-{message_id}"

        if not uuid:
            return None

        # Parse timestamp - optimize for Z-suffix (most common)
        timestamp_str = _to_str(doc.get("timestamp")) or ""
        try:
            if timestamp_str and timestamp_str[-1] == "Z":
                # Parse without Z, then attach UTC timezone directly
                timestamp = datetime.fromisoformat(timestamp_str[:-1]).replace(tzinfo=_UTC)
            elif timestamp_str:
                timestamp = datetime.fromisoformat(timestamp_str)
            else:
                timestamp = datetime.now(_UTC)
        except (ValueError, AttributeError):
            timestamp = datetime.now(_UTC)

        msg = Message(
            uuid=uuid,
            parent_uuid=_to_str(doc.get("parentUuid")),
            type=msg_type,
            timestamp=timestamp,
            session_id=_to_str(doc.get("sessionId")) or "",
            cwd=_to_str(doc.get("cwd")) or "",
            git_branch=_to_str(doc.get("gitBranch")),
            version=_to_str(doc.get("version")),
            is_sidechain=bool(doc.get("isSidechain")),
        )

        # Parse type-specific content
        message_data = doc.get("message")
        if msg_type == MessageType.USER:
            self._parse_user_direct(msg, doc, message_data)
        elif msg_type == MessageType.ASSISTANT:
            self._parse_assistant_direct(msg, doc, message_data)
        elif msg_type == MessageType.SUMMARY:
            self._parse_summary_direct(msg, doc, message_data)
        elif msg_type == MessageType.FILE_HISTORY_SNAPSHOT:
            self._parse_snapshot_direct(msg, doc)
        elif msg_type == MessageType.SYSTEM:
            self._parse_system_direct(msg, doc)

        return msg

    def _parse_user_direct(self, msg: Message, doc: Any, message_data: Any) -> None:
        """Parse user message content from simdjson."""
        user_type_str = _to_str(doc.get("userType"))
        if user_type_str:
            try:
                msg.user_type = UserType(user_type_str)
            except ValueError:
                pass

        if not message_data:
            return

        content = message_data.get("content")

        if isinstance(content, str):
            msg.text_content = content
        elif _is_array(content):
            text_parts = []
            for block in content:
                if _is_object(block):
                    block_type = _to_str(block.get("type")) or ""
                    if block_type == "text":
                        text_parts.append(_to_str(block.get("text")) or "")
                    elif block_type == "tool_result":
                        block_content = block.get("content")
                        msg.tool_result = ToolResult(
                            tool_use_id=_to_str(block.get("tool_use_id")) or "",
                            content=self._extract_tool_result_direct(block_content),
                            is_error=bool(block.get("is_error")),
                        )
                elif isinstance(block, str):
                    text_parts.append(block)
            msg.text_content = "\n".join(text_parts)

    def _extract_tool_result_direct(self, content: Any) -> str:
        """Extract text content from tool result."""
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if _is_array(content):
            parts = []
            for item in content:
                if _is_object(item) and _to_str(item.get("type")) == "text":
                    parts.append(_to_str(item.get("text")) or "")
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
        return str(content)

    def _parse_assistant_direct(self, msg: Message, doc: Any, message_data: Any) -> None:
        """Parse assistant message content from simdjson."""
        msg.model = _to_str(doc.get("model"))
        msg.request_id = _to_str(doc.get("requestId"))

        if not message_data:
            return

        content = message_data.get("content")

        if isinstance(content, str):
            msg.text_content = content
        elif _is_array(content):
            text_parts = []
            for block in content:
                if _is_object(block):
                    block_type = _to_str(block.get("type")) or ""
                    if block_type == "text":
                        text_parts.append(_to_str(block.get("text")) or "")
                    elif block_type == "tool_use":
                        tool_input = block.get("input")
                        tool_use = ToolUse(
                            id=_to_str(block.get("id")) or "",
                            name=_to_str(block.get("name")) or "",
                            input=_to_dict(tool_input),
                        )
                        msg.tool_uses.append(tool_use)
                    elif block_type == "thinking":
                        msg.thinking = ThinkingBlock(
                            content=_to_str(block.get("thinking")) or "",
                            signature=_to_str(block.get("signature")) or "",
                        )
                elif isinstance(block, str):
                    text_parts.append(block)
            msg.text_content = "\n".join(text_parts)

    def _parse_summary_direct(self, msg: Message, doc: Any, message_data: Any) -> None:
        """Parse summary message content from simdjson."""
        msg.summary_text = _to_str(doc.get("summary")) or ""
        if not msg.summary_text and message_data:
            content = message_data.get("content")
            if isinstance(content, str):
                msg.summary_text = content

        # Set parent_uuid to leafUuid
        leaf_uuid = _to_str(doc.get("leafUuid"))
        if leaf_uuid:
            msg.parent_uuid = leaf_uuid

    def _parse_snapshot_direct(self, msg: Message, doc: Any) -> None:
        """Parse file-history-snapshot message from simdjson."""
        snapshot = doc.get("snapshot")
        msg.snapshot_data = _to_dict(snapshot) if snapshot else doc.as_dict()

        # Set parent_uuid to messageId
        message_id = _to_str(doc.get("messageId"))
        if message_id:
            msg.parent_uuid = message_id

    def _parse_system_direct(self, msg: Message, doc: Any) -> None:
        """Parse system message from simdjson."""
        msg.system_subtype = _to_str(doc.get("subtype"))

        # Capture relevant system data
        msg.system_data = {
            "hookCount": doc.get("hookCount"),
            "hookInfos": _to_list(doc.get("hookInfos")),
            "hookErrors": _to_list(doc.get("hookErrors")),
            "preventedContinuation": doc.get("preventedContinuation"),
            "stopReason": _to_str(doc.get("stopReason")),
            "hasOutput": doc.get("hasOutput"),
        }

    def _fix_checkpoint_timestamps(self, messages: dict[str, Message]) -> None:
        """Give checkpoints the timestamp of their parent message."""
        for msg in messages.values():
            if msg.type == MessageType.FILE_HISTORY_SNAPSHOT and msg.parent_uuid:
                parent = messages.get(msg.parent_uuid)
                if parent:
                    msg.timestamp = parent.timestamp

    def _link_orphaned_chains(
        self,
        messages: dict[str, Message],
        root_uuids: list[str],
        children: dict[str, list[str]],
        summaries: list[Message],
    ) -> None:
        """Link orphaned message chains via summaries.

        This handles two cases:
        1. Orphans: messages whose parent_uuid points to a non-existent message
        2. SYSTEM roots: compact_boundary, stop_hook_summary, etc. that start
           new chains after compaction but should be linked to prior summaries

        Summaries don't have their own timestamp - we use the timestamp of
        the message they point to (via parent_uuid/leafUuid).
        """
        if not summaries:
            return

        def get_naive_ts(ts: datetime) -> datetime:
            return ts.replace(tzinfo=None) if ts.tzinfo else ts

        # Build summary info with effective timestamps from their leaf messages
        # summary.parent_uuid points to the leafUuid (last message before summary)
        summary_info: list[tuple[Message, datetime]] = []
        for summary in summaries:
            if summary.parent_uuid and summary.parent_uuid in messages:
                leaf_msg = messages[summary.parent_uuid]
                effective_ts = get_naive_ts(leaf_msg.timestamp)
            else:
                # Fallback: use summary's own timestamp (may be now())
                effective_ts = get_naive_ts(summary.timestamp)
            summary_info.append((summary, effective_ts))

        # Sort by effective timestamp
        summary_info.sort(key=lambda x: x[1])

        # Find messages that need linking:
        # 1. Orphans (parent exists but not in messages) - EXCEPT summaries
        #    Summaries intentionally point to leafUuid in parent sessions
        # 2. SYSTEM roots (no parent, but should link to summary)
        to_link: list[str] = []

        for uuid, msg in messages.items():
            if msg.parent_uuid and msg.parent_uuid not in messages:
                # Orphan - parent doesn't exist
                # Skip summaries - they reference leafUuid in parent sessions
                if msg.type != MessageType.SUMMARY:
                    to_link.append(uuid)
            elif msg.parent_uuid is None and msg.type == MessageType.SYSTEM:
                # SYSTEM root - should link to prior summary
                # (compact_boundary, stop_hook_summary, etc.)
                to_link.append(uuid)

        if not to_link:
            return

        for msg_uuid in to_link:
            msg = messages[msg_uuid]
            msg_ts = get_naive_ts(msg.timestamp)

            # Find the most recent summary before this message
            best_summary = None
            for summary, summary_ts in summary_info:
                if summary_ts <= msg_ts:
                    best_summary = summary

            if best_summary:
                msg.parent_uuid = best_summary.uuid
                if best_summary.uuid not in children:
                    children[best_summary.uuid] = []
                children[best_summary.uuid].append(msg_uuid)

                if msg_uuid in root_uuids:
                    root_uuids.remove(msg_uuid)

    # Keep old method for compatibility
    def parse_record(self, data: dict[str, Any]) -> Message | None:
        """Parse a dict record into a Message (legacy, for orjson compatibility)."""
        msg_type_str = data.get("type")
        if not msg_type_str:
            return None

        try:
            msg_type = MessageType(msg_type_str)
        except ValueError:
            return None

        uuid = data.get("uuid", "")

        if not uuid and msg_type == MessageType.SUMMARY:
            leaf_uuid = data.get("leafUuid", "")
            if leaf_uuid:
                uuid = f"summary-{leaf_uuid}"

        if not uuid and msg_type == MessageType.FILE_HISTORY_SNAPSHOT:
            message_id = data.get("messageId", "")
            if message_id:
                uuid = f"checkpoint-{message_id}"

        if not uuid:
            return None

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

        message_data = data.get("message", {})
        if msg_type == MessageType.USER:
            self._parse_user_legacy(msg, data, message_data)
        elif msg_type == MessageType.ASSISTANT:
            self._parse_assistant_legacy(msg, data, message_data)
        elif msg_type == MessageType.SUMMARY:
            self._parse_summary_legacy(msg, data, message_data)
        elif msg_type == MessageType.FILE_HISTORY_SNAPSHOT:
            self._parse_snapshot_legacy(msg, data)

        return msg

    def _parse_user_legacy(self, msg: Message, data: dict, message_data: dict) -> None:
        user_type_str = data.get("userType")
        if user_type_str:
            try:
                msg.user_type = UserType(user_type_str)
            except ValueError:
                pass

        content = message_data.get("content", "")
        if isinstance(content, str):
            msg.text_content = content
        elif isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type", "")
                    if block_type == "text":
                        text_parts.append(block.get("text", ""))
                    elif block_type == "tool_result":
                        msg.tool_result = ToolResult(
                            tool_use_id=block.get("tool_use_id", ""),
                            content=self._extract_tool_result_legacy(block.get("content", "")),
                            is_error=block.get("is_error", False),
                        )
                elif isinstance(block, str):
                    text_parts.append(block)
            msg.text_content = "\n".join(text_parts)

    def _extract_tool_result_legacy(self, content: Any) -> str:
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

    def _parse_assistant_legacy(self, msg: Message, data: dict, message_data: dict) -> None:
        msg.model = data.get("model")
        msg.request_id = data.get("requestId")

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

    def _parse_summary_legacy(self, msg: Message, data: dict, message_data: dict) -> None:
        msg.summary_text = data.get("summary", "")
        if not msg.summary_text:
            content = message_data.get("content", "")
            if isinstance(content, str):
                msg.summary_text = content
        leaf_uuid = data.get("leafUuid")
        if leaf_uuid:
            msg.parent_uuid = leaf_uuid

    def _parse_snapshot_legacy(self, msg: Message, data: dict) -> None:
        msg.snapshot_data = data.get("snapshot", data)
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
