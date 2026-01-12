"""GitHub Gist API client with OAuth device flow."""

import asyncio
import os
from pathlib import Path

import httpx

GIST_API = "https://api.github.com/gists"
CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "Iv23liW8XnbC7QiqENOl")
TOKEN_FILE = Path.home() / ".one_claude" / "github_token"


def get_token() -> str | None:
    """Get stored GitHub token."""
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    return None


def save_token(token: str) -> None:
    """Save GitHub token."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token)
    TOKEN_FILE.chmod(0o600)


def clear_token() -> None:
    """Clear stored token."""
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


async def start_device_flow() -> tuple[dict | None, str | None]:
    """Start device flow, return auth info for user to complete."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/device/code",
            headers={"Accept": "application/json"},
            data={"client_id": CLIENT_ID, "scope": "gist"},
        )
        if resp.status_code != 200:
            return None, f"Auth failed: {resp.status_code}"

        data = resp.json()
        return {
            "device_code": data["device_code"],
            "user_code": data["user_code"],
            "verification_uri": data["verification_uri"],
            "interval": data.get("interval", 5),
        }, None


async def poll_for_token(device_code: str, interval: int = 5) -> tuple[str | None, str | None]:
    """Poll for token after user authorizes."""
    async with httpx.AsyncClient() as client:
        while True:
            await asyncio.sleep(interval)
            resp = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            result = resp.json()

            if "access_token" in result:
                token = result["access_token"]
                save_token(token)
                return token, None

            error = result.get("error")
            if error == "authorization_pending":
                continue
            elif error == "slow_down":
                interval += 5
            elif error == "expired_token":
                return None, "Code expired"
            elif error == "access_denied":
                return None, "Access denied"
            else:
                return None, f"Auth error: {error}"


class GistAPI:
    """Async client for GitHub Gist API."""

    async def create(
        self,
        files: dict[str, str],
        description: str = "",
    ) -> tuple[str | None, str | None]:
        """Create a gist. Returns (None, 'auth_needed') if no token."""
        token = get_token()
        if not token:
            return None, "auth_needed"

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    GIST_API,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {token}",
                    },
                    json={
                        "description": description,
                        "public": True,
                        "files": {name: {"content": content} for name, content in files.items()},
                    },
                    timeout=60.0,
                )
                if resp.status_code == 201:
                    return resp.json()["html_url"], None
                elif resp.status_code == 401:
                    clear_token()
                    return None, "Token expired, try again"
                elif resp.status_code == 422:
                    return None, f"Validation failed: {resp.text}"
                else:
                    return None, f"GitHub API error: {resp.status_code}"
            except httpx.TimeoutException:
                return None, "Request timed out"
            except httpx.RequestError as e:
                return None, f"Network error: {e}"

    async def update(self, gist_id: str, files: dict[str, str]) -> tuple[bool, str | None]:
        """Update files in an existing gist."""
        token = get_token()
        if not token:
            return False, "No token"

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.patch(
                    f"{GIST_API}/{gist_id}",
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {token}",
                    },
                    json={"files": {name: {"content": content} for name, content in files.items()}},
                    timeout=60.0,
                )
                if resp.status_code == 200:
                    return True, None
                else:
                    return False, f"Update failed: {resp.status_code}"
            except Exception as e:
                return False, str(e)

    async def delete(self, gist_id: str) -> tuple[bool, str | None]:
        """Delete a gist."""
        token = get_token()
        if not token:
            return False, "No token"

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.delete(
                    f"{GIST_API}/{gist_id}",
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {token}",
                    },
                    timeout=60.0,
                )
                if resp.status_code == 204:
                    return True, None
                else:
                    return False, f"Delete failed: {resp.status_code}"
            except Exception as e:
                return False, str(e)

    async def get(self, gist_id: str) -> tuple[dict | None, str | None]:
        """Fetch a gist by ID."""
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(f"{GIST_API}/{gist_id}", timeout=60.0)
                if resp.status_code == 200:
                    return resp.json(), None
                elif resp.status_code == 404:
                    return None, "Gist not found"
                else:
                    return None, f"GitHub API error: {resp.status_code}"
            except httpx.TimeoutException:
                return None, "Request timed out"
            except httpx.RequestError as e:
                return None, f"Network error: {e}"

    async def get_raw_file(self, raw_url: str) -> tuple[str | None, str | None]:
        """Fetch raw file content from a gist."""
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(raw_url, timeout=60.0)
                if resp.status_code == 200:
                    return resp.text, None
                else:
                    return None, f"Failed to fetch file: {resp.status_code}"
            except httpx.TimeoutException:
                return None, "Request timed out"
            except httpx.RequestError as e:
                return None, f"Network error: {e}"
