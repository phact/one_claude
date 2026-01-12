"""Screen for managing exported gists."""

import asyncio

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListView, ListItem, Static

from one_claude.tui.screens.gist_modals import copy_to_clipboard


class ExportsScreen(Screen):
    """Screen for viewing and managing exported gists."""

    BINDINGS = [
        Binding("j", "cursor_down", "j/k nav", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("escape", "go_back", "Esc"),
        Binding("q", "go_back", "q·back"),
        Binding("u", "copy_url", "u·url"),
        Binding("c", "copy_command", "c·cmd"),
        Binding("d", "delete_gist", "d·delete"),
        Binding("enter", "copy_command", "Enter"),
    ]

    DEFAULT_CSS = """
    ExportsScreen {
        background: $background;
    }
    ExportsScreen #title {
        text-align: center;
        text-style: bold;
        color: $primary;
        padding: 1;
    }
    ExportsScreen #empty {
        text-align: center;
        color: $text-muted;
        padding: 2;
    }
    ExportsScreen ListView {
        height: 1fr;
        margin: 0 2;
        border-left: thick gray;
    }
    ExportsScreen ListView:focus {
        border-left: thick $primary;
    }
    ExportsScreen .export-item {
        padding: 0 1;
        height: 3;
    }
    ExportsScreen .export-title {
        text-style: bold;
        color: $foreground;
    }
    ExportsScreen .export-meta {
        color: $text-muted;
    }
    ExportsScreen ListItem.--highlight {
        background: $surface;
    }
    """

    def __init__(self):
        super().__init__()
        self.exports = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Exported Gists", id="title")
        yield ListView(id="exports-list")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_list()

    def _refresh_list(self) -> None:
        from one_claude.gist.store import load_exports

        self.exports = load_exports()
        exports_list = self.query_one("#exports-list", ListView)
        exports_list.clear()

        if not self.exports:
            exports_list.append(ListItem(Static("No exported gists yet", id="empty")))
            return

        for export in self.exports:
            item = ListItem(
                Vertical(
                    Static(export.title, classes="export-title"),
                    Static(
                        f"{export.message_count} msgs, {export.checkpoint_count} checkpoints | {export.exported_at[:10]} | {export.gist_id[:8]}...",
                        classes="export-meta",
                    ),
                    classes="export-item",
                )
            )
            exports_list.append(item)

    def _get_selected_export(self):
        exports_list = self.query_one("#exports-list", ListView)
        if exports_list.index is not None and exports_list.index < len(self.exports):
            return self.exports[exports_list.index]
        return None

    def action_cursor_down(self) -> None:
        self.query_one("#exports-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#exports-list", ListView).action_cursor_up()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_copy_url(self) -> None:
        export = self._get_selected_export()
        if export:
            if copy_to_clipboard(export.gist_url):
                self.app.notify("URL copied!")
            else:
                self.app.notify("Copy failed", severity="error")

    def action_copy_command(self) -> None:
        export = self._get_selected_export()
        if export:
            command = f"uvx one_claude gist import {export.gist_id}"
            if copy_to_clipboard(command):
                self.app.notify("Command copied!")
            else:
                self.app.notify("Copy failed", severity="error")

    def action_delete_gist(self) -> None:
        export = self._get_selected_export()
        if export:
            asyncio.create_task(self._do_delete(export))

    async def _do_delete(self, export) -> None:
        from one_claude.gist.api import GistAPI
        from one_claude.gist.store import delete_export

        self.app.notify(f"Deleting {export.gist_id[:8]}...")

        api = GistAPI()
        success, error = await api.delete(export.gist_id)

        if success:
            delete_export(export.gist_id)
            self.app.notify("Gist deleted")
            self._refresh_list()
        else:
            # Still remove from local tracking even if GitHub delete fails
            # (gist might have been manually deleted)
            if "404" in str(error):
                delete_export(export.gist_id)
                self.app.notify("Removed from list (already deleted from GitHub)")
                self._refresh_list()
            else:
                self.app.notify(f"Delete failed: {error}", severity="error")
