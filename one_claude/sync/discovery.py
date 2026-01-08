"""Peer discovery client."""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import orjson

# Try to import websockets and aiohttp
try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

try:
    import websockets

    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False


@dataclass
class DiscoveryPeer:
    """A peer from the discovery server."""

    peer_id: str
    public_key: str
    address: str
    last_seen: str
    online: bool


class DiscoveryClient:
    """Client for peer discovery server."""

    def __init__(
        self,
        server_url: str,
        device_id: str,
        auth_token: str | None = None,
    ):
        self.server_url = server_url.rstrip("/")
        self.device_id = device_id
        self.auth_token = auth_token
        self._ws = None
        self._callbacks: list[Callable] = []

    @property
    def available(self) -> bool:
        """Check if required libraries are available."""
        return AIOHTTP_AVAILABLE

    async def register(
        self,
        public_key: str,
        address: str,
    ) -> bool:
        """Register this device with discovery server."""
        if not AIOHTTP_AVAILABLE:
            return False

        async with aiohttp.ClientSession() as session:
            headers = {}
            if self.auth_token:
                headers["Authorization"] = f"Bearer {self.auth_token}"

            data = {
                "device_id": self.device_id,
                "public_key": public_key,
                "address": address,
                "capabilities": ["sync_v1"],
            }

            try:
                async with session.post(
                    f"{self.server_url}/register",
                    json=data,
                    headers=headers,
                ) as resp:
                    return resp.status == 200
            except Exception:
                return False

    async def get_peers(self, user_id: str | None = None) -> list[DiscoveryPeer]:
        """Get list of available peers."""
        if not AIOHTTP_AVAILABLE:
            return []

        async with aiohttp.ClientSession() as session:
            headers = {}
            if self.auth_token:
                headers["Authorization"] = f"Bearer {self.auth_token}"

            params = {}
            if user_id:
                params["user_id"] = user_id

            try:
                async with session.get(
                    f"{self.server_url}/peers",
                    params=params,
                    headers=headers,
                ) as resp:
                    if resp.status != 200:
                        return []

                    data = await resp.json()
                    return [
                        DiscoveryPeer(
                            peer_id=p["device_id"],
                            public_key=p["public_key"],
                            address=p["address"],
                            last_seen=p.get("last_seen", ""),
                            online=p.get("online", False),
                        )
                        for p in data.get("peers", [])
                    ]
            except Exception:
                return []

    async def subscribe(self, callback: Callable) -> None:
        """Subscribe to peer updates via WebSocket."""
        if not WEBSOCKETS_AVAILABLE:
            return

        self._callbacks.append(callback)

        ws_url = self.server_url.replace("http", "ws") + "/ws"

        try:
            async with websockets.connect(ws_url) as ws:
                self._ws = ws

                # Subscribe
                await ws.send(
                    orjson.dumps(
                        {
                            "type": "subscribe",
                            "device_id": self.device_id,
                        }
                    ).decode()
                )

                # Listen for updates
                async for message in ws:
                    try:
                        data = orjson.loads(message)
                        for cb in self._callbacks:
                            await cb(data)
                    except Exception:
                        pass

        except Exception:
            pass
        finally:
            self._ws = None

    async def heartbeat(self) -> bool:
        """Send keepalive to discovery server."""
        if not AIOHTTP_AVAILABLE:
            return False

        async with aiohttp.ClientSession() as session:
            headers = {}
            if self.auth_token:
                headers["Authorization"] = f"Bearer {self.auth_token}"

            try:
                async with session.post(
                    f"{self.server_url}/heartbeat",
                    json={"device_id": self.device_id},
                    headers=headers,
                ) as resp:
                    return resp.status == 200
            except Exception:
                return False

    async def unregister(self) -> bool:
        """Unregister this device from discovery server."""
        if not AIOHTTP_AVAILABLE:
            return False

        async with aiohttp.ClientSession() as session:
            headers = {}
            if self.auth_token:
                headers["Authorization"] = f"Bearer {self.auth_token}"

            try:
                async with session.delete(
                    f"{self.server_url}/register/{self.device_id}",
                    headers=headers,
                ) as resp:
                    return resp.status == 200
            except Exception:
                return False
