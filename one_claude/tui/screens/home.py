"""Home screen with conversation list."""

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
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Input, Label, ListItem, ListView, Static

from one_claude.core.models import ConversationPath, Project
from one_claude.core.scanner import ClaudeScanner
from one_claude.index.search import SearchEngine
from one_claude.teleport.executors import get_mode_names


class ConversationListItem(ListItem):
    """A single conversation path item in the list."""

    def __init__(self, path: ConversationPath, is_match: bool = True, next_prefix: str = ""):
        super().__init__()
        self.path = path
        self.is_match = is_match
        self.next_prefix = next_prefix  # Prefix of next item, for tree connection
        # Add dimmed class for non-matching items during search
        if not is_match:
            self.add_class("dimmed")
        # Add branch class for tree children (items with indent prefix)
        prefix = path.tree_prefix or ""
        if prefix and prefix not in ("", "● "):
            self.add_class("branch")

    def compose(self) -> ComposeResult:
        """Create the conversation item display."""
        # Tree prefix + title
        prefix = self.path.tree_prefix or ""
        title = self.path.title or "Untitled"
        display_title = f"{prefix}{title}"

        # Last user message (truncated) for context
        last_msg = self.path.last_user_message or ""

        path_id = self.path.id[:8]

        # Meta line with tree continuation prefix (separate for coloring)
        meta_prefix = self._get_meta_prefix()
        meta_content = f"{self._get_project_name()}  {self._format_time()}  {self.path.message_count} msgs"

        # Add branch indicator if this path has siblings
        if self.path.sibling_leaf_uuids:
            branch_count = len(self.path.sibling_leaf_uuids) + 1
            meta_content += f"  {branch_count} branches"

        with Horizontal(classes="session-row"):
            yield Static(display_title, classes="session-title", markup=False)
            if last_msg:
                yield Static(f"  {last_msg}", classes="session-last-msg", markup=False)
            yield Static(path_id, classes="session-id")
        with Horizontal(classes="session-row"):
            # Use Rich markup to color the tree prefix differently from content
            if meta_prefix:
                safe_content = meta_content.replace("[", "\\[")
                full_meta = f"[white]{meta_prefix}[/white]{safe_content}"
                yield Static(full_meta, classes="session-meta", markup=True)
            else:
                yield Static(meta_content, classes="session-meta", markup=False)

    def _get_meta_prefix(self) -> str:
        """Get the prefix for the metadata line to continue tree lines."""
        prefix = self.path.tree_prefix or ""
        if not prefix:
            return ""

        # Check if this is a root item (starts with ●)
        is_root = prefix.startswith('●')

        # Check if current item's last branch char is └ (no continuation)
        # Only those items need extra │ to connect to children
        ends_with_last_branch = False
        for char in prefix:
            if char == '└':
                ends_with_last_branch = True
            elif char in '●├│':
                ends_with_last_branch = False  # These continue naturally

        # Replace tree drawing chars with their continuation equivalents
        result = []
        # Add leading spaces for root items to align with bullet (● renders wider)
        if is_root:
            result.append(' ')
            result.append(' ')
        for char in prefix:
            if char == '●':
                result.append('│')  # Root continues down
            elif char == '├':
                result.append('│')  # Branch point continues
            elif char == '└':
                result.append(' ')  # Last branch, no continuation
            elif char == '│':
                result.append('│')  # Vertical line continues
            elif char == '─':
                result.append(' ')  # Horizontal line becomes space
            else:
                result.append(char)  # Keep spaces as-is

        meta = list(''.join(result))

        # Only add connection │ if current item ends its branch (└) but has children
        next_prefix = self.next_prefix
        if ends_with_last_branch and next_prefix and len(next_prefix) > len(prefix):
            # Find where the child's branch char starts
            branch_pos = -1
            for i, char in enumerate(next_prefix):
                if char in '├└':
                    branch_pos = i
                    break

            if branch_pos >= 0:
                # Ensure meta is long enough and insert │ at branch position
                while len(meta) <= branch_pos:
                    meta.append(' ')
                meta[branch_pos] = '│'

        return ''.join(meta)

    def _get_project_name(self) -> str:
        """Get short project name."""
        path = self.path.project_display
        parts = path.rstrip("/").split("/")
        return parts[-1] if parts else path

    def _format_time(self) -> str:
        """Format the time as relative."""
        now = datetime.now()
        updated = self.path.updated_at

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
    """Home screen showing all conversation paths."""

    BINDINGS = [
        # Vim navigation (hidden from footer)
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "go_top_prefix", "Top", show=False),
        Binding("G", "go_bottom", "Bottom", show=False),
        Binding("ctrl+u", "page_up", "Page Up", show=False),
        Binding("ctrl+d", "page_down", "Page Down", show=False),
        Binding("ctrl+f", "page_down_full", "Page Down", show=False),
        Binding("ctrl+b", "page_up_full", "Page Up", show=False),
        Binding("tab", "switch_focus", "Switch", show=False),
        Binding("escape", "clear_search", "Clear", show=False),
        # Footer: Navigation
        Binding("enter", "select", "Open"),
        Binding("/", "focus_search", "Search"),
        # Footer: Actions
        Binding("t", "teleport", "Teleport"),
        Binding("c", "copy_session_id", "Copy ID"),
        Binding("m", "toggle_mode", "Mode"),
        # Footer: Gists
        Binding("e", "export_gist", "Export"),
        Binding("i", "import_gist", "Import"),
        Binding("E", "manage_gists", "Gists"),
    ]

    DEFAULT_CSS = """
    HomeScreen {
        background: $background;
    }

    HomeScreen #main-container {
        width: 100%;
        height: 1fr;
    }

    HomeScreen #search-bar {
        margin: 1 2 1 0;
        height: 3;
        width: 90%;
    }

    HomeScreen #search-input {
        width: 90%;
    }

    HomeScreen #mode-indicator {
        dock: right;
        width: auto;
        padding: 1 1;
    }

    HomeScreen #project-list {
        height: 1fr;
    }

    HomeScreen #session-list {
        width: 100%;
        height: 1fr;
    }

    /* Project list styling */
    ProjectListItem {
        height: 1;
        padding: 0 1;
    }

    ProjectListItem:hover {
        background: $surface;
    }

    ProjectListItem.-highlight {
        background: $primary;
        color: $background;
    }

    /* Conversation list */
    ConversationListItem {
        width: 100%;
        height: auto;
        padding: 0 1;
        margin-bottom: 0;
    }

    ConversationListItem:hover {
        background: $surface;
    }

    ConversationListItem.-highlight {
        background: $panel;
    }

    ConversationListItem .session-row {
        width: 100%;
        height: 1;
    }

    ConversationListItem .session-title {
        width: 1fr;
        text-style: bold;
    }

    ConversationListItem .session-id {
        width: 9;
        color: $text-muted;
    }

    ConversationListItem .session-last-msg {
        color: $text-muted;
        max-width: 30;
    }

    ConversationListItem .session-meta {
        width: 100%;
        color: $text-muted;
    }

    ConversationListItem.dimmed {
        opacity: 0.4;
    }

    ConversationListItem.dimmed .session-title {
        color: $text-muted;
        text-style: none;
    }

    """

    def __init__(self, scanner: ClaudeScanner):
        super().__init__()
        self.scanner = scanner
        self.search_engine = SearchEngine(scanner)
        self.search_engine.start_preload()  # Background load message trees
        self.projects: list[Project] = []
        self.paths: list[ConversationPath] = []
        self.all_paths: list[ConversationPath] = []  # Unfiltered
        self.selected_project: Project | None = None
        self.search_query: str = ""
        self.teleport_mode: str = "docker"  # Default to docker
        self._g_pressed: bool = False  # For gg command

    def compose(self) -> ComposeResult:
        """Create the home screen layout."""
        with Horizontal(id="main-container"):
            with Vertical(classes="sidebar"):
                yield Label("Projects", id="projects-header")
                yield ListView(id="project-list")
            with Vertical(classes="content"):
                with Horizontal(id="search-bar"):
                    yield Input(placeholder="Search... (/ to focus)", id="search-input")
                    yield Static(f"[#00d4ff]m[/] {self.teleport_mode}", id="mode-indicator", markup=True)
                yield Label("Conversations", id="sessions-header")
                yield ListView(id="session-list")

        yield Footer()

    def on_mount(self) -> None:
        """Load conversations on mount."""
        self.refresh_conversations()
        # Focus conversation list by default
        self.query_one("#session-list", ListView).focus()

        # Check for missing tools in local mode
        self._check_local_tools()

    def refresh_conversations(self) -> None:
        """Refresh the conversation list."""
        # Still need projects for the sidebar
        self.projects = self.scanner.scan_all()

        # Populate project list
        project_list = self.query_one("#project-list", ListView)
        project_list.clear()
        project_list.append(ProjectListItem(None, "All"))
        for project in self.projects:
            project_list.append(ProjectListItem(project))

        # Get conversation paths (uses tree cache from search engine preload)
        tree_cache = self.search_engine._tree_cache
        self.all_paths = self.scanner.scan_conversation_paths(tree_cache=tree_cache)

        # Show conversations
        self._update_conversation_list()

    def _update_conversation_list(self) -> None:
        """Update the conversation list based on selected project and search."""
        session_list = self.query_one("#session-list", ListView)
        session_list.clear()

        # Start with all or project-filtered paths
        if self.selected_project:
            base_paths = [
                p for p in self.all_paths
                if p.project_display == self.selected_project.display_path
            ]
        else:
            base_paths = self.all_paths

        # Track which paths match the search (for highlighting)
        matching_ids: set[str] = set()

        if self.search_query:
            query_lower = self.search_query.lower()
            tree_cache = self.search_engine._tree_cache

            for p in base_paths:
                # Title match
                if query_lower in p.title.lower():
                    matching_ids.add(p.id)
                    continue

                # Content match - search messages in tree cache
                for jsonl_file in p.jsonl_files:
                    session_id = jsonl_file.stem
                    tree = tree_cache.get(session_id)
                    if tree:
                        # Get the linear path for this conversation
                        path_msgs = tree.get_linear_path(p.leaf_uuid)
                        for msg in path_msgs:
                            if msg.text_content and query_lower in msg.text_content.lower():
                                matching_ids.add(p.id)
                                break
                        else:
                            continue
                        break

        # Filter and display paths
        if self.search_query:
            # Group paths into trees (root + children)
            trees: list[list[ConversationPath]] = []
            current_tree: list[ConversationPath] = []

            for path in base_paths:
                # Root paths have no indent (empty or ● prefix)
                is_root = path.tree_prefix in ("", "● ")

                if is_root:
                    if current_tree:
                        trees.append(current_tree)
                    current_tree = [path]
                else:
                    current_tree.append(path)

            if current_tree:
                trees.append(current_tree)

            # Only show trees that have at least one match
            self.paths = []
            for tree in trees:
                if any(p.id in matching_ids for p in tree):
                    self.paths.extend(tree)
        else:
            self.paths = list(base_paths)

        for i, path in enumerate(self.paths):
            # If searching, dim non-matching paths (but still show them if tree has a match)
            is_match = not self.search_query or path.id in matching_ids
            # Get next item's prefix for tree connection
            next_prefix = ""
            if i + 1 < len(self.paths):
                next_prefix = self.paths[i + 1].tree_prefix or ""
            session_list.append(ConversationListItem(path, is_match=is_match, next_prefix=next_prefix))

        # Update header
        header = self.query_one("#sessions-header", Label)
        count = len(self.paths)
        if self.search_query:
            match_count = len(matching_ids)
            if self.selected_project:
                name = self.selected_project.display_path.rstrip("/").split("/")[-1]
                header.update(f"Conversations - {name} ({match_count}/{count} match)")
            else:
                header.update(f"Conversations ({match_count}/{count} match)")
        elif self.selected_project:
            name = self.selected_project.display_path.rstrip("/").split("/")[-1]
            header.update(f"Conversations - {name} ({count})")
        else:
            header.update(f"Conversations ({count})")

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle search input changes."""
        if event.input.id == "search-input":
            self.search_query = event.value
            self._update_conversation_list()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle search input submission (Enter)."""
        if event.input.id == "search-input":
            session_list = self.query_one("#session-list", ListView)
            session_list.focus()
            if self.paths:
                session_list.index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle list selection."""
        if isinstance(event.item, ProjectListItem):
            self.selected_project = event.item.project
            self._update_conversation_list()
        elif isinstance(event.item, ConversationListItem):
            # Open session screen with conversation path
            from one_claude.tui.screens.session import SessionScreen

            self.app.push_screen(SessionScreen(event.item.path, self.scanner))

    def action_cursor_down(self) -> None:
        """Move cursor down."""
        self._g_pressed = False
        focused = self.focused
        if isinstance(focused, ListView):
            focused.action_cursor_down()

    def action_cursor_up(self) -> None:
        """Move cursor up."""
        self._g_pressed = False
        focused = self.focused
        if isinstance(focused, ListView):
            focused.action_cursor_up()

    def action_go_top_prefix(self) -> None:
        """Handle g key - go to top on gg."""
        if self._g_pressed:
            # gg - go to top
            self._g_pressed = False
            focused = self.focused
            if isinstance(focused, ListView):
                focused.index = 0
        else:
            self._g_pressed = True

    def action_go_bottom(self) -> None:
        """Go to bottom of list (G)."""
        self._g_pressed = False
        focused = self.focused
        if isinstance(focused, ListView) and focused.children:
            focused.index = len(focused.children) - 1

    def action_page_up(self) -> None:
        """Page up half screen (ctrl+u)."""
        self._g_pressed = False
        focused = self.focused
        if isinstance(focused, ListView):
            # Move up ~10 items (half page)
            focused.index = max(0, (focused.index or 0) - 10)

    def action_page_down(self) -> None:
        """Page down half screen (ctrl+d)."""
        self._g_pressed = False
        focused = self.focused
        if isinstance(focused, ListView) and focused.children:
            focused.index = min(len(focused.children) - 1, (focused.index or 0) + 10)

    def action_page_up_full(self) -> None:
        """Page up full screen (ctrl+b)."""
        self._g_pressed = False
        focused = self.focused
        if isinstance(focused, ListView):
            focused.index = max(0, (focused.index or 0) - 20)

    def action_page_down_full(self) -> None:
        """Page down full screen (ctrl+f)."""
        self._g_pressed = False
        focused = self.focused
        if isinstance(focused, ListView) and focused.children:
            focused.index = min(len(focused.children) - 1, (focused.index or 0) + 20)

    def action_select(self) -> None:
        """Select current item."""
        focused = self.focused
        if isinstance(focused, ListView):
            focused.action_select_cursor()

    def action_switch_focus(self) -> None:
        """Switch focus between project and conversation list."""
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
            self._update_conversation_list()
            self.query_one("#session-list", ListView).focus()
        elif self.search_query:
            # Clear search from session list
            search_input.value = ""
            self.search_query = ""
            self._update_conversation_list()

    def _copy_to_clipboard(self, text: str) -> tuple[bool, str]:
        """Copy text to clipboard. Returns (success, error_hint)."""
        # macOS: pbcopy
        if sys.platform == "darwin":
            if shutil.which("pbcopy"):
                try:
                    subprocess.run(["pbcopy"], input=text.encode(), check=True)
                    return True, ""
                except Exception:
                    pass
            return False, "pbcopy not found (should be built-in)"

        # Linux: try wl-copy (Wayland) then xclip (X11)
        if shutil.which("wl-copy"):
            try:
                subprocess.run(["wl-copy", text], check=True)
                return True, ""
            except Exception:
                pass

        if shutil.which("xclip"):
            try:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=text.encode(),
                    check=True,
                )
                return True, ""
            except Exception:
                pass

        # Try pyperclip as last resort
        if pyperclip:
            try:
                pyperclip.copy(text)
                return True, ""
            except Exception:
                pass

        # Suggest install based on environment
        if os.environ.get("WAYLAND_DISPLAY"):
            return False, "install wl-clipboard"
        else:
            return False, "install xclip"

    def action_copy_session_id(self) -> None:
        """Copy selected conversation ID to clipboard."""
        session_list = self.query_one("#session-list", ListView)
        if session_list.index is not None and session_list.index < len(self.paths):
            path = self.paths[session_list.index]
            success, hint = self._copy_to_clipboard(path.id)
            if success:
                self.app.notify(f"Copied: {path.id[:8]}...")
            else:
                self.app.notify(f"{path.id} ({hint})")

    def _check_local_tools(self) -> None:
        """Check for missing tools and notify user."""
        if not shutil.which("tmux"):
            self.app.notify("Local mode: tmux not found, will run claude directly", severity="warning")

    def action_toggle_mode(self) -> None:
        """Toggle between teleport modes."""
        modes = get_mode_names()
        current_idx = modes.index(self.teleport_mode) if self.teleport_mode in modes else 0
        next_idx = (current_idx + 1) % len(modes)
        self.teleport_mode = modes[next_idx]

        # Update the indicator
        indicator = self.query_one("#mode-indicator", Static)
        indicator.update(f"[#00d4ff]m[/] {self.teleport_mode}")

    def action_teleport(self) -> None:
        """Teleport to the selected conversation."""
        session_list = self.query_one("#session-list", ListView)
        if session_list.index is not None and session_list.index < len(self.paths):
            path = self.paths[session_list.index]
            asyncio.create_task(self._do_teleport(path))
        else:
            self.app.notify("Select a conversation first")

    async def _do_teleport(self, path: ConversationPath) -> None:
        """Execute teleport to a conversation path."""
        from one_claude.teleport.restore import FileRestorer

        mode_str = self.teleport_mode
        self.app.notify(f"Teleporting to {path.id[:8]} ({mode_str})...")

        try:
            restorer = FileRestorer(self.scanner)

            # Need to get a Session object for the restorer
            # Use the newest JSONL file in the chain
            if path.jsonl_files:
                session_id = path.jsonl_files[-1].stem
                session = self.scanner.get_session_by_id(session_id)
                if not session:
                    self.app.notify("Session not found", severity="error")
                    return
            else:
                self.app.notify("No JSONL files in path", severity="error")
                return

            # Teleport to the leaf message
            teleport_session = await restorer.restore_to_sandbox(
                session,
                message_uuid=path.leaf_uuid,
                mode=mode_str,
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

                sys.stderr.write(f"\n🚀 Teleporting to {path.title or path.id[:8]} [{mode_str}]...\n")
                sys.stderr.write(f"   Project: {path.project_display}\n")
                sys.stderr.write(f"   Files restored: {files_count}\n")
                sys.stderr.write(f"   Terminal: {term_size.columns}x{term_size.lines} ({term})\n\n")
                sys.stderr.flush()

                subprocess.run(shell_cmd, cwd=working_dir)

            self.app.notify("Cleaning up...")
            await sandbox.stop()
            self.app.notify(f"Returned from teleport ({files_count} files)")

        except Exception as e:
            self.app.notify(f"Teleport error: {e}", severity="error")

    def action_export_gist(self) -> None:
        """Export selected session to gist."""
        session_list = self.query_one("#session-list", ListView)
        if session_list.index is not None and session_list.index < len(self.paths):
            path = self.paths[session_list.index]
            asyncio.create_task(self._do_export_gist(path))
        else:
            self.app.notify("Select a conversation first")

    async def _do_export_gist(self, path: ConversationPath) -> None:
        """Execute gist export."""
        from one_claude.gist.api import get_token, start_device_flow
        from one_claude.gist.exporter import SessionExporter
        from one_claude.tui.screens.gist_modals import AuthModal

        # Check auth first
        if not get_token():
            auth_info, error = await start_device_flow()
            if error:
                self.app.notify(f"Auth failed: {error}", severity="error")
                return
            # Show auth modal
            authorized = await self.app.push_screen_wait(
                AuthModal(
                    auth_info["verification_uri"],
                    auth_info["user_code"],
                    auth_info["device_code"],
                    auth_info["interval"],
                )
            )
            if not authorized:
                self.app.notify("Auth cancelled")
                return

        self.app.notify("Exporting to gist...")

        exporter = SessionExporter(self.scanner)
        result = await exporter.export_full_session(path)

        if result.success and result.gist_url:
            from one_claude.tui.screens.gist_modals import ExportResultModal

            await self.app.push_screen_wait(
                ExportResultModal(result.gist_url, result.message_count, result.checkpoint_count)
            )
        else:
            self.app.notify(f"Export failed: {result.error}", severity="error")

    def action_import_gist(self) -> None:
        """Show import modal."""
        from one_claude.tui.screens.gist_modals import ImportModal

        async def do_import():
            gist_url = await self.app.push_screen_wait(ImportModal())
            if gist_url:
                await self._do_import_gist(gist_url)

        asyncio.create_task(do_import())

    async def _do_import_gist(self, gist_url: str) -> None:
        """Execute gist import."""
        from one_claude.gist.importer import SessionImporter

        self.app.notify("Importing from gist...")

        importer = SessionImporter(self.scanner.claude_dir)
        result = await importer.import_from_gist(gist_url)

        if result.success:
            msg = f"Imported {result.message_count} msgs, {result.checkpoint_count} checkpoints"
            self.app.notify(msg)
            self.app.notify(f"Session: {result.session_id[:8] if result.session_id else 'unknown'}")
            self.refresh_conversations()
        else:
            self.app.notify(f"Import failed: {result.error}", severity="error")

    def action_manage_gists(self) -> None:
        """Show exports management screen."""
        from one_claude.tui.screens.exports import ExportsScreen

        self.app.push_screen(ExportsScreen())
