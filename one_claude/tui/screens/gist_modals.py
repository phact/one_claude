"""Modal screens for gist export/import and help."""

import asyncio
import subprocess
import sys

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static


class HelpModal(ModalScreen[None]):
    """Modal showing keyboard shortcuts."""

    BINDINGS = [
        Binding("escape", "close", "Esc"),
        Binding("?", "close", "?"),
        Binding("enter", "close", "Enter"),
    ]

    DEFAULT_CSS = """
    HelpModal {
        align: center middle;
    }
    HelpModal > Vertical {
        width: 70;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        background: $surface;
        border-left: thick $primary;
        border-top: solid $border;
        border-right: solid $border;
        border-bottom: solid $border;
    }
    HelpModal .title {
        text-align: center;
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    HelpModal .section {
        color: $primary;
        text-style: bold;
        margin-top: 1;
    }
    HelpModal .keys {
        color: $foreground;
    }
    HelpModal .key {
        color: $warning;
        text-style: bold;
    }
    HelpModal .hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """

    def __init__(self, screen_name: str = "home"):
        super().__init__()
        self.screen_name = screen_name

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Keyboard Shortcuts", classes="title")

            if self.screen_name == "home":
                yield Static("Navigation", classes="section")
                yield Static("  j/k        Move up/down", classes="keys", markup=False)
                yield Static("  gg / G     Top / Bottom", classes="keys", markup=False)
                yield Static("  Ctrl+d/u   Page down/up", classes="keys", markup=False)
                yield Static("  Tab        Switch panel", classes="keys", markup=False)
                yield Static("  Enter      Open session", classes="keys", markup=False)

                yield Static("Actions", classes="section")
                yield Static("  /          Search", classes="keys", markup=False)
                yield Static("  t          Teleport to checkpoint", classes="keys", markup=False)
                yield Static("  c          Copy session ID", classes="keys", markup=False)
                yield Static("  m          Toggle mode (docker/local)", classes="keys", markup=False)

                yield Static("Gists", classes="section")
                yield Static("  e          Export to gist", classes="keys", markup=False)
                yield Static("  i          Import from gist", classes="keys", markup=False)
                yield Static("  E          Manage exported gists", classes="keys", markup=False)

            else:  # session screen
                yield Static("Navigation", classes="section")
                yield Static("  j/k        Move between messages", classes="keys", markup=False)
                yield Static("  gg / G     First / Last message", classes="keys", markup=False)
                yield Static("  n/p        Next/prev checkpoint", classes="keys", markup=False)
                yield Static("  [/]        Prev/next branch point", classes="keys", markup=False)

                yield Static("Actions", classes="section")
                yield Static("  /          Search in messages", classes="keys", markup=False)
                yield Static("  t          Teleport to this point", classes="keys", markup=False)
                yield Static("  c          Copy message content", classes="keys", markup=False)
                yield Static("  b          View/switch branches", classes="keys", markup=False)
                yield Static("  e          Export from here to gist", classes="keys", markup=False)

                yield Static("Display", classes="section")
                yield Static("  s          Toggle system messages", classes="keys", markup=False)
                yield Static("  h          Toggle thinking blocks", classes="keys", markup=False)

            yield Static("Global", classes="section")
            yield Static("  ?          This help", classes="keys", markup=False)
            yield Static("  Esc        Back / Close", classes="keys", markup=False)
            yield Static("  q          Quit", classes="keys", markup=False)

            yield Static("Press any key to close", classes="hint")

    def action_close(self) -> None:
        self.dismiss(None)


def copy_to_clipboard(text: str) -> bool:
    """Copy text to clipboard."""
    try:
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
            return True
        # Linux - try wl-copy then xclip
        try:
            subprocess.run(["wl-copy", text], check=True)
            return True
        except FileNotFoundError:
            pass
        subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
        return True
    except Exception:
        return False


