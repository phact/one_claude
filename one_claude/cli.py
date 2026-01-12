"""CLI interface for one_claude."""

import click

from one_claude.config import Config


@click.group(invoke_without_command=True)
@click.option("--config", "-c", type=click.Path(), help="Path to config file")
@click.pass_context
def main(ctx: click.Context, config: str | None) -> None:
    """one_claude - Time Travel for Claude Code Sessions.

    Browse, search, and teleport to your Claude Code sessions across time.
    """
    ctx.ensure_object(dict)
    ctx.obj["config"] = Config.load(config) if config else Config.load()

    # If no subcommand, run the TUI
    if ctx.invoked_subcommand is None:
        from one_claude.tui.app import OneClaude

        app = OneClaude(ctx.obj["config"])
        app.run()


@main.command()
@click.pass_context
def sessions(ctx: click.Context) -> None:
    """List all sessions."""
    from rich.console import Console
    from rich.table import Table

    from one_claude.core.scanner import ClaudeScanner

    config = ctx.obj["config"]
    scanner = ClaudeScanner(config.claude_dir)

    console = Console()
    table = Table(title="Claude Code Sessions")
    table.add_column("Project", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("Messages", justify="right")
    table.add_column("Updated", style="green")

    sessions = scanner.get_sessions_flat()
    for session in sessions[:50]:  # Limit to 50
        project_name = session.project_display.rstrip("/").split("/")[-1]
        title = (session.title or "Untitled")[:40]
        updated = session.updated_at.strftime("%Y-%m-%d %H:%M")
        table.add_row(project_name, title, str(session.message_count), updated)

    console.print(table)


@main.command()
@click.argument("session_id")
@click.pass_context
def show(ctx: click.Context, session_id: str) -> None:
    """Show a specific session."""
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel

    from one_claude.core.models import MessageType
    from one_claude.core.scanner import ClaudeScanner

    config = ctx.obj["config"]
    scanner = ClaudeScanner(config.claude_dir)
    console = Console()

    # Find the session
    for project in scanner.scan_all():
        for session in project.sessions:
            if session.id == session_id or session.id.startswith(session_id):
                # Load messages
                tree = scanner.load_session_messages(session)
                messages = tree.get_main_thread()

                console.print(f"\n[bold]{session.title}[/bold]")
                console.print(f"[dim]{session.project_display}[/dim]\n")

                for msg in messages:
                    if msg.type == MessageType.USER:
                        console.print(Panel(msg.text_content[:500], title="User"))
                    elif msg.type == MessageType.ASSISTANT:
                        content = msg.text_content[:500]
                        if msg.tool_uses:
                            tools = ", ".join(t.name for t in msg.tool_uses)
                            content += f"\n[dim]Tools: {tools}[/dim]"
                        console.print(Panel(content, title="Assistant"))

                return

    console.print(f"[red]Session not found: {session_id}[/red]")


@main.command()
@click.pass_context
def projects(ctx: click.Context) -> None:
    """List all projects."""
    from rich.console import Console
    from rich.table import Table

    from one_claude.core.scanner import ClaudeScanner

    config = ctx.obj["config"]
    scanner = ClaudeScanner(config.claude_dir)

    console = Console()
    table = Table(title="Projects")
    table.add_column("Path", style="cyan")
    table.add_column("Sessions", justify="right")
    table.add_column("Latest", style="green")

    projects = scanner.scan_all()
    for project in projects:
        latest = project.latest_session
        latest_date = latest.updated_at.strftime("%Y-%m-%d") if latest else "-"
        table.add_row(project.display_path, str(project.session_count), latest_date)

    console.print(table)


@main.command()
@click.argument("query")
@click.option("--mode", "-m", default="text", help="Search mode: text, title, content")
@click.option("--limit", "-l", default=20, help="Maximum results")
@click.pass_context
def search(ctx: click.Context, query: str, mode: str, limit: int) -> None:
    """Search sessions."""
    from rich.console import Console
    from rich.table import Table

    from one_claude.core.scanner import ClaudeScanner
    from one_claude.index.search import SearchEngine

    config = ctx.obj["config"]
    scanner = ClaudeScanner(config.claude_dir)
    engine = SearchEngine(scanner)

    console = Console()

    results = engine.search(query, mode=mode, limit=limit)

    if not results:
        console.print(f"[yellow]No results for '{query}'[/yellow]")
        return

    table = Table(title=f"Search: {query}")
    table.add_column("Score", justify="right", style="cyan")
    table.add_column("Session", style="white")
    table.add_column("Snippet", style="dim")

    for result in results:
        title = result.session.title or result.session.id[:8]
        snippet = result.snippet[:60] if result.snippet else ""
        table.add_row(f"{result.score:.2f}", title[:40], snippet)

    console.print(table)


@main.command()
@click.pass_context
def tui(ctx: click.Context) -> None:
    """Launch the interactive TUI."""
    from one_claude.tui.app import OneClaude

    app = OneClaude(ctx.obj["config"])
    app.run()


@main.group()
def gist() -> None:
    """Gist export/import commands."""
    pass


@gist.command(name="import")
@click.argument("gist_url_or_id")
@click.pass_context
def gist_import(ctx: click.Context, gist_url_or_id: str) -> None:
    """Import a session from a GitHub gist."""
    import asyncio

    from rich.console import Console

    from one_claude.gist.importer import SessionImporter

    config = ctx.obj["config"]
    console = Console()

    async def do_import():
        importer = SessionImporter(config.claude_dir)
        result = await importer.import_from_gist(gist_url_or_id)

        if result.success:
            console.print(f"[green]Imported successfully![/green]")
            console.print(f"  Session: {result.session_id}")
            console.print(f"  Project: {result.project_path}")
            console.print(f"  Messages: {result.message_count}")
            console.print(f"  Checkpoints: {result.checkpoint_count}")
        else:
            console.print(f"[red]Import failed: {result.error}[/red]")

    asyncio.run(do_import())


@gist.command(name="export")
@click.argument("session_id")
@click.pass_context
def gist_export(ctx: click.Context, session_id: str) -> None:
    """Export a session to a GitHub gist."""
    import asyncio

    from rich.console import Console

    from one_claude.core.scanner import ClaudeScanner
    from one_claude.gist.exporter import SessionExporter

    config = ctx.obj["config"]
    console = Console()
    scanner = ClaudeScanner(config.claude_dir)

    # Find conversation path by session ID
    tree_cache = {}
    paths = scanner.scan_conversation_paths(tree_cache=tree_cache)

    target_path = None
    for path in paths:
        if path.id == session_id or path.id.startswith(session_id):
            target_path = path
            break
        for jsonl_file in path.jsonl_files:
            if jsonl_file.stem == session_id or jsonl_file.stem.startswith(session_id):
                target_path = path
                break

    if not target_path:
        console.print(f"[red]Session not found: {session_id}[/red]")
        return

    async def do_export():
        exporter = SessionExporter(scanner)
        result = await exporter.export_full_session(target_path)

        if result.success:
            console.print(f"[green]Exported successfully![/green]")
            console.print(f"  URL: {result.gist_url}")
            console.print(f"  Messages: {result.message_count}")
            console.print(f"  Checkpoints: {result.checkpoint_count}")
        else:
            console.print(f"[red]Export failed: {result.error}[/red]")

    asyncio.run(do_export())


@gist.command(name="list")
def gist_list() -> None:
    """List exported gists."""
    from rich.console import Console
    from rich.table import Table

    from one_claude.gist.store import load_exports

    console = Console()
    exports = load_exports()

    if not exports:
        console.print("[yellow]No exported gists yet[/yellow]")
        return

    table = Table(title="Exported Gists")
    table.add_column("Title", style="white")
    table.add_column("Messages", justify="right")
    table.add_column("Date", style="green")
    table.add_column("URL", style="cyan")

    for export in exports:
        table.add_row(
            export.title[:30],
            str(export.message_count),
            export.exported_at[:10],
            export.gist_url,
        )

    console.print(table)


if __name__ == "__main__":
    main()
