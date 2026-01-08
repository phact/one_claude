"""P2P sync protocol definitions."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import orjson


class MessageType(Enum):
    """Types of sync protocol messages."""

    # Handshake
    HELLO = "hello"
    HELLO_ACK = "hello_ack"

    # Session list exchange
    SESSION_LIST = "session_list"
    SESSION_DIFF = "session_diff"

    # Session data transfer
    SESSION_REQUEST = "session_request"
    SESSION_DATA = "session_data"
    SESSION_ACK = "session_ack"

    # File history
    FILE_HISTORY_REQUEST = "file_history_request"
    FILE_HISTORY_DATA = "file_history_data"

    # Status
    ERROR = "error"
    PING = "ping"
    PONG = "pong"


@dataclass
class SyncMessage:
    """A sync protocol message."""

    type: MessageType
    peer_id: str
    payload: dict[str, Any]
    timestamp: str
    nonce: str  # For replay protection

    def to_bytes(self) -> bytes:
        """Serialize message to bytes."""
        data = {
            "type": self.type.value,
            "peer_id": self.peer_id,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
        }
        return orjson.dumps(data)

    @classmethod
    def from_bytes(cls, data: bytes) -> "SyncMessage":
        """Deserialize message from bytes."""
        parsed = orjson.loads(data)
        return cls(
            type=MessageType(parsed["type"]),
            peer_id=parsed["peer_id"],
            payload=parsed["payload"],
            timestamp=parsed["timestamp"],
            nonce=parsed["nonce"],
        )


@dataclass
class SessionHash:
    """Hash summary of a session for comparison."""

    session_id: str
    content_hash: str  # SHA256 of JSONL content
    message_count: int
    last_modified: str
    file_size: int

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SessionHash":
        return cls(**data)


@dataclass
class SyncState:
    """Sync state for tracking what's been synced."""

    session_id: str
    local_hash: str
    remote_hash: str | None
    last_synced: str | None
    status: str  # "synced", "pending_upload", "pending_download", "conflict"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SyncState":
        return cls(**data)


def compute_session_hash(jsonl_path: Path) -> SessionHash:
    """Compute hash summary of a session file."""
    content = jsonl_path.read_bytes()
    content_hash = hashlib.sha256(content).hexdigest()

    # Count messages
    message_count = 0
    for line in content.split(b"\n"):
        if line.strip():
            try:
                data = orjson.loads(line)
                if data.get("type") in ("user", "assistant"):
                    message_count += 1
            except Exception:
                pass

    stat = jsonl_path.stat()

    return SessionHash(
        session_id=jsonl_path.stem,
        content_hash=content_hash,
        message_count=message_count,
        last_modified=datetime.fromtimestamp(stat.st_mtime).isoformat(),
        file_size=stat.st_size,
    )


def compute_diff(
    local_sessions: list[SessionHash], remote_sessions: list[SessionHash]
) -> tuple[list[str], list[str], list[str]]:
    """
    Compute sync diff between local and remote session lists.

    Returns:
        (to_upload, to_download, conflicts) - lists of session IDs
    """
    local_map = {s.session_id: s for s in local_sessions}
    remote_map = {s.session_id: s for s in remote_sessions}

    to_upload = []
    to_download = []
    conflicts = []

    # Sessions only on local -> upload
    for session_id in local_map:
        if session_id not in remote_map:
            to_upload.append(session_id)

    # Sessions only on remote -> download
    for session_id in remote_map:
        if session_id not in local_map:
            to_download.append(session_id)

    # Sessions on both -> check for differences
    for session_id in local_map:
        if session_id in remote_map:
            local = local_map[session_id]
            remote = remote_map[session_id]

            if local.content_hash != remote.content_hash:
                # Different content - determine direction by timestamp
                local_time = datetime.fromisoformat(local.last_modified)
                remote_time = datetime.fromisoformat(remote.last_modified)

                if local_time > remote_time:
                    to_upload.append(session_id)
                elif remote_time > local_time:
                    to_download.append(session_id)
                else:
                    # Same timestamp but different content - conflict
                    conflicts.append(session_id)

    return to_upload, to_download, conflicts
