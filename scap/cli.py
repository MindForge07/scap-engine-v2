"""SCAP v2 CLI — command-line interface."""
from __future__ import annotations

import json
import os
import sys
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from scap.models import Decision, ProjectContext, Experience
from scap.store import MemoryStore

console = Console()

# Default export directory
_EXPORT_DIR = os.environ.get("SCAP_EXPORT_DIR", ".scap")


def _get_store(config_path: str) -> MemoryStore:
    store = MemoryStore(config_path)
    store.initialize()
    return store


def _auto_export(store: MemoryStore, project: str) -> None:
    """Export context.md after a write."""
    try:
        path = os.path.join(_EXPORT_DIR, f"{project}.md")
        store.export_context(project, path)
        console.print(f"  [dim]→ context exported to {path}[/dim]")
    except Exception as e:
        console.print(f"  [dim yellow]→ export skipped: {e}[/dim yellow]")


@click.group()
@click.option("--db", default="./data/scap.db", help="Database path")
@click.pass_context
def cli(ctx: click.Context, db: str) -> None:
    """SCAP v2 — Project Memory System"""
    ctx.ensure_object(dict)
    ctx.obj["store"] = _get_store(db)


@cli.command()
@click.option("--project", "-p", prompt="Project name", help="Project namespace")
@click.option("--stack", "-s", multiple=True, help="Tech stack items (repeatable)")
@click.pass_context
def init(ctx: click.Context, project: str, stack: tuple) -> None:
    """Initialize a project in the memory store."""
    store: MemoryStore = ctx.obj["store"]
    ctx_obj = ProjectContext(
        project=project,
        tech_stack=list(stack) if stack else [],
    )
    store.update_project_context(ctx_obj)
    console.print(f"[green]✓[/green] Project '{project}' initialized")
    if stack:
        console.print(f"  Tech stack: {', '.join(stack)}")
    _auto_export(store, project)


@cli.command()
@click.option("--project", "-p", default="", help="Filter by project")
@click.pass_context
def status(ctx: click.Context, project: str) -> None:
    """Show system status."""
    store: MemoryStore = ctx.obj["store"]
    stats = store.get_stats(project)

    table = Table(title="SCAP v2 Status")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Decisions", str(stats["decision_count"]))
    table.add_row("Experiences", str(stats["experience_count"]))
    table.add_row("Projects", ", ".join(stats["projects"]) if stats["projects"] else "(none)")
    console.print(table)


@cli.command()
@click.argument("query")
@click.option("--project", "-p", default="", help="Filter by project")
@click.pass_context
def search(ctx: click.Context, query: str, project: str) -> None:
    """Search project memory."""
    store: MemoryStore = ctx.obj["store"]
    results = store.search(project, query, limit=10)

    if not results:
        console.print(f"[yellow]No results for '{query}'[/yellow]")
        return

    console.print(f"Found {len(results)} results:")
    for r in results:
        console.print(f"  [cyan]{r.get('entity_id', '?')}[/cyan] "
                       f"[{r.get('entity_type', '?')}] "
                       f"{r.get('title', '?')[:60]}")
        if r.get("snippet"):
            console.print(f"    {r['snippet'][:100]}")


@cli.command("list")
@click.option("--project", "-p", default="", help="Filter by project")
@click.option("--limit", "-l", default=50, help="Max results")
@click.pass_context
def list_cmd(ctx: click.Context, project: str, limit: int) -> None:
    """List all decisions."""
    store: MemoryStore = ctx.obj["store"]
    decisions = store.list_decisions(project=project, limit=limit)

    if not decisions:
        console.print("[yellow]No decisions recorded yet[/yellow]")
        return

    table = Table(title="Project Decisions")
    table.add_column("ID", style="cyan")
    table.add_column("Project")
    table.add_column("Title")
    table.add_column("Status", style="green")
    table.add_column("Updated")

    for d in decisions:
        table.add_row(
            d.id, d.project, d.title[:40], d.status,
            d.updated_at.strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)


@cli.command("ingest")
@click.option("--file", "-f", "file_path", type=str, help="Markdown file with YAML front matter")
@click.pass_context
def ingest_cmd(ctx: click.Context, file_path: str) -> None:
    """Import a decision from a markdown file with YAML front matter."""
    if not file_path or not os.path.isfile(file_path):
        console.print("[red]File not found[/red]")
        sys.exit(1)

    import yaml
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Extract YAML front matter
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            meta = yaml.safe_load(parts[1])
            body = parts[2].strip()
        else:
            meta = {}
            body = content
    else:
        meta = {}
        body = content

    store: MemoryStore = ctx.obj["store"]
    project = meta.get("project", "default")
    d = Decision(
        project=project,
        title=meta.get("title", os.path.basename(file_path)),
        decision=meta.get("decision", body[:500]),
        rationale=meta.get("rationale", ""),
        constraints=meta.get("constraints", []),
        tags=meta.get("tags", []),
    )
    d = store.save_decision(d)
    console.print(f"[green]✓[/green] Ingested: {d.id} — {d.title}")
    _auto_export(store, project)


@cli.command()
@click.option("--project", "-p", required=True, help="Project to export")
@click.option("--output", "-o", default="", help="Output path (default: .scap/{project}.md)")
@click.pass_context
def export(ctx: click.Context, project: str, output: str) -> None:
    """Export project context to a markdown file for system prompt injection."""
    store: MemoryStore = ctx.obj["store"]
    if not output:
        output = os.path.join(_EXPORT_DIR, f"{project}.md")
    path = store.export_context(project, output)
    console.print(f"[green]✓[/green] Exported to: {path}")

    # Show preview
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    console.print(f"  {len(lines)} lines, {sum(len(l) for l in lines)} chars")
    console.print(f"  [dim]Include this file in your MCP client's system prompt[/dim]")


@cli.command()
@click.option("--project", "-p", required=True, help="Project name")
@click.option("--stack", "-s", multiple=True, help="Tech stack items")
@click.option("--convention", "-c", multiple=True, help="Conventions")
@click.pass_context
def configure(ctx: click.Context, project: str, stack: tuple, convention: tuple) -> None:
    """Update project context (tech stack, conventions)."""
    store: MemoryStore = ctx.obj["store"]
    existing = store.get_project_context(project)
    ctx_obj = ProjectContext(
        project=project,
        tech_stack=list(stack) if stack else (existing.tech_stack if existing else []),
        conventions=list(convention) if convention else (existing.conventions if existing else []),
        active_goals=existing.active_goals if existing else [],
    )
    store.update_project_context(ctx_obj)
    console.print(f"[green]✓[/green] Project '{project}' context updated")
    _auto_export(store, project)


if __name__ == "__main__":
    cli()
