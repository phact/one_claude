"""Home screen with session list."""

import asyncio
import os
import shutil
import subprocess
import sys
from datetime import datetime

try:
    import pyperclip
except ImportError:
    pyperclip = None  # type: ignore

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Input, Label, ListItem, ListView, Static

from one_claude.core.models import Project, Session
from one_claude.core.scanner import ClaudeScanner
from one_claude.index.search import SearchEngine
from one_claude.teleport.executors import get_mode_names


class SessionListItem(ListItem):
    """A single session item in the list."""

    def __init__(self, session: Session):
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        """Create the session item display."""
        title = self.session.title or "Untitled"
        session_id = self.session.id[:8]
        checkpoint_str = f"  {self.session.checkpoint_count} cp" if self.session.checkpoint_count else ""
        meta = f"{self._get_project_name()}  {self._format_time()}  {self.session.message_count} msgs{checkpoint_str}"

        with Horizontal(classes="session-row"):
            yield Static(title, classes="session-title")
            yield Static(session_id, classes="session-id")
        yield Static(meta, classes="session-meta")

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
        Binding("c", "copy_session_id", "Copy ID"),
        Binding("t", "teleport", "Teleport"),
        Binding("m", "toggle_mode", "Mode"),
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

    HomeScreen #session-list {
        width: 100%;
    }

    HomeScreen #mode-indicator {
        dock: bottom;
        width: 100%;
        height: 1;
        text-align: right;
        background: $surface;
        color: $text-muted;
        padding: 0 2;
    }

    SessionListItem {
        width: 100%;
        height: auto;
    }

    SessionListItem .session-row {
        width: 100%;
        height: 1;
    }

    SessionListItem .session-title {
        width: 1fr;
    }

    SessionListItem .session-id {
        width: 9;
        color: $text-muted;
    }

    SessionListItem .session-meta {
        width: 100%;
    }
    """

    def __init__(self, scanner: ClaudeScanner):
        super().__init__()
        self.scanner = scanner
        self.search_engine = SearchEngine(scanner)
        self.projects: list[Project] = []
        self.sessions: list[Session] = []
        self.all_sessions: list[Session] = []  # Unfiltered
        self.selected_project: Project | None = None
        self.search_query: str = ""
        self.teleport_mode: str = "docker"  # Default to docker

    def compose(self) -> ComposeResult:
        """Create the home screen layout."""
        # Search box at top
        yield Input(placeholder="Search titles & messages... (/ to focus)", id="search-input")

        with Horizontal(id="main-container"):
            with Vertical(classes="sidebar"):
                yield Label("Projects", id="projects-header")
                yield ListView(id="project-list")
            with Vertical(classes="content"):
                yield Label("Sessions", id="sessions-header")
                yield ListView(id="session-list")

        # Mode indicator at bottom right (markup=False to show literal brackets)
        yield Static(f"[m] {self.teleport_mode}", id="mode-indicator", markup=False)

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

        # Build all sessions list (excluding agent sessions)
        self.all_sessions = []
        for project in self.projects:
            for session in project.sessions:
                if not session.is_agent:
                    self.all_sessions.append(session)
        self.all_sessions.sort(key=lambda s: s.updated_at, reverse=True)

        # Show sessions
        self._update_session_list()

    def _update_session_list(self) -> None:
        """Update the session list based on selected project and search."""
        session_list = self.query_one("#session-list", ListView)
        session_list.clear()

        # Start with all or project-filtered sessions (excluding agents)
        if self.selected_project:
            base_sessions = [s for s in self.selected_project.sessions if not s.is_agent]
        else:
            base_sessions = self.all_sessions

        # Apply search filter
        if self.search_query:
            # Use SearchEngine for full-text search (titles + message content)
            project_filter = self.selected_project.display_path if self.selected_project else None
            results = self.search_engine.search(
                self.search_query,
                mode="text",
                project_filter=project_filter,
                limit=100,
            )
            # Extract sessions from results (already sorted by relevance)
            self.sessions = [r.session for r in results]
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

    def action_copy_session_id(self) -> None:
        """Copy selected session ID to clipboard."""
        session_list = self.query_one("#session-list", ListView)
        if session_list.index is not None and session_list.index < len(self.sessions):
            session = self.sessions[session_list.index]
            if pyperclip:
                try:
                    pyperclip.copy(session.id)
                    self.app.notify(f"Copied: {session.id[:8]}...")
                    return
                except Exception:
                    pass
            # Fallback: just show the ID
            self.app.notify(f"ID: {session.id}")

    def action_toggle_mode(self) -> None:
        """Toggle between teleport modes."""
        modes = get_mode_names()
        current_idx = modes.index(self.teleport_mode) if self.teleport_mode in modes else 0
        next_idx = (current_idx + 1) % len(modes)
        self.teleport_mode = modes[next_idx]

        # Update the indicator
        indicator = self.query_one("#mode-indicator", Static)
        indicator.update(f"[m] {self.teleport_mode}")
        self.app.notify(f"Teleport mode: {self.teleport_mode}")

    def action_teleport(self) -> None:
        """Teleport to the latest message of selected session."""
        session_list = self.query_one("#session-list", ListView)
        if session_list.index is not None and session_list.index < len(self.sessions):
            session = self.sessions[session_list.index]
            asyncio.create_task(self._do_teleport(session))
        else:
            self.app.notify("Select a session first")

    async def _do_teleport(self, session) -> None:
        """Execute teleport to latest message of session."""
        from one_claude.teleport.restore import FileRestorer

        mode_str = self.teleport_mode
        self.app.notify(f"Teleporting to {session.id[:8]} ({mode_str})...")

        try:
            restorer = FileRestorer(self.scanner)

            # Teleport to latest (no message_uuid = latest)
            teleport_session = await restorer.restore_to_sandbox(
                session,
                message_uuid=None,  # Latest
                mode=mode_str,  # local, docker, or microvm
            )

            files_count = len(teleport_session.files_restored)
            sandbox = teleport_session.sandbox
            working_dir = sandbox.working_dir

            # Suspend TUI and run shell
            with self.app.suspend():
                term = os.environ.get("TERM")
                term_size = shutil.get_terminal_size()

                shell_cmd = sandbox.get_shell_command(
                    term=term,
                    lines=term_size.lines,
                    columns=term_size.columns,
                )

                sys.stderr.write(f"\n🚀 Teleporting to {session.title or session.id[:8]} [{mode_str}]...\n")
                sys.stderr.write(f"   Project: {session.project_display}\n")
                sys.stderr.write(f"   Files restored: {files_count}\n")
                sys.stderr.write(f"   Terminal: {term_size.columns}x{term_size.lines} ({term})\n\n")
                sys.stderr.flush()

                subprocess.run(shell_cmd, cwd=working_dir)

            self.app.notify("Cleaning up...")
            await sandbox.stop()
            self.app.notify(f"Returned from teleport ({files_count} files)")

        except Exception as e:
            self.app.notify(f"Teleport error: {e}", severity="error")
