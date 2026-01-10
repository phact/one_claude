"""Pluggable teleport executors for different sandbox modes."""

import shutil
from abc import ABC, abstractmethod
from pathlib import Path


class TeleportExecutor(ABC):
    """Base class for teleport execution strategies."""

    name: str  # Display name for the mode

    def is_available(self) -> bool:
        """Check if this executor is available on the system."""
        return True

    @abstractmethod
    def get_command(
        self,
        host_dir: Path,
        claude_dir: Path,
        project_path: str,
        image: str,
        term: str | None = None,
    ) -> list[str]:
        """Get the command to run Claude in this execution mode.

        Args:
            host_dir: Host directory with workspace files (mounted to /workspace)
            claude_dir: Claude config directory (mounted to /root)
            project_path: Original project path (e.g., /tmp/test)
            image: Container image to use
            term: TERM environment variable

        Returns:
            Command list to execute
        """
        pass

    def prepare(self, claude_dir: Path) -> None:
        """Prepare the claude config directory before execution.

        Override in subclasses if needed.
        """
        pass


class LocalExecutor(TeleportExecutor):
    """Run claude directly in the original project directory."""

    name = "local"

    def get_command(
        self,
        host_dir: Path,
        claude_dir: Path,
        project_path: str,
        image: str,
        term: str | None = None,
    ) -> list[str]:
        return ["bash", "-c", f"cd {project_path} && claude --continue"]


class DockerExecutor(TeleportExecutor):
    """Run claude in Docker container with proper TTY support."""

    name = "docker"

    def is_available(self) -> bool:
        """Check if docker is installed."""
        return shutil.which("docker") is not None

    def prepare(self, claude_dir: Path) -> None:
        """Create debug directory and fix installMethod."""
        import re

        # Create debug directory (Claude needs this)
        (claude_dir / ".claude" / "debug").mkdir(parents=True, exist_ok=True)

        # Fix installMethod in .claude.json
        claude_json = claude_dir / ".claude.json"
        if claude_json.exists():
            content = claude_json.read_text()
            content = re.sub(r'"installMethod":[^,}]*', '"installMethod":"npm"', content)
            claude_json.write_text(content)

    def get_command(
        self,
        host_dir: Path,
        claude_dir: Path,
        project_path: str,
        image: str,
        term: str | None = None,
    ) -> list[str]:
        inner_cwd = f"/workspace{project_path}"

        cmd = [
            "docker", "run",
            "-it",  # Interactive TTY
            "--rm",  # Clean up container after exit
            "-v", f"{host_dir}:/workspace",
            "-v", f"{claude_dir}:/root",
            "-w", inner_cwd,
            "-e", "HOME=/root",
        ]

        if term:
            cmd.extend(["-e", f"TERM={term}"])

        cmd.extend([image, "claude", "--continue"])
        return cmd


class MicrovmExecutor(TeleportExecutor):
    """Run claude in microsandbox (has TTY issues)."""

    name = "microvm"

    def is_available(self) -> bool:
        """Check if msb is installed."""
        return shutil.which("msb") is not None

    def prepare(self, claude_dir: Path) -> None:
        """Create debug directory and fix installMethod."""
        import re

        # Create debug directory (Claude needs this)
        (claude_dir / ".claude" / "debug").mkdir(parents=True, exist_ok=True)

        # Fix installMethod in .claude.json
        claude_json = claude_dir / ".claude.json"
        if claude_json.exists():
            content = claude_json.read_text()
            content = re.sub(r'"installMethod":[^,}]*', '"installMethod":"npm"', content)
            claude_json.write_text(content)

    def get_command(
        self,
        host_dir: Path,
        claude_dir: Path,
        project_path: str,
        image: str,
        term: str | None = None,
    ) -> list[str]:
        inner_cwd = f"/workspace{project_path}"

        cmd = [
            "msb", "exe",
            "-v", f"{host_dir}:/workspace",
            "-v", f"{claude_dir}:/root",
            "--workdir", inner_cwd,
            "--env", "HOME=/root",
        ]

        if term:
            cmd.extend(["--env", f"TERM={term}"])

        cmd.extend(["-e", "claude --continue", image])
        return cmd


# Registry of available executors
EXECUTORS: dict[str, TeleportExecutor] = {
    "local": LocalExecutor(),
    "docker": DockerExecutor(),
    "microvm": MicrovmExecutor(),
}


def get_executor(mode: str) -> TeleportExecutor:
    """Get executor for the given mode."""
    return EXECUTORS.get(mode, EXECUTORS["local"])


def get_mode_names() -> list[str]:
    """Get list of available mode names (only those installed on system)."""
    return [name for name, executor in EXECUTORS.items() if executor.is_available()]
