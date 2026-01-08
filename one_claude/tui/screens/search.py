"""Search screen for one_claude."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Input, Label, ListItem, ListView, Static

from one_claude.core.scanner import ClaudeScanner
from one_claude.index.search import SearchEngine, SearchResult


class SearchResultItem(ListItem):
    """A search result item."""

    def __init__(self, result: SearchResult):
        super().__init__()
        self.result = result

    def compose(self) -> ComposeResult:
        """Create the result display."""
        session = self.result.session
        title = session.title or "Untitled"
        project = session.project_display.rstrip("/").split("/")[-1]
        score = f"{self.result.score:.2f}"

        yield Static(f"[{score}] {title}", classes="result-title")
        yield Static(f"  {project} - {self.result.snippet[:60]}...", classes="result-meta")


class SearchScreen(Screen):
    """Search screen."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("enter", "select", "Open"),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
    ]

    CSS = """
    #search-container {
        height: 100%;
        padding: 1;
    }

    #search-input {
        dock: top;
        margin-bottom: 1;
    }

    #search-mode {
        dock: top;
        margin-bottom: 1;
        color: $text-muted;
    }

    #results-container {
        height: 1fr;
    }

    .result-title {
        text-style: bold;
    }

    .result-meta {
        color: $text-muted;
    }

    #no-results {
        text-align: center;
        margin-top: 2;
        color: $text-muted;
    }
    """

    def __init__(self, scanner: ClaudeScanner):
        super().__init__()
        self.scanner = scanner
        self.search_engine = SearchEngine(scanner)
        self.results: list[SearchResult] = []
        self.mode = "text"  # "text", "title", "content"

    def compose(self) -> ComposeResult:
        """Create the search screen layout."""
        with Vertical(id="search-container"):
            yield Input(placeholder="Search sessions...", id="search-input")
            yield Label(f"Mode: {self.mode} | Tab to change", id="search-mode")
            yield ListView(id="results-list")
            yield Label("Type to search", id="no-results")

    def on_mount(self) -> None:
        """Focus search input on mount."""
        self.query_one("#search-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle search input changes."""
        query = event.value.strip()
        self._perform_search(query)

    def _perform_search(self, query: str) -> None:
        """Perform search and update results."""
        results_list = self.query_one("#results-list", ListView)
        no_results = self.query_one("#no-results", Label)

        if not query:
            results_list.clear()
            no_results.update("Type to search")
            no_results.display = True
            return

        self.results = self.search_engine.search(query, mode=self.mode, limit=30)

        results_list.clear()
        if self.results:
            no_results.display = False
            for result in self.results:
                results_list.append(SearchResultItem(result))
        else:
            no_results.update(f"No results for '{query}'")
            no_results.display = True

    def on_key(self, event) -> None:
        """Handle key events."""
        if event.key == "tab":
            # Cycle through modes
            modes = ["text", "title", "content"]
            idx = modes.index(self.mode)
            self.mode = modes[(idx + 1) % len(modes)]
            self.query_one("#search-mode", Label).update(f"Mode: {self.mode} | Tab to change")

            # Re-run search with new mode
            query = self.query_one("#search-input", Input).value
            if query:
                self._perform_search(query)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle result selection."""
        if isinstance(event.item, SearchResultItem):
            from one_claude.tui.screens.session import SessionScreen

            self.app.push_screen(SessionScreen(event.item.result.session, self.scanner))

    def action_close(self) -> None:
        """Close search screen."""
        self.app.pop_screen()

    def action_cursor_down(self) -> None:
        """Move cursor down in results."""
        self.query_one("#results-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        """Move cursor up in results."""
        self.query_one("#results-list", ListView).action_cursor_up()

    def action_select(self) -> None:
        """Select current result."""
        self.query_one("#results-list", ListView).action_select_cursor()
