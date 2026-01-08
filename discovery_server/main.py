"""Standalone discovery server for one_claude P2P sync.

This server only handles peer registration and address exchange.
No session data passes through - all sync is direct P2P.

Run with: python -m discovery_server.main --port 8765
"""

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta

try:
    import websockets
    from websockets.server import serve
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class Peer:
    """A registered peer."""

    peer_id: str
    public_key: str
    address: str  # WebSocket address for direct connection
    last_seen: datetime
    online: bool = True


class DiscoveryServer:
    """Discovery server for peer registration and lookup."""

    def __init__(self, cleanup_interval: int = 60, peer_timeout: int = 300):
        self.peers: dict[str, Peer] = {}
        self.connections: dict[str, any] = {}  # peer_id -> websocket
        self.cleanup_interval = cleanup_interval
        self.peer_timeout = peer_timeout

    async def register(self, peer_id: str, public_key: str, address: str) -> dict:
        """Register a peer."""
        now = datetime.now()

        if peer_id in self.peers:
            # Update existing peer
            peer = self.peers[peer_id]
            peer.public_key = public_key
            peer.address = address
            peer.last_seen = now
            peer.online = True
            logger.info(f"Peer updated: {peer_id}")
        else:
            # New peer
            self.peers[peer_id] = Peer(
                peer_id=peer_id,
                public_key=public_key,
                address=address,
                last_seen=now,
                online=True,
            )
            logger.info(f"Peer registered: {peer_id}")

        return {"status": "ok", "peer_id": peer_id}

    async def unregister(self, peer_id: str) -> dict:
        """Unregister a peer."""
        if peer_id in self.peers:
            self.peers[peer_id].online = False
            logger.info(f"Peer unregistered: {peer_id}")
        return {"status": "ok"}

    async def lookup(self, peer_id: str) -> dict:
        """Look up a peer by ID."""
        peer = self.peers.get(peer_id)
        if peer and peer.online:
            return {
                "status": "found",
                "peer": {
                    "peer_id": peer.peer_id,
                    "public_key": peer.public_key,
                    "address": peer.address,
                }
            }
        return {"status": "not_found"}

    async def list_peers(self) -> dict:
        """List all online peers."""
        online_peers = [
            {
                "peer_id": p.peer_id,
                "public_key": p.public_key,
                "address": p.address,
            }
            for p in self.peers.values()
            if p.online
        ]
        return {"status": "ok", "peers": online_peers}

    async def heartbeat(self, peer_id: str) -> dict:
        """Update peer heartbeat."""
        if peer_id in self.peers:
            self.peers[peer_id].last_seen = datetime.now()
            self.peers[peer_id].online = True
        return {"status": "ok"}

    async def cleanup_stale_peers(self):
        """Remove stale peers periodically."""
        while True:
            await asyncio.sleep(self.cleanup_interval)

            now = datetime.now()
            timeout = timedelta(seconds=self.peer_timeout)

            stale = []
            for peer_id, peer in self.peers.items():
                if now - peer.last_seen > timeout:
                    peer.online = False
                    stale.append(peer_id)

            if stale:
                logger.info(f"Marked {len(stale)} peers as offline")

    async def broadcast_peer_status(self, peer_id: str, online: bool):
        """Broadcast peer status change to all connected peers."""
        message = json.dumps({
            "type": "peer_status",
            "peer_id": peer_id,
            "online": online,
        })

        for pid, ws in list(self.connections.items()):
            if pid != peer_id:
                try:
                    await ws.send(message)
                except Exception:
                    pass

    async def handle_connection(self, websocket):
        """Handle a WebSocket connection."""
        peer_id = None

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    action = data.get("action")

                    if action == "register":
                        peer_id = data.get("peer_id")
                        result = await self.register(
                            peer_id=peer_id,
                            public_key=data.get("public_key", ""),
                            address=data.get("address", ""),
                        )
                        self.connections[peer_id] = websocket
                        await websocket.send(json.dumps(result))
                        await self.broadcast_peer_status(peer_id, True)

                    elif action == "unregister":
                        result = await self.unregister(data.get("peer_id", peer_id))
                        await websocket.send(json.dumps(result))

                    elif action == "lookup":
                        result = await self.lookup(data.get("peer_id"))
                        await websocket.send(json.dumps(result))

                    elif action == "list":
                        result = await self.list_peers()
                        await websocket.send(json.dumps(result))

                    elif action == "heartbeat":
                        result = await self.heartbeat(data.get("peer_id", peer_id))
                        await websocket.send(json.dumps(result))

                    else:
                        await websocket.send(json.dumps({
                            "status": "error",
                            "message": f"Unknown action: {action}"
                        }))

                except json.JSONDecodeError:
                    await websocket.send(json.dumps({
                        "status": "error",
                        "message": "Invalid JSON"
                    }))

        except websockets.exceptions.ConnectionClosed:
            pass

        finally:
            if peer_id:
                self.connections.pop(peer_id, None)
                if peer_id in self.peers:
                    self.peers[peer_id].online = False
                await self.broadcast_peer_status(peer_id, False)
                logger.info(f"Peer disconnected: {peer_id}")


async def main(host: str, port: int):
    """Run the discovery server."""
    if not WEBSOCKETS_AVAILABLE:
        print("Error: websockets not installed. Install with: uv pip install websockets")
        return

    server = DiscoveryServer()

    # Start cleanup task
    cleanup_task = asyncio.create_task(server.cleanup_stale_peers())

    logger.info(f"Starting discovery server on {host}:{port}")

    async with serve(server.handle_connection, host, port):
        await asyncio.Future()  # Run forever


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="one_claude discovery server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on")

    args = parser.parse_args()

    asyncio.run(main(args.host, args.port))
