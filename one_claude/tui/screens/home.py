"""Home screen with session list."""

from datetime import datetime

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Input, Label, ListItem, ListView, Static

from one_claude.core.models import Project, Session
from one_claude.core.scanner import ClaudeScanner


class SessionListItem(ListItem):
    """A single session item in the list."""

    def __init__(self, session: Session):
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        """Create the session item display."""
        yield Static(self.session.title or "Untitled", classes="session-title")
        checkpoint_str = f"  {self.session.checkpoint_count} cp" if self.session.checkpoint_count else ""
        yield Static(
            f"{self._get_project_name()}  {self._format_time()}  {self.session.message_count} msgs{checkpoint_str}",
            classes="session-meta",
        )

    def _get_project_name(self) -> str:
        """Get short project name."""
        path = self.session.project_display
        parts = path.rstrip("/").split("/")
        return parts[-1] if parts else path

    def _format_time(self) -> str:
        """Format the session time as relative."""
        now = datetime.now()
        updated = self.session.updated_at

        # Make both naive for comparison
        if updated.tzinfo is not None:
            updated = updated.replace(tzinfo=None)

        diff = now - updated
        seconds = diff.total_seconds()

        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            mins = int(seconds / 60)
            return f"{mins}m ago"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            return f"{hours}h ago"
        elif seconds < 604800:
            days = int(seconds / 86400)
            return f"{days}d ago"
        else:
            return updated.strftime("%Y-%m-%d")


class ProjectListItem(ListItem):
    """A single project item in the sidebar."""

    def __init__(self, project: Project | None = None, label: str = "All"):
        super().__init__()
        self.project = project
        self.label = label

    def compose(self) -> ComposeResult:
        """Create the project item display."""
        if self.project:
            name = self._get_project_name()
            count = self.project.session_count
            yield Static(f"{name} ({count})")
        else:
            yield Static(self.label)

    def _get_project_name(self) -> str:
        """Get short project name."""
        if not self.project:
            return self.label
        path = self.project.display_path
        parts = path.rstrip("/").split("/")
        return parts[-1] if parts else path


class HomeScreen(Screen):
    """Home screen showing all sessions."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("enter", "select", "Open"),
        Binding("/", "focus_search", "/ Search"),
        Binding("escape", "clear_search", "Clear", show=False),
        Binding("tab", "switch_focus", "Tab Switch", show=False),
    ]

    DEFAULT_CSS = """
    HomeScreen #search-input {
        dock: top;
        width: 100%;
        margin: 1 2;
    }

    HomeScreen #main-container {
        width: 100%;
        height: 1fr;
        margin-top: 1;
    }
    """

    def __init__(self, scanner: ClaudeScanner):
        super().__init__()
        self.scanner = scanner
        self.projects: list[Project] = []
        self.sessions: list[Session] = []
        self.all_sessions: list[Session] = []  # Unfiltered
        self.selected_project: Project | None = None
        self.search_query: str = ""

    def compose(self) -> ComposeResult:
        """Create the home screen layout."""
        # Search box at top
        yield Input(placeholder="Search sessions... (/ to focus)", id="search-input")

        with Horizontal(id="main-container"):
            with Vertical(classes="sidebar"):
                yield Label("Projects", id="projects-header")
                yield ListView(id="project-list")
            with Vertical(classes="content"):
                yield Label("Sessions", id="sessions-header")
                yield ListView(id="session-list")

    def on_mount(self) -> None:
        """Load sessions on mount."""
        self.refresh_sessions()
        # Focus session list by default
        self.query_one("#session-list", ListView).focus()

    def refresh_sessions(self) -> None:
        """Refresh the session list."""
        self.projects = self.scanner.scan_all()

        # Populate project list
        project_list = self.query_one("#project-list", ListView)
        project_list.clear()
        project_list.append(ProjectListItem(None, "All"))
        for project in self.projects:
            project_list.append(ProjectListItem(project))

        # Build all sessions list
        self.all_sessions = []
        for project in self.projects:
            self.all_sessions.extend(project.sessions)
        self.all_sessions.sort(key=lambda s: s.updated_at, reverse=True)

        # Show sessions
        self._update_session_list()

    def _update_session_list(self) -> None:
        """Update the session list based on selected project and search."""
        session_list = self.query_one("#session-list", ListView)
        session_list.clear()

        # Start with all or project-filtered sessions
        if self.selected_project:
            base_sessions = self.selected_project.sessions
        else:
            base_sessions = self.all_sessions

        # Apply search filter
        if self.search_query:
            query = self.search_query.lower()
            self.sessions = [
                s for s in base_sessions
                if query in (s.title or "").lower()
                or query in s.project_display.lower()
            ]
        else:
            self.sessions = list(base_sessions)

        for session in self.sessions:
            session_list.append(SessionListItem(session))

        # Update header
        header = self.query_one("#sessions-header", Label)
        count = len(self.sessions)
        if self.selected_project:
            name = self.selected_project.display_path.rstrip("/").split("/")[-1]
            header.update(f"Sessions - {name} ({count})")
        else:
            header.update(f"Sessions ({count})")

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle search input changes."""
        if event.input.id == "search-input":
            self.search_query = event.value
            self._update_session_list()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle search input submission (Enter)."""
        if event.input.id == "search-input":
            # Focus the session list and select first item
            session_list = self.query_one("#session-list", ListView)
            session_list.focus()
            if self.sessions:
                session_list.index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle list selection."""
        if isinstance(event.item, ProjectListItem):
            self.selected_project = event.item.project
            self._update_session_list()
        elif isinstance(event.item, SessionListItem):
            # Open session screen
            from one_claude.tui.screens.session import SessionScreen

            self.app.push_screen(SessionScreen(event.item.session, self.scanner))

    def action_cursor_down(self) -> None:
        """Move cursor down."""
        focused = self.focused
        if isinstance(focused, ListView):
            focused.action_cursor_down()

    def action_cursor_up(self) -> None:
        """Move cursor up."""
        focused = self.focused
        if isinstance(focused, ListView):
            focused.action_cursor_up()

    def action_select(self) -> None:
        """Select current item."""
        focused = self.focused
        if isinstance(focused, ListView):
            focused.action_select_cursor()

    def action_switch_focus(self) -> None:
        """Switch focus between project and session list."""
        project_list = self.query_one("#project-list", ListView)
        session_list = self.query_one("#session-list", ListView)

        if self.focused == project_list:
            session_list.focus()
        else:
            project_list.focus()

    def action_focus_search(self) -> None:
        """Focus the search input."""
        search_input = self.query_one("#search-input", Input)
        search_input.focus()

    def action_clear_search(self) -> None:
        """Clear search and return to list."""
        search_input = self.query_one("#search-input", Input)
        if search_input.has_focus:
            search_input.value = ""
            self.search_query = ""
            self._update_session_list()
            self.query_one("#session-list", ListView).focus()