class AuthModal(ModalScreen[bool]):
    """Modal for GitHub device flow auth."""

    BINDINGS = [
        Binding("escape", "cancel", "Esc"),
        Binding("c", "copy_code", "c·copy"),
    ]

    DEFAULT_CSS = """
    AuthModal {
        align: center middle;
    }
    AuthModal > Vertical {
        width: 60;
        height: auto;
        padding: 1 2;
        background: $surface;
        border-left: thick $primary;
        border-top: solid $border;
        border-right: solid $border;
        border-bottom: solid $border;
    }
    AuthModal .title {
        text-align: center;
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    AuthModal .url {
        text-align: center;
        color: $primary;
    }
    AuthModal .code {
        text-align: center;
        text-style: bold;
        color: $success;
        margin: 1 0;
    }
    AuthModal .hint {
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }
    AuthModal .status {
        text-align: center;
        color: $text-muted;
    }
    """

    def __init__(self, verification_uri: str, user_code: str, device_code: str, interval: int):
        super().__init__()
        self.verification_uri = verification_uri
        self.user_code = user_code
        self.device_code = device_code
        self.interval = interval
        self._polling = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("GitHub Authorization", classes="title")
            yield Static(f"Visit: {self.verification_uri}", classes="url")
            yield Static(self.user_code, classes="code")
            yield Static("Press 'c' to copy code", classes="hint")
            yield Static("Waiting for authorization...", id="status", classes="status")

    def action_copy_code(self) -> None:
        if copy_to_clipboard(self.user_code):
            self.app.notify("Code copied!")
        else:
            self.app.notify("Copy failed")

    def on_mount(self) -> None:
        self._polling = True
        asyncio.create_task(self._poll())

    async def _poll(self) -> None:
        from one_claude.gist.api import poll_for_token

        token, error = await poll_for_token(self.device_code, self.interval)
        if not self._polling:
            return
        if token:
            self.dismiss(True)
        else:
            self.query_one("#status", Static).update(f"Error: {error}")

    def action_cancel(self) -> None:
        self._polling = False
        self.dismiss(False)


class ExportResultModal(ModalScreen[None]):
    """Modal showing export result with URL."""

    BINDINGS = [
        Binding("escape", "close", "Esc"),
        Binding("enter", "close", "Enter"),
        Binding("u", "copy_url", "u·url"),
        Binding("c", "copy_command", "c·cmd"),
    ]

    DEFAULT_CSS = """
    ExportResultModal {
        align: center middle;
    }
    ExportResultModal > Vertical {
        width: 80;
        height: auto;
        padding: 1 2;
        background: $surface;
        border-left: thick $success;
        border-top: solid $border;
        border-right: solid $border;
        border-bottom: solid $border;
    }
    ExportResultModal .title {
        text-align: center;
        text-style: bold;
        color: $success;
        margin-bottom: 1;
    }
    ExportResultModal .stats {
        text-align: center;
        margin-bottom: 1;
    }
    ExportResultModal .url {
        text-align: center;
        color: $primary;
        margin: 1 0;
    }
    ExportResultModal .command {
        text-align: center;
        color: $success;
        margin: 0 0 1 0;
    }
    ExportResultModal .hint {
        text-align: center;
        color: $text-muted;
    }
    """

    def __init__(self, gist_url: str, message_count: int, checkpoint_count: int):
        super().__init__()
        self.gist_url = gist_url
        self.gist_id = gist_url.rstrip("/").split("/")[-1]
        self.message_count = message_count
        self.checkpoint_count = checkpoint_count

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Export Successful!", classes="title")
            yield Static(f"{self.message_count} messages, {self.checkpoint_count} checkpoints", classes="stats")
            yield Static(self.gist_url, classes="url")
            yield Static(f"uvx one_claude gist import {self.gist_id}", classes="command")
            yield Static("'u' copy URL | 'c' copy command | Enter close", classes="hint")

    def action_copy_url(self) -> None:
        if copy_to_clipboard(self.gist_url):
            self.app.notify("URL copied!")
        else:
            self.app.notify("Copy failed")

    def action_copy_command(self) -> None:
        command = f"uvx one_claude gist import {self.gist_id}"
        if copy_to_clipboard(command):
            self.app.notify("Command copied!")
        else:
            self.app.notify("Copy failed")

    def action_close(self) -> None:
        self.dismiss(None)


