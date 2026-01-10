"""Session detail screen showing the conversation."""

import os
import shutil
from datetime import datetime

try:
    import pyperclip
except ImportError:
    pyperclip = None  # type: ignore

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer
from textual.message import Message as TextualMessage
from textual.screen import Screen
from textual.widgets import Collapsible, Input, Label, Static

from one_claude.core.models import Message, MessageType, Session
from one_claude.core.scanner import ClaudeScanner


class CheckpointSelected(TextualMessage):
    """Message sent when a checkpoint is clicked."""

    def __init__(self, checkpoint: Message) -> None:
        self.checkpoint = checkpoint
        super().__init__()


class MessageWidget(Static):
    """Widget displaying a single message."""

    def __init__(self, message: Message, show_thinking: bool = False):
        self.message = message
        self.show_thinking = show_thinking

        # Determine CSS class based on message type
        if message.type == MessageType.USER:
            classes = "message-container message-user"
        elif message.type == MessageType.ASSISTANT:
            classes = "message-container message-assistant"
        elif message.type == MessageType.SUMMARY:
            classes = "message-container message-summary"
        elif message.type == MessageType.FILE_HISTORY_SNAPSHOT:
            classes = "message-container message-checkpoint"
        else:
            classes = "message-container"

        super().__init__(classes=classes)

    def on_click(self) -> None:
        """Handle click on message widget."""
        if self.message.type == MessageType.FILE_HISTORY_SNAPSHOT:
            # Notify parent screen of checkpoint selection
            self.post_message(CheckpointSelected(self.message))

    def compose(self) -> ComposeResult:
        """Compose the message display."""
        # Header
        header_text = self._build_header()
        yield Static(header_text, classes="message-header")

        # Content
        if self.message.type == MessageType.USER:
            yield self._render_user_content()
        elif self.message.type == MessageType.ASSISTANT:
            yield from self._render_assistant_content()
        elif self.message.type == MessageType.SUMMARY:
            yield Static(self.message.summary_text or "", classes="message-content")
        elif self.message.type == MessageType.FILE_HISTORY_SNAPSHOT:
            yield from self._render_checkpoint_content()

    def _build_header(self) -> str:
        """Build the message header."""
        if self.message.type == MessageType.USER:
            label = "USER"
            if self.message.user_type:
                label += f" ({self.message.user_type.value})"
        elif self.message.type == MessageType.ASSISTANT:
            label = "ASSISTANT"
            if self.message.model:
                label += f" ({self.message.model})"
        elif self.message.type == MessageType.SUMMARY:
            label = "SUMMARY"
        elif self.message.type == MessageType.FILE_HISTORY_SNAPSHOT:
            label = "CHECKPOINT"
        else:
            label = self.message.type.value.upper()

        time_str = self._format_time()
        return f"{label}  {time_str}"

    def _format_time(self) -> str:
        """Format timestamp with h/m/d breakdown."""
        ts = self.message.timestamp
        now = datetime.now()

        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)

        diff = now - ts
        seconds = int(diff.total_seconds())

        if seconds < 60:
            return "just now"

        minutes = seconds // 60
        hours = minutes // 60
        days = hours // 24

        if days > 7:
            return ts.strftime("%Y-%m-%d %H:%M")
        elif days > 0:
            remaining_hours = hours % 24
            if remaining_hours > 0:
                return f"{days}d {remaining_hours}h ago"
            return f"{days}d ago"
        elif hours > 0:
            remaining_mins = minutes % 60
            if remaining_mins > 0:
                return f"{hours}h {remaining_mins}m ago"
            return f"{hours}h ago"
        else:
            return f"{minutes}m ago"

    def _render_user_content(self) -> Static:
        """Render user message content."""
        content = self.message.text_content

        # If this is a tool result, show it differently
        if self.message.tool_result:
            result = self.message.tool_result
            content = f"Tool Result ({result.tool_use_id[:8]}...):\n{result.content[:500]}"
            if len(result.content) > 500:
                content += "\n... (truncated)"

        return Static(content, classes="message-content")

    def _render_assistant_content(self) -> ComposeResult:
        """Render assistant message content."""
        # Text content
        if self.message.text_content:
            yield Static(self.message.text_content, classes="message-content")

        # Tool uses
        for tool_use in self.message.tool_uses:
            tool_display = self._format_tool_use(tool_use)
            yield Static(tool_display, classes="tool-use")

        # Thinking (if enabled)
        if self.show_thinking and self.message.thinking:
            thinking_text = f"Thinking: {self.message.thinking.content[:200]}..."
            yield Static(thinking_text, classes="thinking")

    def _render_checkpoint_content(self) -> ComposeResult:
        """Render file history checkpoint content."""
        snapshot = self.message.snapshot_data
        if isinstance(snapshot, str):
            # Parse JSON string if needed
            import json
            try:
                snapshot = json.loads(snapshot)
            except (json.JSONDecodeError, TypeError):
                snapshot = {}

        if isinstance(snapshot, dict):
            file_count = len(snapshot)
            if file_count > 0:
                yield Static(f"Saved {file_count} file(s)", classes="checkpoint-info")
            else:
                yield Static("Checkpoint saved", classes="checkpoint-info")
        else:
            yield Static("Checkpoint saved", classes="checkpoint-info")

    def _format_tool_use(self, tool_use) -> str:
        """Format a tool use for display."""
        name = tool_use.name
        inputs = tool_use.input

        # Format based on tool type
        if name == "Read":
            path = inputs.get("file_path", "")
            return f"Read: {path}"
        elif name == "Write":
            path = inputs.get("file_path", "")
            return f"Write: {path}"
        elif name == "Edit":
            path = inputs.get("file_path", "")
            return f"Edit: {path}"
        elif name == "Bash":
            cmd = inputs.get("command", "")[:60]
            return f"Bash: {cmd}"
        elif name == "Grep":
            pattern = inputs.get("pattern", "")
            return f"Grep: {pattern}"
        elif name == "Glob":
            pattern = inputs.get("pattern", "")
            return f"Glob: {pattern}"
        elif name == "Task":
            desc = inputs.get("description", "")
            return f"Task: {desc}"
        else:
            return f"{name}: {str(inputs)[:50]}"


