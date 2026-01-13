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
        session_id: str,
        term: str | None = None,
    ) -> list[str]:
        """Get the command to run Claude in this execution mode.

        Args:
            host_dir: Host directory with workspace files (mounted to /workspace)
            claude_dir: Claude config directory (mounted to /root)
            project_path: Original project path (e.g., /tmp/test)
            image: Container image to use
            session_id: Session ID to resume
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
    _tmux_available: bool | None = None

    def is_available(self) -> bool:
        return True

    def has_tmux(self) -> bool:
        if self._tmux_available is None:
            self._tmux_available = shutil.which("tmux") is not None
        return self._tmux_available

    def get_command(
        self,
        host_dir: Path,
        claude_dir: Path,
        project_path: str,
        image: str,
        session_id: str,
        term: str | None = None,
    ) -> list[str]:
        claude_cmd = f"claude --resume {session_id}"

        # Fall back to direct claude if no tmux
        if not self.has_tmux():
            return ["bash", "-c", f"cd '{project_path}' && {claude_cmd}"]

        # tmux with dual panes: claude on left, shell on right
        # Kill any existing teleport session first
        # Use send-keys to run tree in right pane after it starts
        tmux_cmd = f"""
tmux kill-session -t teleport 2>/dev/null
tmux new-session -d -s teleport -c "{project_path}" "{claude_cmd}" \\; \
    split-window -h -c "{project_path}" \\; \
    send-keys "echo 'CMD: {claude_cmd}'" Enter \\; \
    send-keys "tree -L 2 2>/dev/null || ls -la" Enter \\; \
    select-pane -t 0 \\; \
    attach-session -t teleport
"""
        return ["bash", "-c", tmux_cmd.strip()]


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
        session_id: str,
        term: str | None = None,
    ) -> list[str]:
        inner_cwd = f"/workspace{project_path}"

        # Build docker run prefix
        # Use --user to run as current user (not root) so --dangerously-skip-permissions works
        docker_base = f"docker run -it --rm --user \\$(id -u):\\$(id -g) -v {host_dir}:/workspace -v {claude_dir}:/home/user -w {inner_cwd} -e HOME=/home/user"
        if term:
            docker_base += f" -e TERM={term}"

        claude_cmd = f"{docker_base} --name teleport-claude {image} claude --resume {session_id}"
        shell_cmd = f"{docker_base} --name teleport-shell {image} bash"

        # tmux with dual panes: claude on left, shell on right
        # Kill any existing teleport session and containers first
        # Use send-keys to run tree in right pane after it starts
        tmux_cmd = f"""
tmux kill-session -t teleport 2>/dev/null
docker rm -f teleport-claude teleport-shell 2>/dev/null
tmux new-session -d -s teleport "{claude_cmd}" \\; \
    split-window -h "{shell_cmd}" \\; \
    send-keys "echo 'CMD: claude --resume {session_id}'" Enter \\; \
    send-keys "tree -L 2 2>/dev/null || ls -la" Enter \\; \
    select-pane -t 0 \\; \
    attach-session -t teleport
"""
        return ["bash", "-c", tmux_cmd.strip()]


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
        session_id: str,
        term: str | None = None,
    ) -> list[str]:
        inner_cwd = f"/workspace{project_path}"

        # Build msb exe prefix
        msb_prefix = f"msb exe -v {host_dir}:/workspace -v {claude_dir}:/root --workdir {inner_cwd} --env HOME=/root"
        if term:
            msb_prefix += f" --env TERM={term}"

        claude_cmd = f'{msb_prefix} -e "claude --resume {session_id}" {image}'
        shell_cmd = f"{msb_prefix} -e bash {image}"

        # tmux with dual panes: claude on left, shell on right
        # Kill any existing teleport session first
        # Use send-keys to run tree in right pane after it starts
        tmux_cmd = f"""
tmux kill-session -t teleport 2>/dev/null
tmux new-session -d -s teleport "{claude_cmd}" \\; \
    split-window -h "{shell_cmd}" \\; \
    send-keys "tree -L 2 2>/dev/null || ls -la" Enter \\; \
    select-pane -t 0 \\; \
    attach-session -t teleport
"""
        return ["bash", "-c", tmux_cmd.strip()]


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
