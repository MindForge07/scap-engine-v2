"""SCAP v2 CLI — command-line interface."""
from __future__ import annotations

import json
import os
import sys
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from scap.models import Decision, ProjectContext, Experience, LatentTrace
from scap.store import MemoryStore
from scap.embedder import Embedder

console = Console()

# Default export directory
_EXPORT_DIR = os.environ.get("SCAP_EXPORT_DIR", ".scap")


def _get_store(config_path: str) -> MemoryStore:
    store = MemoryStore(config_path)
    store.initialize()
    return store


def _get_embedder() -> Embedder:
    """Create a lazy-loading embedder instance."""
    return Embedder()


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
    table.add_row("Latent Traces", str(stats.get("latent_trace_count", 0)))
    table.add_row("Evolution Gen", str(stats.get("evolution_gen", 0)))
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


# ═══════════════════════════════════════════════════════
# Latent Space Evolution Commands (Phase 3)
# ═══════════════════════════════════════════════════════


@cli.command()
@click.argument("query")
@click.option("--project", "-p", default="", help="Filter by project")
@click.option("--limit", "-l", default=5, help="Max results (max 20)")
@click.pass_context
def latent(ctx: click.Context, query: str, project: str, limit: int) -> None:
    """Semantic similarity search using latent vectors.

    Finds decisions and experiences that are semantically similar to QUERY,
    even without exact keyword overlap. Requires sentence-transformers.
    """
    store: MemoryStore = ctx.obj["store"]
    limit = min(max(limit, 1), 20)
    embedder = _get_embedder()

    if not embedder.is_available:
        console.print("[red]Embedding model not available.[/red]")
        console.print("[dim]Install with: pip install scap-engine-v2[evolution][/dim]")
        return

    query_vector = embedder.embed(query)
    if not query_vector:
        console.print("[red]Failed to generate embedding for query.[/red]")
        return

    results = store.search_by_vector(project, query_vector, limit)

    if not results:
        console.print(f"[yellow]No latent matches for '{query}'[/yellow]")
        return

    console.print(f"Found {len(results)} semantic matches:")
    for r in results:
        sim = r.get("similarity", 0)
        console.print(f"  [cyan]{r.get('entity_id', '?')}[/cyan] "
                       f"[{r.get('entity_type', '?')}] "
                       f"sim={sim:.3f} "
                       f"{r.get('title', '?')[:50]}")
        if r.get("snippet"):
            console.print(f"    [dim]{r['snippet'][:100]}[/dim]")


@cli.command()
@click.option("--project", "-p", required=True, help="Project to consolidate")
@click.option("--threshold", "-t", default=0.85, help="Similarity threshold (0.5-0.99)")
@click.pass_context
def consolidate(ctx: click.Context, project: str, threshold: float) -> None:
    """Consolidate similar latent traces, advancing evolution generation.

    Merges near-duplicate traces: the higher-fitness trace survives and
    gets evolution_gen + 1, the lower-fitness one is pruned.
    Original Decision/Experience records are preserved.
    """
    store: MemoryStore = ctx.obj["store"]
    threshold = min(max(threshold, 0.5), 0.99)

    traces = store.list_latent_traces(project=project, limit=500)
    if len(traces) < 2:
        console.print(f"[yellow]Not enough traces to consolidate "
                       f"({len(traces)} found, need >= 2)[/yellow]")
        return

    traces.sort(key=lambda t: t.fitness, reverse=True)

    merged_count = 0
    survivors: list[LatentTrace] = []
    merged_ids: list[str] = []

    for trace in traces:
        if trace.id in merged_ids:
            continue
        is_duplicate = False
        for survivor in survivors:
            sim = MemoryStore._cosine_similarity(trace.embedding, survivor.embedding)
            if sim >= threshold:
                survivor.evolution_gen += 1
                store.save_latent_trace(survivor)
                store.delete_latent_trace(trace.id)
                merged_ids.append(trace.id)
                merged_count += 1
                is_duplicate = True
                break
        if not is_duplicate:
            survivors.append(trace)

    max_gen = max((t.evolution_gen for t in survivors), default=0)
    console.print(f"[green]✓[/green] Consolidation complete for '{project}'")
    console.print(f"  Total traces: {len(traces)}")
    console.print(f"  Merged: {merged_count}")
    console.print(f"  Surviving: {len(survivors)}")
    console.print(f"  New evolution gen: {max_gen}")


