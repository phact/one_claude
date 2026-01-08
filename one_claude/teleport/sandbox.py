"""Microsandbox wrapper for teleport functionality.

Uses the microsandbox JSON-RPC API directly for volume mounting support.
Falls back to local directory mode if server isn't running.
"""

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass
class SandboxResult:
    """Result of a sandbox command execution."""

    stdout: str
    stderr: str
    exit_code: int


class MicrosandboxClient:
    """Low-level JSON-RPC client for microsandbox server."""

    def __init__(
        self, server_url: str = "http://127.0.0.1:5555", api_key: str | None = None
    ):
        self.server_url = server_url.rstrip("/")
        self.api_key = api_key or os.environ.get("MSB_API_KEY")
        self._request_id = 0

    def _next_id(self) -> str:
        self._request_id += 1
        return str(self._request_id)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Make a JSON-RPC call to the microsandbox server."""
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params,
        }

        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                f"{self.server_url}/api/v1/rpc",
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            result = resp.json()

            if "error" in result:
                raise RuntimeError(f"RPC error: {result['error']}")

            return result.get("result", {})

    async def start_sandbox(
        self,
        name: str,
        namespace: str = "default",
        image: str = "python",
        memory: int = 1024,
        cpus: int = 1,
        workdir: str = "/workspace",
        volumes: list[str] | None = None,
    ) -> dict[str, Any]:
        """Start a new sandbox with optional volume mounts."""
        config = {
            "image": image,
            "memory": memory,
            "cpus": cpus,
            "workdir": workdir,
        }
        if volumes:
            config["volumes"] = volumes

        return await self._rpc(
            "sandbox.start",
            {"sandbox": name, "namespace": namespace, "config": config},
        )

    async def stop_sandbox(
        self, name: str, namespace: str = "default"
    ) -> dict[str, Any]:
        """Stop a running sandbox."""
        return await self._rpc(
            "sandbox.stop", {"sandbox": name, "namespace": namespace}
        )

    async def exec_command(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        namespace: str = "default",
        timeout: int = 60,
    ) -> dict[str, Any]:
        """Execute a shell command in a running sandbox."""
        params = {
            "sandbox": name,
            "namespace": namespace,
            "command": command,
            "timeout": timeout,
        }
        if args:
            params["args"] = args
        return await self._rpc("sandbox.command.run", params)

    def is_server_running(self) -> bool:
        """Check if microsandbox server is running."""
        try:
            with httpx.Client(timeout=2.0) as client:
                resp = client.get(f"{self.server_url}/health")
                return resp.status_code == 200
        except Exception:
            return False


class TeleportSandbox:
    """Manages microsandbox instances for file restoration.

    Files are written to a host directory which is mounted into the sandbox.
    Falls back to local directory mode if microsandbox server isn't running.
    """

    def __init__(
        self,
        session_id: str,
        working_dir: str | None = None,
        server_url: str = "http://127.0.0.1:5555",
    ):
        self.session_id = session_id
        self._requested_workdir = working_dir or "/workspace"
        self.client = MicrosandboxClient(server_url)
        self.sandbox_name = f"teleport_{session_id[:8]}"
        self.namespace = "one_claude"

        # Host directory for files (mounted into sandbox)
        self._host_dir: Path | None = None
        self.working_dir = ""  # Set on start
        self.files: dict[str, bytes] = {}
        self._started = False
        self._using_sandbox = False  # True if actual microsandbox, False for local mode

    @property
    def available(self) -> bool:
        """Check if microsandbox server is available."""
        return self.client.is_server_running()

    @property
    def isolated(self) -> bool:
        """Check if running in actual isolated sandbox."""
        return self._using_sandbox

    async def start(self) -> None:
        """Start sandbox with volume mount, or local directory if server not running."""
        if self._started:
            return

        # Create host directory for files
        self._host_dir = Path(
            tempfile.mkdtemp(prefix=f"teleport_{self.session_id[:8]}_")
        )
        self.working_dir = str(self._host_dir)

        # Try to start actual sandbox if server is running
        if self.client.is_server_running():
            try:
                volumes = [f"{self._host_dir}:{self._requested_workdir}"]
                await self.client.start_sandbox(
                    name=self.sandbox_name,
                    namespace=self.namespace,
                    workdir=self._requested_workdir,
                    volumes=volumes,
                )
                self._using_sandbox = True
            except Exception:
                # Fall back to local mode
                self._using_sandbox = False
        else:
            self._using_sandbox = False

        self._started = True

    async def stop(self) -> None:
        """Stop sandbox and cleanup."""
        if self._started and self._using_sandbox:
            try:
                await self.client.stop_sandbox(self.sandbox_name, self.namespace)
            except Exception:
                pass  # Sandbox may already be stopped

        self._started = False
        self._using_sandbox = False

        # Cleanup host directory
        if self._host_dir and self._host_dir.exists():
            shutil.rmtree(self._host_dir)

    async def run_command(self, cmd: str) -> SandboxResult:
        """Execute command in sandbox."""
        if not self._started:
            raise RuntimeError("Sandbox not started")

        if self._using_sandbox:
            result = await self.client.exec_command(
                name=self.sandbox_name,
                command="bash",
                args=["-c", cmd],
                namespace=self.namespace,
            )
            stdout = result.get("stdout", "") or ""
            stderr = result.get("stderr", "") or ""
            exit_code = result.get("exit_code", 0)
            return SandboxResult(stdout=stdout, stderr=stderr, exit_code=exit_code)
        else:
            # Local mode - just return placeholder
            return SandboxResult(stdout=f"[local] {cmd}", stderr="", exit_code=0)

    async def write_file(self, path: str, content: bytes) -> None:
        """Write file to host directory (visible in sandbox via mount)."""
        if not self._host_dir:
            raise RuntimeError("Sandbox not started")

        # Map sandbox path to host path
        if path.startswith(self._requested_workdir):
            rel_path = path[len(self._requested_workdir) :].lstrip("/")
        else:
            rel_path = path.lstrip("/")

        file_path = self._host_dir / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)
        self.files[path] = content

    async def read_file(self, path: str) -> bytes:
        """Read file from host directory."""
        if not self._host_dir:
            raise RuntimeError("Sandbox not started")

        if path.startswith(self._requested_workdir):
            rel_path = path[len(self._requested_workdir) :].lstrip("/")
        else:
            rel_path = path.lstrip("/")

        file_path = self._host_dir / rel_path
        if file_path.exists():
            return file_path.read_bytes()
        return self.files.get(path, b"")

    async def list_files(self, path: str = ".") -> list[str]:
        """List files in sandbox directory."""
        return list(self.files.keys())

    def get_shell_command(self) -> list[str]:
        """Get command to launch interactive shell."""
        if self._using_sandbox:
            # Use msb CLI to attach to sandbox
            return [
                "msb",
                "shell",
                "-n",
                self.namespace,
                self.sandbox_name,
            ]
        else:
            # Local mode - just cd to directory and run bash
            return ["bash", "--init-file", "/dev/null", "-i"]
