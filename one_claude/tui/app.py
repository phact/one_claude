"""Main Textual application for one_claude."""

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header

from one_claude.config import Config
from one_claude.core.scanner import ClaudeScanner
from one_claude.teleport.sandbox import is_msb_available
from one_claude.tui.screens.home import HomeScreen
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

        # Check microsandbox availability for sandbox mode
        self.sandbox_available = is_msb_available()

        # Update subtitle to show sandbox mode
        mode = "sandbox" if self.sandbox_available else "local"
        self.sub_title = f"Time Travel for Claude Code [{mode}]"

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        yield Footer()

    def on_mount(self) -> None:
        """Handle app mount - push the home screen."""
        self.push_screen(HomeScreen(self.scanner))

    def action_search(self) -> None:
        """Focus search on home screen."""
        if isinstance(self.screen, HomeScreen):
            self.screen.action_focus_search()

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
    app = OneClaude()
    app.run()


if __name__ == "__main__":
    run()