class SessionScreen(Screen):
    """Screen showing session details and conversation."""

    BINDINGS = [
        Binding("escape", "cancel_or_back", "Back"),
        Binding("j", "scroll_down", "Down", show=False),
        Binding("k", "scroll_up", "Up", show=False),
        Binding("ctrl+f", "page_down", "Page Down", show=False),
        Binding("ctrl+u", "page_up", "Page Up", show=False),
        Binding("g", "scroll_top", "Top", show=False),
        Binding("G", "scroll_bottom", "Bottom", show=False),
        Binding("t", "teleport", "Teleport"),
        Binding("/", "start_search", "/ Search"),
        Binding("n", "next_match", "n Next", show=False),
        Binding("N", "prev_match", "N Prev", show=False),
        Binding("tab", "next_checkpoint", "Tab Next CP"),
        Binding("shift+tab", "prev_checkpoint", "S-Tab Prev CP", show=False),
        Binding("c", "copy_session_id", "Copy ID"),
    ]

    DEFAULT_CSS = """
    SessionScreen #session-header-row {
        width: 100%;
        height: 1;
    }

    SessionScreen #session-title {
        width: 1fr;
    }

    SessionScreen #session-id {
        width: auto;
        color: $text-muted;
    }

    SessionScreen .message-summary {
        background: $warning-muted;
        border: solid $warning;
    }

    SessionScreen .message-checkpoint {
        background: $success-muted;
        border: solid $success;
    }

    SessionScreen .message-checkpoint:hover {
        background: $success;
    }

    SessionScreen .message-checkpoint.selected {
        background: $success;
        border: double $success;
    }

    SessionScreen .checkpoint-info {
        color: $success;
    }

    SessionScreen #search-input {
        dock: top;
        display: none;
        margin: 0 1;
    }

    SessionScreen #search-input.visible {
        display: block;
    }

    SessionScreen .search-match {
        background: $warning;
    }

    SessionScreen .agent-session {
        margin-left: 2;
        padding: 0 1;
    }

    SessionScreen .agent-title {
        color: $text-muted;
    }
    """

    def __init__(self, session: Session, scanner: ClaudeScanner):
        super().__init__()
        self.session = session
        self.scanner = scanner
        self.displayed_count: int = 0
        self.search_query: str = ""
        self.match_widgets: list[MessageWidget] = []
        self.current_match_index: int = -1
        self.selected_checkpoint: Message | None = None
        self.selected_checkpoint_widget: MessageWidget | None = None
        self.checkpoint_widgets: list[MessageWidget] = []
        self.current_checkpoint_index: int = -1

    def compose(self) -> ComposeResult:
        """Create the session screen layout."""
        # Search input (hidden by default)
        yield Input(placeholder="/search...", id="search-input")

        # Header with session info and ID
        with Horizontal(id="session-header-row"):
            yield Static(
                f" {self.session.title or 'Untitled Session'}",
                id="session-title",
            )
            yield Static(
                self.session.id[:8],
                id="session-id",
            )
        yield Static(
            f"  {self.session.project_display}",
            id="session-meta",
        )

        # Agent sessions (if any)
        if self.session.child_agent_ids:
            agent_count = len(self.session.child_agent_ids)
            with Collapsible(title=f"Subagents ({agent_count})", collapsed=True, id="agents-collapsible"):
                for agent_id in self.session.child_agent_ids:
                    agent_session = self.scanner.get_session_by_id(agent_id)
                    if agent_session:
                        short_id = agent_id.replace("agent-", "")[:7]
                        title = agent_session.title or "Untitled"
                        if len(title) > 60:
                            title = title[:57] + "..."
                        yield Static(f"  {short_id}: {title}", classes="agent-session agent-title")

        # Message list
        yield ScrollableContainer(id="message-container")

    def on_mount(self) -> None:
        """Load messages on mount."""
        self._load_messages()
        container = self.query_one("#message-container", ScrollableContainer)
        # Focus container so keybindings work immediately
        container.focus()
        # Scroll to bottom and select last checkpoint
        self.call_after_refresh(self._scroll_to_end_and_select_last)

    def _load_messages(self) -> None:
        """Load and display messages."""
        container = self.query_one("#message-container", ScrollableContainer)

        # Load the message tree
        message_tree = self.scanner.load_session_messages(self.session)

        # Get ALL messages chronologically (includes summaries and compacted chains)
        all_messages = message_tree.all_messages()

        # Filter to displayable messages
        display_types = (MessageType.USER, MessageType.ASSISTANT, MessageType.SUMMARY, MessageType.FILE_HISTORY_SNAPSHOT)
        display_messages = [msg for msg in all_messages if msg.type in display_types]

        # Count checkpoints in displayed messages
        checkpoint_count = sum(1 for m in display_messages if m.type == MessageType.FILE_HISTORY_SNAPSHOT)

        # Update header with actual count
        self.displayed_count = len(display_messages)
        meta = self.query_one("#session-meta", Static)
        cp_str = f"  {checkpoint_count} checkpoints" if checkpoint_count else ""
        meta.update(f"  {self.session.project_display}  {self.displayed_count} messages{cp_str}")

        # Create message widgets and track checkpoints
        self.checkpoint_widgets = []
        for msg in display_messages:
            widget = MessageWidget(msg, show_thinking=False)
            container.mount(widget)
            if msg.type == MessageType.FILE_HISTORY_SNAPSHOT:
                self.checkpoint_widgets.append(widget)

    def _scroll_to_end_and_select_last(self) -> None:
        """Scroll to bottom and select last checkpoint."""
        container = self.query_one("#message-container", ScrollableContainer)
        container.scroll_end(animate=False)
        # Select last checkpoint if any exist
        if self.checkpoint_widgets:
            last_widget = self.checkpoint_widgets[-1]
            self.current_checkpoint_index = len(self.checkpoint_widgets) - 1
            last_widget.add_class("selected")
            self.selected_checkpoint_widget = last_widget
            self.selected_checkpoint = last_widget.message

    def action_cancel_or_back(self) -> None:
        """Cancel search or go back to home screen."""
        search_input = self.query_one("#search-input", Input)
        if search_input.has_class("visible"):
            # Hide search and clear highlights
            search_input.remove_class("visible")
            search_input.value = ""
            self._clear_highlights()
            self.query_one("#message-container", ScrollableContainer).focus()
        else:
            self.app.pop_screen()

    def action_scroll_down(self) -> None:
        """Scroll down."""
        container = self.query_one("#message-container", ScrollableContainer)
        container.scroll_down()

    def action_scroll_up(self) -> None:
        """Scroll up."""
        container = self.query_one("#message-container", ScrollableContainer)
        container.scroll_up()

    def action_scroll_top(self) -> None:
        """Scroll to top."""
        container = self.query_one("#message-container", ScrollableContainer)
        container.scroll_home()

    def action_scroll_bottom(self) -> None:
        """Scroll to bottom."""
        container = self.query_one("#message-container", ScrollableContainer)
        container.scroll_end()

    def action_page_down(self) -> None:
        """Scroll down by half a page."""
        container = self.query_one("#message-container", ScrollableContainer)
        container.scroll_page_down()

    def action_page_up(self) -> None:
        """Scroll up by half a page."""
        container = self.query_one("#message-container", ScrollableContainer)
        container.scroll_page_up()

    def on_checkpoint_selected(self, event: CheckpointSelected) -> None:
        """Handle checkpoint selection."""
        self._select_checkpoint_widget(event.checkpoint)

    def _select_checkpoint_widget(self, checkpoint: Message) -> None:
        """Select a checkpoint and update UI."""
        # Clear previous selection
        if self.selected_checkpoint_widget:
            self.selected_checkpoint_widget.remove_class("selected")

        # Find and select the widget
        for i, widget in enumerate(self.checkpoint_widgets):
            if widget.message.uuid == checkpoint.uuid:
                widget.add_class("selected")
                widget.scroll_visible()
                self.selected_checkpoint_widget = widget
                self.current_checkpoint_index = i
                break

        self.selected_checkpoint = checkpoint
        cp_num = self.current_checkpoint_index + 1
        total = len(self.checkpoint_widgets)
        self.app.notify(f"Checkpoint {cp_num}/{total} - press 't' to teleport")

    def action_next_checkpoint(self) -> None:
        """Go to next checkpoint."""
        if not self.checkpoint_widgets:
            self.app.notify("No checkpoints in this session")
            return
        self.current_checkpoint_index = (self.current_checkpoint_index + 1) % len(self.checkpoint_widgets)
        widget = self.checkpoint_widgets[self.current_checkpoint_index]
        self._select_checkpoint_widget(widget.message)

    def action_prev_checkpoint(self) -> None:
        """Go to previous checkpoint."""
        if not self.checkpoint_widgets:
            self.app.notify("No checkpoints in this session")
            return
        self.current_checkpoint_index = (self.current_checkpoint_index - 1) % len(self.checkpoint_widgets)
        widget = self.checkpoint_widgets[self.current_checkpoint_index]
        self._select_checkpoint_widget(widget.message)

    def action_teleport(self) -> None:
        """Launch teleport directly from selected checkpoint."""
        import asyncio
        if self.selected_checkpoint:
            asyncio.create_task(self._do_teleport())
        else:
            self.app.notify("Select a checkpoint first (Tab to navigate)")

    async def _do_teleport(self) -> None:
        """Execute the teleport and launch shell."""
        import subprocess
        import sys
        from one_claude.teleport.restore import FileRestorer

        self.app.notify("Restoring files...")

        try:
            restorer = FileRestorer(self.scanner)
            # Get message_uuid from checkpoint (strip "checkpoint-" prefix)
            message_uuid = self.selected_checkpoint.uuid.replace("checkpoint-", "")

            teleport_session = await restorer.restore_to_sandbox(
                self.session,
                message_uuid=message_uuid,
            )

            files_count = len(teleport_session.files_restored)
            if files_count == 0:
                self.app.notify("No files to restore at this checkpoint", severity="warning")
                return

            # Get shell command and working directory
            sandbox = teleport_session.sandbox
            working_dir = sandbox.working_dir

            # Suspend TUI and run shell (like k9s exec)
            mode = "sandbox" if sandbox.isolated else "local"
            with self.app.suspend():
                # Get terminal info after TUI is suspended (real terminal is restored)
                term = os.environ.get("TERM")
                term_size = shutil.get_terminal_size()

                shell_cmd = sandbox.get_shell_command(
                    term=term,
                    lines=term_size.lines,
                    columns=term_size.columns,
                )

                sys.stderr.write(f"\n🚀 Teleporting to checkpoint [{mode}]...\n")
                sys.stderr.write(f"   Working directory: {working_dir}\n")
                sys.stderr.write(f"   Files restored: {files_count}\n")
                sys.stderr.write(f"   Terminal: {term_size.columns}x{term_size.lines} ({term})\n")
                sys.stderr.write(f"\n   Layout: Claude Code (left) | Terminal (right)\n")
                sys.stderr.write(f"   Exit tmux with: Ctrl-b d (detach) or exit both panes\n\n")
                sys.stderr.flush()

                # Run tmux session in foreground
                subprocess.run(shell_cmd, cwd=working_dir)

            # Cleanup temp directory after shell exits
            self.app.notify("Cleaning up...")
            await sandbox.stop()

            # TUI resumes here automatically
            self.app.notify(f"Returned from teleport ({files_count} files)")

        except Exception as e:
            self.app.notify(f"Teleport error: {e}", severity="error")

    def action_start_search(self) -> None:
        """Show search input."""
        search_input = self.query_one("#search-input", Input)
        search_input.add_class("visible")
        search_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle search submission."""
        if event.input.id == "search-input":
            self._perform_search(event.value)
            # Hide search input and return focus to container for n/N navigation
            search_input = self.query_one("#search-input", Input)
            search_input.remove_class("visible")
            self.query_one("#message-container", ScrollableContainer).focus()

    def _perform_search(self, query: str) -> None:
        """Search for query in messages."""
        self._clear_highlights()
        self.search_query = query.lower()
        self.match_widgets = []
        self.current_match_index = -1

        if not self.search_query:
            return

        # Find all matching message widgets
        container = self.query_one("#message-container", ScrollableContainer)
        for widget in container.query(MessageWidget):
            msg = widget.message
            # Search in text content and summary
            searchable = (msg.text_content or "") + (msg.summary_text or "")
            if self.search_query in searchable.lower():
                self.match_widgets.append(widget)
                widget.add_class("search-match")

        # Go to first match
        if self.match_widgets:
            self.current_match_index = 0
            self._scroll_to_current_match()
            self.app.notify(f"Match 1 of {len(self.match_widgets)}")
        else:
            self.app.notify(f"No matches for '{query}'")

    def action_next_match(self) -> None:
        """Go to next search match."""
        if not self.match_widgets:
            return
        self.current_match_index = (self.current_match_index + 1) % len(self.match_widgets)
        self._scroll_to_current_match()
        self.app.notify(f"Match {self.current_match_index + 1} of {len(self.match_widgets)}")

    def action_prev_match(self) -> None:
        """Go to previous search match."""
        if not self.match_widgets:
            return
        self.current_match_index = (self.current_match_index - 1) % len(self.match_widgets)
        self._scroll_to_current_match()
        self.app.notify(f"Match {self.current_match_index + 1} of {len(self.match_widgets)}")

    def _scroll_to_current_match(self) -> None:
        """Scroll to show the current match."""
        if 0 <= self.current_match_index < len(self.match_widgets):
            widget = self.match_widgets[self.current_match_index]
            widget.scroll_visible()

    def _clear_highlights(self) -> None:
        """Clear all search highlights."""
        container = self.query_one("#message-container", ScrollableContainer)
        for widget in container.query(".search-match"):
            widget.remove_class("search-match")
        self.match_widgets = []
        self.current_match_index = -1

    def action_copy_session_id(self) -> None:
        """Copy session ID to clipboard."""
        if pyperclip:
            try:
                pyperclip.copy(self.session.id)
                self.app.notify(f"Copied: {self.session.id[:8]}...")
                return
            except Exception:
                pass
        # Fallback: just show the ID
        self.app.notify(f"ID: {self.session.id}")
