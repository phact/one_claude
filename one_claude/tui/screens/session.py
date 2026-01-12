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
from textual.widgets import Collapsible, Footer, Input, Label, Static

from one_claude.core.models import ConversationPath, Message, MessageTree, MessageType
from one_claude.core.scanner import ClaudeScanner


class MessageClicked(TextualMessage):
    """Message sent when a message widget is clicked."""

    def __init__(self, message: Message) -> None:
        self.message = message
        super().__init__()


class MessageWidget(Static):
    """Widget displaying a single message."""

    def __init__(
        self,
        message: Message,
        turn_number: int = 0,
        show_thinking: bool = False,
        has_branch: bool = False,  # True if there's a branch at this point
    ):
        self.message = message
        self.turn_number = turn_number
        self.show_thinking = show_thinking
        self.has_branch = has_branch

        # Determine CSS class based on message type
        if message.type == MessageType.USER:
            classes = "message-container message-user"
        elif message.type == MessageType.ASSISTANT:
            classes = "message-container message-assistant"
        elif message.type == MessageType.SUMMARY:
            classes = "message-container message-summary"
        elif message.type == MessageType.FILE_HISTORY_SNAPSHOT:
            classes = "message-container message-checkpoint"
        elif message.type == MessageType.SYSTEM:
            classes = "message-container message-system"
        else:
            classes = "message-container"

        super().__init__(classes=classes)

    def on_click(self) -> None:
        """Handle click on message widget."""
        self.post_message(MessageClicked(self.message))

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
            yield Static(self.message.summary_text or "", classes="message-content", markup=False)
        elif self.message.type == MessageType.FILE_HISTORY_SNAPSHOT:
            yield from self._render_checkpoint_content()
        elif self.message.type == MessageType.SYSTEM:
            yield from self._render_system_content()

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
        elif self.message.type == MessageType.SYSTEM:
            label = "SYSTEM"
            if self.message.system_subtype:
                label += f" ({self.message.system_subtype})"
        else:
            label = self.message.type.value.upper()

        time_str = self._format_time()

        # Branch indicator
        branch_indicator = "  branch exists [b] ↱" if self.has_branch else ""

        return f"#{self.turn_number}  {label}  {time_str}{branch_indicator}"

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

        return Static(content, classes="message-content", markup=False)

    def _render_assistant_content(self) -> ComposeResult:
        """Render assistant message content."""
        # Text content
        if self.message.text_content:
            yield Static(self.message.text_content, classes="message-content", markup=False)

        # Tool uses
        for tool_use in self.message.tool_uses:
            tool_display = self._format_tool_use(tool_use)
            yield Static(tool_display, classes="tool-use", markup=False)

        # Thinking (if enabled)
        if self.show_thinking and self.message.thinking:
            yield Static(self.message.thinking.content, classes="thinking", markup=False)

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

    def _render_system_content(self) -> ComposeResult:
        """Render system message content."""
        data = self.message.system_data or {}
        hook_count = data.get("hookCount", 0)
        hook_errors = data.get("hookErrors", [])

        if hook_count:
            yield Static(f"{hook_count} hook(s) executed", classes="system-info")

        if hook_errors:
            for err in hook_errors:
                yield Static(f"Hook error: {err}", classes="system-error")

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
    """Screen showing conversation path details."""

    BINDINGS = [
        # Vim navigation (hidden from footer)
        Binding("j", "next_message", "Down", show=False),
        Binding("k", "prev_message", "Up", show=False),
        Binding("ctrl+f", "page_down", "Page Down", show=False),
        Binding("ctrl+u", "page_up", "Page Up", show=False),
        Binding("g", "scroll_top", "Top", show=False),
        Binding("G", "scroll_bottom", "Bottom", show=False),
        Binding("n", "next_match", "Next Match", show=False),
        Binding("N", "prev_match", "Prev Match", show=False),
        Binding("shift+tab", "prev_checkpoint", "Prev CP", show=False),
        # Footer: Navigation
        Binding("escape", "cancel_or_back", "Back"),
        Binding("/", "start_search", "Search"),
        Binding("tab", "next_checkpoint", "Next CP"),
        # Footer: Actions
        Binding("t", "teleport", "Teleport"),
        Binding("c", "copy_session_id", "Copy"),
        Binding("b", "switch_branch", "Branch"),
        Binding("e", "export_from_message", "Export"),
        # Footer: Display
        Binding("s", "toggle_system", "System"),
    ]

    DEFAULT_CSS = """
    SessionScreen {
        background: $background;
    }

    SessionScreen #session-header-row {
        width: 100%;
        height: 1;
        margin: 0 1;
    }

    SessionScreen #session-title {
        width: 1fr;
        text-style: bold;
    }

    SessionScreen #session-id {
        width: auto;
        color: $text-muted;
    }

    SessionScreen #session-meta {
        color: $text-muted;
        margin: 0 1 1 1;
    }

    SessionScreen #message-container {
        padding: 0 1;
    }

    /* Message types with left accent bars (defined in app.py) */
    SessionScreen .message-user .message-header {
        color: $primary;
    }

    SessionScreen .message-assistant .message-header {
        color: $secondary;
    }

    SessionScreen .message-summary .message-header {
        color: $warning;
    }

    SessionScreen .message-checkpoint .message-header {
        color: $success;
    }

    SessionScreen .checkpoint-info {
        color: $success;
    }

    SessionScreen .message-system .message-header {
        color: $text-muted;
    }

    SessionScreen .system-info {
        color: $text-muted;
        text-style: italic;
    }

    SessionScreen .system-error {
        color: $error;
    }

    SessionScreen .thinking {
        text-style: italic;
        color: $text-muted;
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
        color: $background;
    }

    /* Selected message highlight */
    SessionScreen .message-selected {
        background: $panel;
    }
    """

    def __init__(self, path: ConversationPath, scanner: ClaudeScanner):
        super().__init__()
        self.path = path
        self.scanner = scanner
        self.displayed_count: int = 0
        self.search_query: str = ""
        self.match_widgets: list[MessageWidget] = []
        self.current_match_index: int = -1
        # All message widgets and selection
        self.message_widgets: list[MessageWidget] = []
        self.selected_message: Message | None = None
        self.selected_message_widget: MessageWidget | None = None
        self.current_message_index: int = -1
        # Checkpoint navigation (subset of messages)
        self.checkpoint_widgets: list[MessageWidget] = []
        self.current_checkpoint_index: int = -1
        # Toggle for showing system messages (hidden by default)
        self.show_system: bool = False
        # Message tree for fork detection
        self._message_tree: MessageTree | None = None
        # Fork point UUIDs in this path (for branch indicator)
        self._fork_points: set[str] = set()
        # All display messages (for lazy loading)
        self._all_display_messages: list[Message] = []
        # Gap in the middle (unrendered messages between top and bottom)
        self._gap_start: int = 0  # First unrendered index
        self._gap_end: int = 0  # Last unrendered index (exclusive)

    def compose(self) -> ComposeResult:
        """Create the session screen layout."""
        # Search input (hidden by default)
        yield Input(placeholder="/search...", id="search-input")

        # Header with session info and ID
        with Horizontal(id="session-header-row"):
            yield Static(
                f" {self.path.title or 'Untitled'}",
                id="session-title",
                markup=False,
            )
            yield Static(
                self.path.id[:8],
                id="session-id",
            )
        yield Static(
            f"  {self.path.project_display}",
            id="session-meta",
        )

        # Message list
        yield ScrollableContainer(id="message-container")
        yield Footer()

    def on_mount(self) -> None:
        """Load messages on mount."""
        self._load_messages()
        container = self.query_one("#message-container", ScrollableContainer)
        # Focus container so keybindings work immediately
        container.focus()
        # Scroll to bottom and select last checkpoint
        self.call_after_refresh(self._scroll_to_end_and_select_last)

    # Number of messages to render at each end initially (for gg and G)
    INITIAL_RENDER_COUNT = 50
    # Number of messages to load when scrolling
    LOAD_MORE_COUNT = 50

    def _load_messages(self) -> None:
        """Load and display messages."""
        container = self.query_one("#message-container", ScrollableContainer)

        # Load messages and tree together (avoid double-parsing)
        messages, tree = self.scanner.load_conversation_path_with_tree(self.path)

        # Detect fork points if we have a tree
        if tree is not None:
            self._message_tree = tree
            # Find all fork points along this path
            self._fork_points = set()
            for msg in messages:
                if tree.is_fork_point(msg.uuid):
                    self._fork_points.add(msg.uuid)

        # Filter to displayable message types
        display_types = [MessageType.USER, MessageType.ASSISTANT, MessageType.SUMMARY, MessageType.FILE_HISTORY_SNAPSHOT]
        if self.show_system:
            display_types.append(MessageType.SYSTEM)
        self._all_display_messages = [m for m in messages if m.type in display_types]

        # Count checkpoints
        checkpoint_count = sum(1 for m in self._all_display_messages if m.type == MessageType.FILE_HISTORY_SNAPSHOT)

        # Update header with info
        self.displayed_count = len(self._all_display_messages)
        meta = self.query_one("#session-meta", Static)
        branch_str = ""
        if self.path.sibling_leaf_uuids:
            branch_count = len(self.path.sibling_leaf_uuids) + 1
            branch_str = f"  {branch_count} branches"
        cp_str = f"  {checkpoint_count} checkpoints" if checkpoint_count else ""
        meta.update(f"  {self.path.project_display}  {self.displayed_count} messages{branch_str}{cp_str}")

        # Track rendered ranges - we render top and bottom for gg/G support
        total = len(self._all_display_messages)

        # For small conversations, just render everything
        if total <= self.INITIAL_RENDER_COUNT * 2:
            self._gap_start = total  # No gap
            self._gap_end = total
        else:
            # Render first N and last N messages, with a gap in the middle
            self._gap_start = self.INITIAL_RENDER_COUNT  # End of top section
            self._gap_end = total - self.INITIAL_RENDER_COUNT  # Start of bottom section

        # Create message widgets for both ends
        self.message_widgets = []
        self.checkpoint_widgets = []
        self._render_messages(container, 0, self._gap_start)  # Top section
        if self._gap_end < total:
            self._render_messages(container, self._gap_end, total)  # Bottom section

    def _render_messages(self, container, start_idx: int, end_idx: int, prepend: bool = False) -> None:
        """Render messages in the given range."""
        messages_to_render = self._all_display_messages[start_idx:end_idx]
        new_widgets = []

        for i, msg in enumerate(messages_to_render, start=start_idx + 1):
            has_branch = msg.uuid in self._fork_points
            widget = MessageWidget(
                msg,
                turn_number=i,
                show_thinking=True,
                has_branch=has_branch,
            )
            new_widgets.append(widget)
            if msg.type == MessageType.FILE_HISTORY_SNAPSHOT:
                self.checkpoint_widgets.append(widget)

        if prepend:
            # Insert at the beginning
            for widget in reversed(new_widgets):
                container.mount(widget, before=0)
            self.message_widgets = new_widgets + self.message_widgets
        else:
            # Append at the end
            for widget in new_widgets:
                container.mount(widget)
            self.message_widgets.extend(new_widgets)

    def _load_more_at_top(self) -> bool:
        """Load more messages at the top of the gap. Returns True if more were loaded."""
        if self._gap_start >= self._gap_end:
            return False  # No gap left

        container = self.query_one("#message-container", ScrollableContainer)

        # Calculate new range - expand top section into the gap
        new_end = min(self._gap_start + self.LOAD_MORE_COUNT, self._gap_end)

        # Find the widget index where we need to insert (after current top section)
        insert_idx = self._gap_start

        # Render the new messages
        messages_to_render = self._all_display_messages[self._gap_start:new_end]
        new_widgets = []
        for i, msg in enumerate(messages_to_render, start=self._gap_start + 1):
            has_branch = msg.uuid in self._fork_points
            widget = MessageWidget(msg, turn_number=i, show_thinking=True, has_branch=has_branch)
            new_widgets.append(widget)
            if msg.type == MessageType.FILE_HISTORY_SNAPSHOT:
                self.checkpoint_widgets.append(widget)

        # Insert after the top section
        for j, widget in enumerate(new_widgets):
            container.mount(widget, before=insert_idx + j)

        # Insert into message_widgets at the right position
        self.message_widgets = (
            self.message_widgets[:insert_idx] +
            new_widgets +
            self.message_widgets[insert_idx:]
        )

        self._gap_start = new_end
        return True

    def _load_more_at_bottom(self) -> bool:
        """Load more messages at the bottom of the gap. Returns True if more were loaded."""
        if self._gap_start >= self._gap_end:
            return False  # No gap left

        container = self.query_one("#message-container", ScrollableContainer)

        # Calculate new range - expand bottom section into the gap
        new_start = max(self._gap_end - self.LOAD_MORE_COUNT, self._gap_start)

        # Find the widget index where the bottom section starts
        bottom_section_start = self._gap_start

        # Render the new messages
        messages_to_render = self._all_display_messages[new_start:self._gap_end]
        new_widgets = []
        for i, msg in enumerate(messages_to_render, start=new_start + 1):
            has_branch = msg.uuid in self._fork_points
            widget = MessageWidget(msg, turn_number=i, show_thinking=True, has_branch=has_branch)
            new_widgets.append(widget)
            if msg.type == MessageType.FILE_HISTORY_SNAPSHOT:
                self.checkpoint_widgets.append(widget)

        # Insert before the bottom section
        for j, widget in enumerate(new_widgets):
            container.mount(widget, before=bottom_section_start + j)

        # Insert into message_widgets at the right position
        self.message_widgets = (
            self.message_widgets[:bottom_section_start] +
            new_widgets +
            self.message_widgets[bottom_section_start:]
        )

        self._gap_end = new_start
        return True

    def _fill_gap(self) -> None:
        """Fill the entire gap (for seamless navigation)."""
        while self._gap_start < self._gap_end:
            self._load_more_at_top()

    def _widget_index_to_message_index(self, widget_idx: int) -> int:
        """Convert widget list index to actual message index."""
        if self._gap_start >= self._gap_end:
            # No gap, indices match
            return widget_idx
        # Top section: widget indices 0 to gap_start-1 map directly
        if widget_idx < self._gap_start:
            return widget_idx
        # Bottom section: widget indices gap_start+ map to gap_end+
        return self._gap_end + (widget_idx - self._gap_start)

    def _message_index_to_widget_index(self, msg_idx: int) -> int | None:
        """Convert message index to widget list index. Returns None if in gap."""
        if self._gap_start >= self._gap_end:
            # No gap, indices match
            return msg_idx
        # In top section
        if msg_idx < self._gap_start:
            return msg_idx
        # In gap - not loaded
        if msg_idx < self._gap_end:
            return None
        # In bottom section
        return self._gap_start + (msg_idx - self._gap_end)

    def _ensure_message_loaded(self, msg_idx: int) -> int:
        """Ensure message at index is loaded, return widget index."""
        widget_idx = self._message_index_to_widget_index(msg_idx)
        if widget_idx is not None:
            return widget_idx

        # Message is in gap - load incrementally from the closer end
        # Determine which end is closer
        dist_from_top = msg_idx - self._gap_start
        dist_from_bottom = self._gap_end - msg_idx - 1

        if dist_from_top <= dist_from_bottom:
            # Closer to top section - expand downward
            while self._gap_start <= msg_idx and self._gap_start < self._gap_end:
                self._load_more_at_top()
            return msg_idx  # After loading, index matches
        else:
            # Closer to bottom section - expand upward
            while self._gap_end > msg_idx and self._gap_start < self._gap_end:
                self._load_more_at_bottom()
            # Recalculate widget index after loading
            return self._message_index_to_widget_index(msg_idx)

    def _scroll_to_end_and_select_last(self) -> None:
        """Scroll to bottom and select last message."""
        container = self.query_one("#message-container", ScrollableContainer)
        container.scroll_end(animate=False)
        # Select last message
        if self.message_widgets:
            last_widget = self.message_widgets[-1]
            self._select_message_widget(last_widget)

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
        """Scroll up, loading more messages if approaching gap."""
        container = self.query_one("#message-container", ScrollableContainer)
        # Check if near top of visible area and load more if gap exists
        if container.scroll_y < 200 and self._gap_start < self._gap_end:
            self._load_more_at_top()
        container.scroll_up()

    def action_scroll_top(self) -> None:
        """Select first message (already preloaded)."""
        if self.message_widgets:
            self._select_message_widget(self.message_widgets[0])

    def action_scroll_bottom(self) -> None:
        """Select last message (already preloaded at bottom)."""
        if self.message_widgets:
            # Last message is always at the end of message_widgets
            self._select_message_widget(self.message_widgets[-1])

    def action_page_down(self) -> None:
        """Move selection down by 5 messages."""
        if not self.message_widgets:
            return
        total_messages = len(self._all_display_messages)
        # Get current message index (not widget index)
        current_msg_idx = self._widget_index_to_message_index(self.current_message_index)
        new_msg_idx = min(current_msg_idx + 5, total_messages - 1)
        # Ensure target is loaded and get widget index
        widget_idx = self._ensure_message_loaded(new_msg_idx)
        self._select_message_widget(self.message_widgets[widget_idx])

    def action_page_up(self) -> None:
        """Move selection up by 5 messages."""
        if not self.message_widgets:
            return
        # Get current message index (not widget index)
        current_msg_idx = self._widget_index_to_message_index(self.current_message_index)
        new_msg_idx = max(current_msg_idx - 5, 0)
        # Ensure target is loaded and get widget index
        widget_idx = self._ensure_message_loaded(new_msg_idx)
        self._select_message_widget(self.message_widgets[widget_idx])

    def on_message_clicked(self, event: MessageClicked) -> None:
        """Handle message click."""
        # Find the widget for this message
        for widget in self.message_widgets:
            if widget.message.uuid == event.message.uuid:
                self._select_message_widget(widget)
                break

    def _select_message_widget(self, widget: MessageWidget) -> None:
        """Select a message widget and update UI."""
        # Clear previous selection
        if self.selected_message_widget:
            self.selected_message_widget.remove_class("selected")
            self.selected_message_widget.styles.background = None
            self.selected_message_widget.styles.border_left = None

        # Select the widget
        widget.add_class("selected")
        widget.styles.background = "#1e2a3a"  # Subtle dark blue tint
        widget.styles.border_left = ("thick", "#00d4ff")  # Cyan accent
        widget.scroll_visible()
        self.selected_message_widget = widget
        self.selected_message = widget.message

        # Update index
        try:
            self.current_message_index = self.message_widgets.index(widget)
        except ValueError:
            self.current_message_index = -1

        # Update checkpoint index if this is a checkpoint
        if widget.message.type == MessageType.FILE_HISTORY_SNAPSHOT:
            try:
                self.current_checkpoint_index = self.checkpoint_widgets.index(widget)
            except ValueError:
                pass

    def action_next_message(self) -> None:
        """Go to next message."""
        if not self.message_widgets:
            return
        total_messages = len(self._all_display_messages)
        current_msg_idx = self._widget_index_to_message_index(self.current_message_index)
        if current_msg_idx < total_messages - 1:
            new_msg_idx = current_msg_idx + 1
            widget_idx = self._ensure_message_loaded(new_msg_idx)
            self._select_message_widget(self.message_widgets[widget_idx])

    def action_prev_message(self) -> None:
        """Go to previous message."""
        if not self.message_widgets:
            return
        current_msg_idx = self._widget_index_to_message_index(self.current_message_index)
        if current_msg_idx > 0:
            new_msg_idx = current_msg_idx - 1
            widget_idx = self._ensure_message_loaded(new_msg_idx)
            self._select_message_widget(self.message_widgets[widget_idx])

    def action_next_checkpoint(self) -> None:
        """Go to next checkpoint."""
        if not self.checkpoint_widgets:
            return
        self.current_checkpoint_index = (self.current_checkpoint_index + 1) % len(self.checkpoint_widgets)
        widget = self.checkpoint_widgets[self.current_checkpoint_index]
        self._select_message_widget(widget)

    def action_prev_checkpoint(self) -> None:
        """Go to previous checkpoint."""
        if not self.checkpoint_widgets:
            return
        self.current_checkpoint_index = (self.current_checkpoint_index - 1) % len(self.checkpoint_widgets)
        widget = self.checkpoint_widgets[self.current_checkpoint_index]
        self._select_message_widget(widget)

    def action_teleport(self) -> None:
        """Launch teleport from selected message."""
        import asyncio
        if self.selected_message:
            asyncio.create_task(self._do_teleport())

    async def _do_teleport(self) -> None:
        """Execute the teleport and launch shell."""
        import subprocess
        import sys
        from one_claude.teleport.restore import FileRestorer

        try:
            # Get Session object for restorer
            if not self.path.jsonl_files:
                self.app.notify("No JSONL files in path", severity="error")
                return

            session_id = self.path.jsonl_files[-1].stem
            session = self.scanner.get_session_by_id(session_id)
            if not session:
                self.app.notify("Session not found", severity="error")
                return

            restorer = FileRestorer(self.scanner)
            # Get message_uuid (strip "checkpoint-" prefix if present)
            message_uuid = self.selected_message.uuid
            if message_uuid.startswith("checkpoint-"):
                message_uuid = message_uuid.replace("checkpoint-", "")

            teleport_session = await restorer.restore_to_sandbox(
                session,
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
            await sandbox.stop()

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
        """Copy conversation path ID to clipboard."""
        if pyperclip:
            try:
                pyperclip.copy(self.path.id)
                self.app.notify(f"Copied: {self.path.id[:8]}...")
                return
            except Exception:
                pass
        # Fallback: just show the ID
        self.app.notify(f"ID: {self.path.id}")

    def action_toggle_system(self) -> None:
        """Toggle visibility of system messages."""
        self.show_system = not self.show_system
        status = "shown" if self.show_system else "hidden"
        self.app.notify(f"System messages: {status}")
        # Reload messages
        self._reload_messages()

    def action_switch_branch(self) -> None:
        """Switch to a sibling branch."""
        if not self.path.sibling_leaf_uuids:
            self.app.notify("No other branches at this point")
            return

        # For now, just switch to the first sibling
        # TODO: Show a picker if there are multiple siblings
        sibling_uuid = self.path.sibling_leaf_uuids[0]

        # Find the ConversationPath for this sibling
        # We need to scan paths again or cache them
        tree_cache = {}
        paths = self.scanner.scan_conversation_paths(tree_cache=tree_cache)
        for p in paths:
            if p.leaf_uuid == sibling_uuid:
                # Replace this screen with the sibling path
                self.app.pop_screen()
                self.app.push_screen(SessionScreen(p, self.scanner))
                return

        self.app.notify("Could not find sibling branch")

    def _reload_messages(self) -> None:
        """Reload all messages (used after toggling filters)."""
        container = self.query_one("#message-container", ScrollableContainer)
        # Clear existing widgets
        container.remove_children()
        self.message_widgets = []
        self.checkpoint_widgets = []
        self.selected_message = None
        self.selected_message_widget = None
        self.current_message_index = -1
        self.current_checkpoint_index = -1
        # Reload
        self._load_messages()
        self.call_after_refresh(self._scroll_to_end_and_select_last)

    def action_export_from_message(self) -> None:
        """Export from selected message to gist."""
        import asyncio

        if self.selected_message:
            asyncio.create_task(self._do_export())
        else:
            self.app.notify("No message selected", severity="warning")

    async def _do_export(self) -> None:
        """Execute the export from selected message."""
        from one_claude.gist.api import get_token, start_device_flow
        from one_claude.gist.exporter import SessionExporter
        from one_claude.tui.screens.gist_modals import AuthModal

        # Check auth first
        if not get_token():
            auth_info, error = await start_device_flow()
            if error:
                self.app.notify(f"Auth failed: {error}", severity="error")
                return
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
        result = await exporter.export_from_message(
            self.path,
            self.selected_message.uuid,
        )

        if result.success and result.gist_url:
            from one_claude.tui.screens.gist_modals import ExportResultModal

            await self.app.push_screen_wait(
                ExportResultModal(result.gist_url, result.message_count, result.checkpoint_count)
            )
        else:
            self.app.notify(f"Export failed: {result.error}", severity="error")
