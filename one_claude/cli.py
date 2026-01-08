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


if __name__ == "__main__":
    main()
