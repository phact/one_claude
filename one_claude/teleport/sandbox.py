"""Microsandbox wrapper for teleport functionality.

Uses the microsandbox CLI (msb exe) for isolated sandboxes with volume mounts.
Falls back to local directory mode if msb is not available.
"""

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SandboxResult:
    """Result of a sandbox command execution."""

    stdout: str
    stderr: str
    exit_code: int


def is_msb_available() -> bool:
    """Check if msb CLI is available and working."""
    try:
        result = subprocess.run(
            ["msb", "version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


class TeleportSandbox:
    """Manages file restoration for teleport.

    Uses msb exe for isolated sandbox mode, falls back to local directory.
    """

    def __init__(
        self,
        session_id: str,
        image: str = "phact/sandbox",
    ):
        self.session_id = session_id
        self.image = image

        # Host directory for files (mounted into sandbox)
        self._host_dir: Path | None = None
        self.working_dir = ""  # Set on start
        self.files: dict[str, bytes] = {}
        self._started = False
        self._using_sandbox = False

    @property
    def available(self) -> bool:
        """Check if microsandbox is available."""
        return is_msb_available()

    @property
    def isolated(self) -> bool:
        """Check if running in actual isolated sandbox."""
        return self._using_sandbox

    async def start(self) -> None:
        """Prepare working directory for file restoration."""
        if self._started:
            return

        # Create host directory for files
        self._host_dir = Path(
            tempfile.mkdtemp(prefix=f"teleport_{self.session_id[:8]}_")
        )
        self.working_dir = str(self._host_dir)

        # Check if msb is available for sandbox mode
        self._using_sandbox = is_msb_available()
        self._started = True

    async def stop(self) -> None:
        """Cleanup working directory."""
        self._started = False
        self._using_sandbox = False

        # Cleanup host directory
        if self._host_dir and self._host_dir.exists():
            shutil.rmtree(self._host_dir)

    async def write_file(self, path: str, content: bytes) -> None:
        """Write file to working directory."""
        if not self._host_dir:
            raise RuntimeError("Sandbox not started")

        # Use relative path from root
        rel_path = path.lstrip("/")
        file_path = self._host_dir / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)
        self.files[path] = content

    async def read_file(self, path: str) -> bytes:
        """Read file from working directory."""
        if not self._host_dir:
            raise RuntimeError("Sandbox not started")

        rel_path = path.lstrip("/")
        file_path = self._host_dir / rel_path
        if file_path.exists():
            return file_path.read_bytes()
        return self.files.get(path, b"")

    async def list_files(self, path: str = ".") -> list[str]:
        """List files in working directory."""
        return list(self.files.keys())

    def get_shell_command(self) -> list[str]:
        """Get command to launch tmux with Claude Code + terminal split.

        Layout: Claude Code on left, terminal on right.
        In sandbox mode, wraps terminal commands with msb exe for isolation.
        """
        if self._using_sandbox and self._host_dir:
            # Sandbox mode: tmux locally, but terminal pane uses msb exe
            # Claude runs locally (has access to files via host dir)
            # Terminal runs in sandbox for safe command execution
            msb_shell = f"msb exe -v {self._host_dir}:/workspace --workdir /workspace -e bash {self.image}"
            tmux_cmd = (
                f"tmux new-session -s teleport "
                f"\\; send-keys 'claude' Enter "
                f"\\; split-window -h "
                f"\\; send-keys '{msb_shell}' Enter "
                f"\\; select-pane -L"
            )
        else:
            # Local mode: both claude and terminal run locally
            tmux_cmd = (
                "tmux new-session -s teleport "
                "\\; send-keys 'claude' Enter "
                "\\; split-window -h "
                "\\; select-pane -L"
            )

        return ["bash", "-c", tmux_cmd]
