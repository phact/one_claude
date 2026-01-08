"""Peer connection management for P2P sync."""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from one_claude.core.scanner import ClaudeScanner
from one_claude.sync.crypto import CryptoManager, MockCryptoManager
from one_claude.sync.protocol import (
    MessageType,
    SessionHash,
    SyncMessage,
    compute_diff,
    compute_session_hash,
)

# Try to import websockets
try:
    import websockets
    from websockets.client import WebSocketClientProtocol

    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    WebSocketClientProtocol = None  # type: ignore


@dataclass
class PeerInfo:
    """Information about a peer."""

    peer_id: str
    address: str
    public_key: bytes
    last_seen: datetime
    online: bool = True


class PeerConnection:
    """Manages connection to a single peer."""

    def __init__(
        self,
        peer: PeerInfo,
        crypto: CryptoManager | MockCryptoManager,
        device_id: str,
    ):
        self.peer = peer
        self.crypto = crypto
        self.device_id = device_id
        self._ws: WebSocketClientProtocol | None = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        """Establish connection to peer."""
        if not WEBSOCKETS_AVAILABLE:
            return False

        try:
            self._ws = await websockets.connect(f"ws://{self.peer.address}")
            self._connected = True

            # Perform handshake
            await self._handshake()
            return True

        except Exception as e:
            self._connected = False
            return False

    async def _handshake(self) -> None:
        """Perform initial handshake with peer."""
        # Derive shared key
        self.crypto.derive_shared_key(self.peer.peer_id, self.peer.public_key)

        # Send hello
        hello = SyncMessage(
            type=MessageType.HELLO,
            peer_id=self.device_id,
            payload={
                "public_key": self.crypto.get_public_key_b64(),
                "version": "1.0",
            },
            timestamp=datetime.now().isoformat(),
            nonce=self.crypto.generate_nonce(),
        )
        await self.send(hello)

        # Wait for hello_ack
        response = await self.receive()
        if response.type != MessageType.HELLO_ACK:
            raise RuntimeError(f"Expected HELLO_ACK, got {response.type}")

    async def send(self, message: SyncMessage) -> None:
        """Send encrypted message to peer."""
        if not self._ws:
            raise RuntimeError("Not connected")

        data = message.to_bytes()
        encrypted = self.crypto.encrypt(self.peer.peer_id, data)
        await self._ws.send(encrypted)

    async def receive(self, timeout: float = 30.0) -> SyncMessage:
        """Receive and decrypt message from peer."""
        if not self._ws:
            raise RuntimeError("Not connected")

        encrypted = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
        data = self.crypto.decrypt(self.peer.peer_id, encrypted)
        return SyncMessage.from_bytes(data)

    async def close(self) -> None:
        """Close connection."""
        if self._ws:
            await self._ws.close()
        self._connected = False


class SyncManager:
    """Manages sync operations with peers."""

    def __init__(
        self,
        scanner: ClaudeScanner,
        data_dir: Path,
        device_id: str | None = None,
    ):
        self.scanner = scanner
        self.data_dir = data_dir
        self.device_id = device_id or str(uuid.uuid4())

        # Initialize crypto
        crypto_dir = data_dir / "sync" / "keys"
        try:
            self.crypto = CryptoManager(crypto_dir)
            self.crypto.load_keys() or self.crypto.generate_keys()
        except RuntimeError:
            self.crypto = MockCryptoManager(crypto_dir)

        self.peers: dict[str, PeerConnection] = {}
        self._sync_callbacks: list[Callable] = []

    def get_local_session_hashes(self) -> list[SessionHash]:
        """Get hashes of all local sessions."""
        hashes = []
        for project in self.scanner.scan_all():
            for session in project.sessions:
                try:
                    h = compute_session_hash(session.jsonl_path)
                    hashes.append(h)
                except Exception:
                    pass
        return hashes

    async def sync_with_peer(self, peer: PeerConnection) -> dict:
        """Perform full sync with a peer."""
        if not peer.connected:
            success = await peer.connect()
            if not success:
                return {"error": "Failed to connect"}

        # Get local session hashes
        local_hashes = self.get_local_session_hashes()

        # Send session list
        list_msg = SyncMessage(
            type=MessageType.SESSION_LIST,
            peer_id=self.device_id,
            payload={
                "sessions": [h.to_dict() for h in local_hashes],
            },
            timestamp=datetime.now().isoformat(),
            nonce=self.crypto.generate_nonce(),
        )
        await peer.send(list_msg)

        # Receive peer's session list
        response = await peer.receive()
        if response.type != MessageType.SESSION_LIST:
            return {"error": f"Unexpected response: {response.type}"}

        remote_hashes = [SessionHash.from_dict(h) for h in response.payload["sessions"]]

        # Compute diff
        to_upload, to_download, conflicts = compute_diff(local_hashes, remote_hashes)

        results = {
            "uploaded": 0,
            "downloaded": 0,
            "conflicts": len(conflicts),
        }

        # Upload sessions
        for session_id in to_upload:
            success = await self._upload_session(peer, session_id)
            if success:
                results["uploaded"] += 1

        # Download sessions
        for session_id in to_download:
            success = await self._download_session(peer, session_id)
            if success:
                results["downloaded"] += 1

        return results

    async def _upload_session(self, peer: PeerConnection, session_id: str) -> bool:
        """Upload a session to peer."""
        # Find session file
        for project in self.scanner.scan_all():
            for session in project.sessions:
                if session.id == session_id:
                    content = session.jsonl_path.read_bytes()

                    msg = SyncMessage(
                        type=MessageType.SESSION_DATA,
                        peer_id=self.device_id,
                        payload={
                            "session_id": session_id,
                            "project_path": session.project_path,
                            "content": content.decode("utf-8", errors="replace"),
                        },
                        timestamp=datetime.now().isoformat(),
                        nonce=self.crypto.generate_nonce(),
                    )
                    await peer.send(msg)

                    # Wait for ack
                    response = await peer.receive()
                    return response.type == MessageType.SESSION_ACK

        return False

    async def _download_session(self, peer: PeerConnection, session_id: str) -> bool:
        """Download a session from peer."""
        # Request session
        msg = SyncMessage(
            type=MessageType.SESSION_REQUEST,
            peer_id=self.device_id,
            payload={"session_id": session_id},
            timestamp=datetime.now().isoformat(),
            nonce=self.crypto.generate_nonce(),
        )
        await peer.send(msg)

        # Receive session data
        response = await peer.receive()
        if response.type != MessageType.SESSION_DATA:
            return False

        # Save session
        project_path = response.payload["project_path"]
        content = response.payload["content"]

        project_dir = self.scanner.projects_dir / project_path
        project_dir.mkdir(parents=True, exist_ok=True)

        session_file = project_dir / f"{session_id}.jsonl"
        session_file.write_text(content)

        # Send ack
        ack = SyncMessage(
            type=MessageType.SESSION_ACK,
            peer_id=self.device_id,
            payload={"session_id": session_id},
            timestamp=datetime.now().isoformat(),
            nonce=self.crypto.generate_nonce(),
        )
        await peer.send(ack)

        return True

    def add_peer(self, peer_info: PeerInfo) -> PeerConnection:
        """Add a peer to sync with."""
        conn = PeerConnection(peer_info, self.crypto, self.device_id)
        self.peers[peer_info.peer_id] = conn
        return conn

    async def sync_all(self) -> dict:
        """Sync with all connected peers."""
        results = {}
        for peer_id, peer in self.peers.items():
            try:
                result = await self.sync_with_peer(peer)
                results[peer_id] = result
            except Exception as e:
                results[peer_id] = {"error": str(e)}
        return results