@cli.command()
@click.option("--project", "-p", required=True, help="Project name")
@click.option("--task", "-t", default="", help="Task description for semantic filtering")
@click.option("--min-fitness", "-f", default=0.0, help="Minimum fitness (0.0-1.0)")
@click.pass_context
def evolved(ctx: click.Context, project: str, task: str, min_fitness: float) -> None:
    """Show fitness-weighted context with evolution metadata.

    Lists latent traces ranked by fitness x evolution_gen, optionally
    filtered by semantic similarity to a task description.
    """
    store: MemoryStore = ctx.obj["store"]
    min_fitness = min(max(min_fitness, 0.0), 1.0)

    traces = store.list_latent_traces(project=project, limit=500)
    traces = [t for t in traces if t.fitness >= min_fitness]

    if not traces:
        console.print(f"[yellow]No latent traces found for '{project}'[/yellow]")
        return

    # Optional semantic filtering by task similarity
    task_vector = None
    if task:
        embedder = _get_embedder()
        if embedder.is_available:
            task_vector = embedder.embed(task)

    # Score: fitness * (1 + evolution_gen * 0.1) + optional similarity bonus
    scored = []
    for t in traces:
        base_score = t.fitness * (1 + t.evolution_gen * 0.1)
        if task_vector:
            sim = MemoryStore._cosine_similarity(task_vector, t.embedding)
            base_score += sim * 0.3
        scored.append((base_score, t))
    scored.sort(key=lambda x: x[0], reverse=True)

    table = Table(title=f"Evolved Context — {project}")
    table.add_column("Entity ID", style="cyan")
    table.add_column("Type")
    table.add_column("Fitness", style="green")
    table.add_column("Gen")
    table.add_column("Score", style="yellow")
    table.add_column("Title")

    for score, t in scored[:20]:
        title = ""
        if t.entity_type == "decision":
            d = store.get_decision(t.entity_id)
            if d:
                title = d.title[:40]
        elif t.entity_type == "experience":
            exp = store.get_experience(t.entity_id)
            if exp:
                title = exp.situation[:40]

        table.add_row(
            t.entity_id, t.entity_type,
            f"{t.fitness:.2f}", str(t.evolution_gen),
            f"{score:.3f}", title,
        )

    console.print(table)
    stats = store.get_stats(project=project)
    console.print(f"\n[dim]Evolution gen: {stats.get('evolution_gen', 0)} | "
                  f"Total traces: {stats.get('latent_trace_count', 0)}[/dim]")


@cli.command()
@click.option("--project", "-p", default="", help="Filter by project")
@click.option("--limit", "-l", default=50, help="Max results")
@click.pass_context
def traces(ctx: click.Context, project: str, limit: int) -> None:
    """List latent traces."""
    store: MemoryStore = ctx.obj["store"]
    trace_list = store.list_latent_traces(project=project, limit=limit)

    if not trace_list:
        console.print("[yellow]No latent traces found[/yellow]")
        return

    table = Table(title="Latent Traces")
    table.add_column("ID", style="cyan")
    table.add_column("Entity ID")
    table.add_column("Type")
    table.add_column("Project")
    table.add_column("Fitness", style="green")
    table.add_column("Gen")
    table.add_column("Created")

    for t in trace_list:
        table.add_row(
            t.id, t.entity_id, t.entity_type, t.project,
            f"{t.fitness:.2f}", str(t.evolution_gen),
            t.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


@cli.command()
@click.option("--project", "-p", required=True, help="Project to embed")
@click.pass_context
def embed(ctx: click.Context, project: str) -> None:
    """Backfill embeddings for existing decisions and experiences.

    Iterates through records without embeddings, generates vectors,
    and creates LatentTraces. Use after upgrading from v2.0 to v2.1.
    """
    store: MemoryStore = ctx.obj["store"]
    embedder = _get_embedder()

    if not embedder.is_available:
        console.print("[red]Embedding model not available.[/red]")
        console.print("[dim]Install with: pip install scap-engine-v2[evolution][/dim]")
        return

    # Process decisions
    decisions = store.list_decisions(project=project, limit=500)
    dec_embedded = 0
    for d in decisions:
        if d.embedding:
            continue
        text = f"{d.title} {d.decision} {d.rationale}"
        d.embedding = embedder.embed(text)
        if d.embedding:
            store.save_decision(d)
            trace = LatentTrace(
                entity_id=d.id, entity_type="decision",
                project=project, embedding=d.embedding,
            )
            store.save_latent_trace(trace)
            dec_embedded += 1

    # Process experiences
    experiences = store.list_experiences(project=project, limit=500)
    exp_embedded = 0
    for e in experiences:
        if e.embedding:
            continue
        text = f"{e.situation} {e.action} {e.lesson}"
        e.embedding = embedder.embed(text)
        if e.embedding:
            store.save_experience(e)
            trace = LatentTrace(
                entity_id=e.id, entity_type="experience",
                project=project, embedding=e.embedding,
            )
            store.save_latent_trace(trace)
            exp_embedded += 1

    console.print(f"[green]✓[/green] Embedding backfill complete for '{project}'")
    console.print(f"  Decisions embedded: {dec_embedded}/{len(decisions)}")
    console.print(f"  Experiences embedded: {exp_embedded}/{len(experiences)}")
    console.print(f"  Total new traces: {dec_embedded + exp_embedded}")


if __name__ == "__main__":
    cli()