class ImportModal(ModalScreen[str | None]):
    """Modal for importing a gist."""

    BINDINGS = [
        Binding("escape", "cancel", "Esc"),
    ]

    DEFAULT_CSS = """
    ImportModal {
        align: center middle;
    }
    ImportModal > Vertical {
        width: 70;
        height: auto;
        padding: 1 2;
        background: $surface;
        border-left: thick $primary;
        border-top: solid $border;
        border-right: solid $border;
        border-bottom: solid $border;
    }
    ImportModal .title {
        text-align: center;
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    ImportModal Input {
        margin: 1 0;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Import from Gist", classes="title")
            yield Input(placeholder="Paste gist URL or ID...", id="gist-input")

    def on_mount(self) -> None:
        self.query_one("#gist-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if value:
            self.dismiss(value)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class GistsModal(ModalScreen[None]):
    """Modal for managing exported gists."""

    BINDINGS = [
        Binding("escape", "close", "Esc"),
        Binding("enter", "close", "Enter"),
        Binding("j", "cursor_down", "j/k nav", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("c", "copy_url", "c·copy"),
        Binding("d", "delete_gist", "d·delete"),
    ]

    DEFAULT_CSS = """
    GistsModal {
        align: center middle;
    }
    GistsModal > Vertical {
        width: 90;
        height: 80%;
        padding: 1 2;
        background: $surface;
        border-left: thick $primary;
        border-top: solid $border;
        border-right: solid $border;
        border-bottom: solid $border;
    }
    GistsModal .title {
        text-align: center;
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    GistsModal .hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    GistsModal ListView {
        height: 1fr;
        border-left: thick gray;
    }
    GistsModal ListView:focus {
        border-left: thick $primary;
    }
    GistsModal .gist-item {
        padding: 0 1;
    }
    GistsModal .gist-title {
        text-style: bold;
        color: $foreground;
    }
    GistsModal .gist-meta {
        color: $text-muted;
    }
    """

    def __init__(self):
        super().__init__()
        self.exports = []

    def compose(self) -> ComposeResult:
        from textual.widgets import ListView, ListItem

        with Vertical():
            yield Label("Exported Gists", classes="title")
            yield ListView(id="gists-list")
            yield Static("'c' copy URL | 'd' delete | Esc close", classes="hint")

    def on_mount(self) -> None:
        self._refresh_list()

    def _refresh_list(self) -> None:
        from textual.widgets import ListView, ListItem

        from one_claude.gist.store import load_exports

        self.exports = load_exports()
        gists_list = self.query_one("#gists-list", ListView)
        gists_list.clear()

        if not self.exports:
            gists_list.append(ListItem(Static("No exported gists yet")))
            return

        for export in self.exports:
            item = ListItem(
                Vertical(
                    Static(export.title, classes="gist-title"),
                    Static(
                        f"{export.message_count} msgs, {export.checkpoint_count} checkpoints | {export.exported_at[:10]}",
                        classes="gist-meta",
                    ),
                    classes="gist-item",
                )
            )
            item.export = export  # Attach data
            gists_list.append(item)

    def _get_selected_export(self):
        from textual.widgets import ListView

        gists_list = self.query_one("#gists-list", ListView)
        if gists_list.index is not None and gists_list.index < len(self.exports):
            return self.exports[gists_list.index]
        return None

    def action_copy_url(self) -> None:
        export = self._get_selected_export()
        if export:
            if copy_to_clipboard(export.gist_url):
                self.app.notify("URL copied!")
            else:
                self.app.notify("Copy failed")

    def action_delete_gist(self) -> None:
        export = self._get_selected_export()
        if export:
            asyncio.create_task(self._do_delete(export))

    async def _do_delete(self, export) -> None:
        from one_claude.gist.api import GistAPI
        from one_claude.gist.store import delete_export

        api = GistAPI()
        success, error = await api.delete(export.gist_id)

        if success:
            delete_export(export.gist_id)
            self.app.notify("Gist deleted")
            self._refresh_list()
        else:
            self.app.notify(f"Delete failed: {error}", severity="error")

    def action_cursor_down(self) -> None:
        from textual.widgets import ListView

        self.query_one("#gists-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        from textual.widgets import ListView

        self.query_one("#gists-list", ListView).action_cursor_up()

    def action_close(self) -> None:
        self.dismiss(None)
