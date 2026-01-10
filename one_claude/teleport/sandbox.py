"""Sandbox wrapper for teleport functionality.

Supports multiple execution modes via pluggable executors:
- local: Run directly in original project directory
- docker: Docker container with proper TTY support
- microvm: Microsandbox (has TTY limitations)
"""

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from one_claude.teleport.executors import get_executor


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


@dataclass
class TeleportSandbox:
    """Manages file restoration for teleport.

    Uses msb exe for isolated sandbox mode, falls back to local directory.
    """

    session_id: str
    image: str = "phact/sandbox:v3"
    project_path: str = ""  # Original project path (cwd for claude)
    mode: str = "docker"  # local, docker, or microvm

    # Host directory for files (mounted into sandbox)
    _host_dir: Path | None = field(default=None, repr=False)
    _claude_dir: Path | None = field(default=None, repr=False)  # ~/.claude equivalent
    working_dir: str = ""  # Set on start
    files: dict[str, bytes] = field(default_factory=dict)
    _started: bool = False
    _using_sandbox: bool = False

    @property
    def available(self) -> bool:
        """Check if microsandbox is available."""
        return is_msb_available()

    @property
    def isolated(self) -> bool:
        """Check if running in actual isolated sandbox."""
        return self._using_sandbox

    @property
    def claude_dir(self) -> Path | None:
        """Get the claude config directory for this sandbox."""
        return self._claude_dir

    async def start(self) -> None:
        """Prepare working directory for file restoration."""
        if self._started:
            return

        # Create host directory for workspace files
        self._host_dir = Path(
            tempfile.mkdtemp(prefix=f"teleport_{self.session_id[:8]}_")
        )
        self.working_dir = str(self._host_dir)

        # Create ~/.claude equivalent directory
        self._claude_dir = Path(
            tempfile.mkdtemp(prefix=f"teleport_claude_{self.session_id[:8]}_")
        )

        # Sandbox mode is determined by whether we're using a container
        self._using_sandbox = self.mode in ("docker", "microvm")
        self._started = True

    async def stop(self) -> None:
        """Cleanup working directories."""
        self._started = False
        self._using_sandbox = False

        # Cleanup host directory
        if self._host_dir and self._host_dir.exists():
            shutil.rmtree(self._host_dir)

        # Cleanup claude directory
        if self._claude_dir and self._claude_dir.exists():
            shutil.rmtree(self._claude_dir)

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

    def setup_claude_config(
        self,
        source_claude_dir: Path,
        project_dir_name: str,
        jsonl_content: bytes,
        file_history_files: dict[str, bytes],
    ) -> None:
        """Setup the claude config directory for the sandbox.

        Args:
            source_claude_dir: Original ~/.claude directory
            project_dir_name: Name of project directory (e.g., "-home-tato-Desktop-foo")
            jsonl_content: Truncated JSONL content for the session
            file_history_files: Map of relative paths to content for file-history
        """
        if not self._claude_dir:
            raise RuntimeError("Sandbox not started")

        home_dir = source_claude_dir.parent  # ~/.claude -> ~

        # Copy ~/.claude.json (main auth/settings file)
        claude_json = home_dir / ".claude.json"
        if claude_json.exists():
            dest = self._claude_dir / ".claude.json"
            shutil.copy2(claude_json, dest)

        # Copy ~/.claude.json.backup
        claude_json_backup = home_dir / ".claude.json.backup"
        if claude_json_backup.exists():
            dest = self._claude_dir / ".claude.json.backup"
            shutil.copy2(claude_json_backup, dest)

        # Copy ~/.claude/.credentials.json (OAuth tokens)
        creds_file = source_claude_dir / ".credentials.json"
        if creds_file.exists():
            claude_subdir = self._claude_dir / ".claude"
            claude_subdir.mkdir(parents=True, exist_ok=True)
            dest = claude_subdir / ".credentials.json"
            shutil.copy2(creds_file, dest)

        # Copy settings.json
        settings_file = source_claude_dir / "settings.json"
        if settings_file.exists():
            claude_subdir = self._claude_dir / ".claude"
            claude_subdir.mkdir(parents=True, exist_ok=True)
            dest = claude_subdir / "settings.json"
            shutil.copy2(settings_file, dest)

        # Create project directory and write JSONL
        # Structure: _claude_dir/.claude/projects/<project_dir_name>/<session_id>.jsonl
        project_dir = self._claude_dir / ".claude" / "projects" / project_dir_name
        project_dir.mkdir(parents=True, exist_ok=True)

        # Write the truncated JSONL (use session_id as filename)
        jsonl_file = project_dir / f"{self.session_id}.jsonl"
        jsonl_file.write_bytes(jsonl_content)

        # Copy file history (flat structure: .claude/file-history/<session_id>/<hash>@v1)
        if file_history_files:
            fh_session_dir = self._claude_dir / ".claude" / "file-history" / self.session_id
            fh_session_dir.mkdir(parents=True, exist_ok=True)

            for filename, content in file_history_files.items():
                dest = fh_session_dir / filename
                dest.write_bytes(content)

    def get_shell_command(
        self,
        term: str | None = None,
        lines: int | None = None,
        columns: int | None = None,
    ) -> list[str]:
        """Get command to launch Claude Code using the configured executor.

        Args:
            term: TERM environment variable (e.g., xterm-256color)
            lines: Terminal height in lines (unused, for future tmux support)
            columns: Terminal width in columns (unused, for future tmux support)
        """
        executor = get_executor(self.mode)

        # Let executor prepare the claude config directory
        if self._claude_dir:
            executor.prepare(self._claude_dir)

        return executor.get_command(
            host_dir=self._host_dir,
            claude_dir=self._claude_dir,
            project_path=self.project_path,
            image=self.image,
            term=term,
        )
