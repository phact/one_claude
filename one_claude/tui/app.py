"""Main Textual application for one_claude."""

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.theme import Theme
from textual.widgets import Header

from one_claude.config import Config
from one_claude.core.scanner import ClaudeScanner
from one_claude.teleport.sandbox import is_msb_available
from one_claude.tui.screens.gist_modals import HelpModal
from one_claude.tui.screens.home import HomeScreen
from one_claude.tui.screens.session import SessionScreen


# Noir theme: high contrast, cyan accents, colored accent bars
THEME_NOIR = Theme(
    name="noir",
    primary="#00d4ff",
    secondary="#b48cff",
    accent="#00d4ff",
    foreground="#e6e6e6",
    background="#0d0d0d",
    surface="#16161a",
    panel="#1e1e23",
    success="#50c878",
    warning="#ffb432",
    error="#ff5a5a",
    dark=True,
    variables={
        "border": "#32323c",
        "border-blurred": "#28282e",
        "surface-darken-1": "#121215",
        "surface-darken-2": "#0a0a0c",
        "text-muted": "#78788c",
        "scrollbar": "#32323c",
        "scrollbar-hover": "#50505a",
        "footer-background": "#16161a",
        "footer-key": "#00d4ff",
        "footer-description": "#78788c",
    },
)


class OneClaude(App):
    """Main application for browsing Claude Code sessions."""

    TITLE = "one_claude"
    SUB_TITLE = "Time Travel for Claude Code"

    CSS = """
    Screen {
        background: $background;
    }

    #main-container {
        height: 100%;
        width: 100%;
    }

    /* Sidebar styling */
    .sidebar {
        width: 26;
        dock: left;
        border-right: solid $border;
        padding: 0 1;
    }

    .content {
        width: 1fr;
        padding-left: 1;
    }

    /* Section labels */
    .section-label {
        color: $text-muted;
        text-style: bold;
        margin-bottom: 1;
    }

    /* Message styling with left accent bars */
    .message-container {
        padding: 1;
        margin-bottom: 1;
        border-left: thick gray;
    }

    .message-user {
        border-left: thick $primary;
    }

    .message-assistant {
        background: $surface;
        border-left: thick $secondary;
    }

    .message-summary {
        background: $warning 15%;
        border-left: thick $warning;
    }

    .message-checkpoint {
        background: $success 15%;
        border-left: thick $success;
    }

    .message-system {
        background: $surface-darken-1;
        border-left: thick gray;
    }

    .message-header {
        text-style: bold;
        margin-bottom: 1;
        color: $text-muted;
    }

    .message-user .message-header {
        color: $primary;
    }

    .message-assistant .message-header {
        color: $secondary;
    }

    .message-checkpoint .message-header {
        color: $success;
    }

    .tool-use {
        background: $surface-darken-1;
        padding: 0 1;
        margin: 1 0;
        border-left: solid $warning;
    }

    Header {
        dock: top;
    }

    Footer {
        dock: bottom;
    }

    #search-input {
        dock: top;
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "back", "Back"),
        Binding("?", "help", "Help"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, config: Config | None = None):
        super().__init__()
        self.config = config or Config.load()
        self.scanner = ClaudeScanner(self.config.claude_dir)

        # Register and set Noir theme
        self.register_theme(THEME_NOIR)

        # Check microsandbox availability for sandbox mode
        self.sandbox_available = is_msb_available()

        # Update subtitle to show sandbox mode
        mode = "sandbox" if self.sandbox_available else "local"
        self.sub_title = f"Time Travel for Claude Code [{mode}]"

    def on_mount(self) -> None:
        """Handle app mount - push the home screen."""
        self.theme = "noir"
        self.push_screen(HomeScreen(self.scanner))

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()

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
        """Show help modal with keyboard shortcuts."""
        screen_name = "session" if isinstance(self.screen, SessionScreen) else "home"
        self.push_screen(HelpModal(screen_name))

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
