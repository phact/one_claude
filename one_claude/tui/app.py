"""Main Textual application for one_claude."""

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header

from one_claude.config import Config
from one_claude.core.scanner import ClaudeScanner
from one_claude.tui.screens.home import HomeScreen
from one_claude.tui.screens.search import SearchScreen
from one_claude.tui.screens.session import SessionScreen


class OneClaude(App):
    """Main application for browsing Claude Code sessions."""

    TITLE = "one_claude"
    SUB_TITLE = "Time Travel for Claude Code"

    CSS = """
    Screen {
        background: $surface;
    }

    #main-container {
        height: 100%;
        width: 100%;
    }

    .session-list {
        width: 100%;
        height: 100%;
    }

    .session-item {
        padding: 1;
        border-bottom: solid $primary-background;
    }

    .session-item:hover {
        background: $primary-background;
    }

    .session-item.--highlight {
        background: $primary;
    }

    .session-title {
        text-style: bold;
    }

    .session-meta {
        color: $text-muted;
    }

    .message-container {
        padding: 1;
        margin-bottom: 1;
        border: solid $primary-background;
    }

    .message-user {
        background: $primary-background-darken-2;
    }

    .message-assistant {
        background: $surface-darken-1;
    }

    .message-header {
        text-style: bold;
        margin-bottom: 1;
    }

    .tool-use {
        background: $warning-muted;
        padding: 0 1;
        margin: 1 0;
    }

    .sidebar {
        width: 30;
        dock: left;
        border-right: solid $primary-background;
    }

    .content {
        width: 1fr;
    }

    Header {
        dock: top;
    }

    Footer {
        dock: bottom;
    }

    #search-input {
        dock: top;
        margin: 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("/", "search", "Search"),
        Binding("escape", "back", "Back"),
        Binding("?", "help", "Help"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, config: Config | None = None):
        super().__init__()
        self.config = config or Config.load()
        self.scanner = ClaudeScanner(self.config.claude_dir)

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        yield Footer()

    def on_mount(self) -> None:
        """Handle app mount - push the home screen."""
        self.push_screen(HomeScreen(self.scanner))

    def action_search(self) -> None:
        """Open search."""
        self.push_screen(SearchScreen(self.scanner))

    def action_back(self) -> None:
        """Go back to previous screen."""
        if len(self.screen_stack) > 1:
            self.pop_screen()

    def action_refresh(self) -> None:
        """Refresh the current view."""
        if isinstance(self.screen, HomeScreen):
            self.screen.refresh_sessions()

    def action_help(self) -> None:
        """Show help."""
        self.notify(
            "/ - Search | Enter - Open | Esc - Back | q - Quit",
            title="Keyboard Shortcuts",
        )

    def open_session(self, session_id: str) -> None:
        """Open a session in detail view."""
        # Find the session
        for project in self.scanner.scan_all():
            for session in project.sessions:
                if session.id == session_id:
                    self.push_screen(SessionScreen(session, self.scanner))
                    return


def run() -> None:
    """Run the one_claude TUI application."""
    import subprocess
    import asyncio

    app = OneClaude()
    result = app.run()

    # Debug: write result to file
    with open("/tmp/one_claude_debug.txt", "w") as f:
        f.write(f"Result type: {type(result)}\n")
        f.write(f"Result: {result}\n")

    # Handle teleport - launch shell after TUI exits
    if isinstance(result, dict) and "teleport" in result:
        import os
        import sys

        shell_cmd = result["teleport"]
        teleport_session = result.get("cleanup")
        isolated = result.get("isolated", False)
        working_dir = teleport_session.sandbox.working_dir if teleport_session else None

        mode = "isolated sandbox" if isolated else "local directory"
        sys.stderr.write(f"\n🚀 Teleporting to checkpoint ({mode})...\n")
        sys.stderr.write(f"   Working directory: {working_dir}\n")
        sys.stderr.write(f"   Files restored: {len(teleport_session.files_restored) if teleport_session else 0}\n")
        if teleport_session and teleport_session.files_restored:
            sys.stderr.write(f"   Files: {list(teleport_session.files_restored.keys())[:5]}\n")
        sys.stderr.write(f"\n   Type 'exit' to return and cleanup.\n\n")
        sys.stderr.flush()

        try:
            # Change to working directory and exec into bash
            if working_dir:
                os.chdir(working_dir)
            os.execvp("bash", ["bash", "-i"])
        except Exception as e:
            sys.stderr.write(f"\n❌ Failed to launch shell: {e}\n")
            import traceback
            traceback.print_exc()
        finally:
            # Note: execvp replaces process, so this only runs on error
            if teleport_session:
                sys.stderr.write("\n🧹 Cleaning up teleport session...\n")
                try:
                    asyncio.run(teleport_session.sandbox.stop())
                except Exception as e:
                    sys.stderr.write(f"   Cleanup error: {e}\n")
                sys.stderr.write("   Done.\n")


if __name__ == "__main__":
    run()
