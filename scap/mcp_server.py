"""SCAP v2 — MCP server. 8 tools, designed for AI self-recording + latent evolution.

Tools 1-5 (core memory):
  scap_recall, scap_remember, scap_record_experience, scap_context, scap_status

Tools 6-8 (latent space evolution — require sentence-transformers):
  scap_retrieve_latent  — semantic similarity search via embeddings
  scap_consolidate      — nighttime consolidation: merge similar traces, advance gen
  scap_evolved_context   — fitness-weighted context retrieval with evolution metadata
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

from mcp.server.fastmcp import FastMCP

from scap.models import Decision, ProjectContext, Experience, LatentTrace
from scap.store import MemoryStore
from scap.embedder import Embedder

logger = logging.getLogger(__name__)

_store: MemoryStore | None = None
_embedder: Embedder | None = None
# Default export path — per-project .scap/context.md
_EXPORT_DIR = os.environ.get("SCAP_EXPORT_DIR", ".scap")


def _get_store() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore()
        _store.initialize()
    return _store


def _get_embedder() -> Embedder:
    """Lazy-init embedder singleton. Safe to call even if sentence-transformers
    is not installed — is_available will return False."""
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder


def _ensure_project(store: MemoryStore, project: str) -> None:
    """Auto-initialize project context if it doesn't exist."""
    if not store.get_project_context(project):
        store.update_project_context(ProjectContext(project=project))


def _auto_export(store: MemoryStore, project: str) -> None:
    """Export context.md after each write. Cheap operation (< 1ms)."""
    try:
        path = os.path.join(_EXPORT_DIR, f"{project}.md")
        store.export_context(project, path)
    except Exception as e:
        logger.debug(f"Context export skipped: {e}")


def _try_embed(text: str) -> Optional[List[float]]:
    """Try to generate an embedding for the given text.

    Returns the embedding vector, or None if the embedder is unavailable
    or the text is empty. Never raises.
    """
    embedder = _get_embedder()
    if not embedder.is_available:
        return None
    if not text or not text.strip():
        return None
    try:
        return embedder.embed(text)
    except Exception as e:
        logger.warning(f"Embedding generation failed: {e}")
        return None


def _save_trace(store: MemoryStore, entity) -> None:
    """Save a LatentTrace for an entity that has an embedding.

    Silently skips if the entity has no embedding.
    """
    if not entity.embedding:
        return
    entity_type = "decision" if isinstance(entity, Decision) else "experience"
    trace = LatentTrace(
        entity_id=entity.id,
        entity_type=entity_type,
        project=entity.project,
        embedding=entity.embedding,
    )
    store.save_latent_trace(trace)
    logger.debug(f"LatentTrace saved for {entity_type} {entity.id}")


def _format_recall(store: MemoryStore, project: str, task: str,
                   query_vector: Optional[List[float]] = None) -> str:
    """Format project memory as injection-ready text for LLM system prompt.

    When query_vector is provided, uses four-tier search (FTS5 → vector → LIKE)
    for experience retrieval, enabling semantic matching beyond keywords.
    """
    lines = []

    # 1. Project context
    ctx = store.get_project_context(project)
    if ctx:
        lines.append("[Project Context — 当前项目的已知状态]")
        if ctx.tech_stack:
            lines.append(f"技术栈: {', '.join(ctx.tech_stack)}")
        if ctx.conventions:
            lines.append("\n## 团队约定")
            for c in ctx.conventions:
                lines.append(f"- {c}")
        if ctx.active_goals:
            lines.append("\n## 活跃目标")
            for g in ctx.active_goals:
                lines.append(f"- {g}")

    # 2. Relevant decisions — score by keyword overlap with task
    all_decisions = store.list_decisions(project=project, status="active", limit=200)
    task_words = set(task.lower().split())
    scored = []
    for d in all_decisions:
        d_text = f"{d.title} {d.decision} {d.rationale} {' '.join(d.constraints)}".lower()
        overlap = sum(1 for w in task_words if w in d_text)
        scored.append((overlap, d))
    scored.sort(key=lambda x: -x[0])
    relevant = [d for score, d in scored[:5] if score > 0 or len(scored) <= 3]

    if relevant:
        lines.append("\n## 相关历史决策")
        for i, d in enumerate(relevant, 1):
            lines.append(f"\n{i}. [{d.status}] {d.title} ({d.created_at.strftime('%Y-%m-%d')})")
            if d.decision:
                lines.append(f"   - 决策: {d.decision}")
            if d.rationale:
                lines.append(f"   - 理由: {d.rationale}")
            if d.alternatives:
                for alt in d.alternatives:
                    name = alt.get("name", "?")
                    reason = alt.get("reason_rejected", "")
                    lines.append(f"   - 备选: {name}（否决: {reason}）")
            if d.constraints:
                lines.append(f"   - 约束: {', '.join(d.constraints)}")

    # 3. Relevant experiences — vector search when available
    exp_results = store.search(project, task, limit=3, query_vector=query_vector)
    exp_hits = [h for h in exp_results if h.get("entity_type") == "experience"]
    if exp_hits:
        lines.append("\n## 相关经验教训")
        for h in exp_hits:
            title = h.get("title", "")
            snippet = h.get("snippet", "")
            lines.append(f"- {title}: {snippet}")

    if not lines:
        return f"项目 '{project}' 暂无相关记录。这是该项目的第一条记录，直接开始工作即可。"

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
# MCP Server
# ═══════════════════════════════════════════════════════

_SCAP_INSTRUCTIONS = """\
You are equipped with SCAP — a project memory system.

## HOW IT WORKS

Your project context (decisions, conventions, lessons) is stored in a
context.md file that is automatically included in your system prompt.
You already have it — no need to call scap_recall.

## WORK PROTOCOL

### BEFORE starting a task:
Read the project context already in your system prompt.
Only call scap_recall if you need task-specific filtering.

### AFTER making a significant decision:
Call scap_remember to record it.
The context.md file updates automatically.

### AFTER learning a lesson:
Call scap_record_experience to capture it.
The context.md file updates automatically.

### WHAT TO RECORD (judge by: will this affect future decisions?):
  ✓ Tech choices ("we chose Kafka over RabbitMQ because...")
  ✓ Architecture decisions ("using event sourcing for orders")
  ✓ Bug root causes + fix patterns ("N+1 queries cause CPU spikes")
  ✓ Team conventions ("all state changes go through event log")
  ✗ Skip: trivial Q&A, code generation, general knowledge
"""

mcp = FastMCP("SCAP", instructions=_SCAP_INSTRUCTIONS)


@mcp.tool()
async def scap_recall(project: str, task_description: str) -> str:
    """Look up project memory before starting a task.

    ALWAYS call this first. It returns:
    - Project tech stack, conventions, and active goals
    - Related past decisions (with rationale and rejected alternatives)
    - Related experience lessons

    Args:
        project: Project name (e.g. "acme-pay", "my-frontend")
        task_description: What you're about to work on (1-2 sentences)
    """
    store = _get_store()
    # Generate query embedding for semantic search (gracefully degrades to None)
    query_vector = _try_embed(task_description)
    result = _format_recall(store, project, task_description, query_vector=query_vector)
    return json.dumps({"success": True, "context": result}, ensure_ascii=False)


@mcp.tool()
async def scap_remember(
    project: str,
    title: str,
    decision: str,
    rationale: str = "",
) -> str:
    """Record a project decision. Call this after making a significant choice.

    Keep it concise — just the key facts. You don't need to fill every field.

    Args:
        project: Project name
        title: Short title, e.g. "消息队列选型"
        decision: What was chosen, e.g. "Kafka"
        rationale: Why, e.g. "吞吐量需求 50k msg/s, RabbitMQ 在 10k+ 时性能下降"
    """
    store = _get_store()
    _ensure_project(store, project)
    try:
        d = Decision(
            project=project,
            title=title,
            decision=decision,
            rationale=rationale,
        )
        # Generate embedding before save (stored in decisions.embedding column)
        d.embedding = _try_embed(f"{title} {decision} {rationale}")
        d = store.save_decision(d)
        # Save latent trace for vector search
        _save_trace(store, d)
        _auto_export(store, project)
        return json.dumps({
            "success": True,
            "decision_id": d.id,
            "embedded": d.embedding is not None,
            "message": f"Recorded: {d.id} — {d.title}",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


@mcp.tool()
async def scap_record_experience(
    project: str,
    situation: str,
    action: str,
    lesson: str,
    tags: str = "",
) -> str:
    """Record a lesson learned from experience. Call when you discover
    a pattern worth remembering — e.g. a bug root cause, a performance
    insight, or a workflow improvement.

    Args:
        project: Project name
        situation: What happened (e.g. "上线后 CPU 飙到 90%")
        action: What was done (e.g. "加了 ReadOnly 注解 + fetch join")
        lesson: What to remember (e.g. "JPA 查询必须加 @EntityGraph 防 N+1")
        tags: Optional comma-separated tags (e.g. "性能,JPA,N+1")
    """
    store = _get_store()
    _ensure_project(store, project)
    try:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        e = Experience(
            project=project,
            situation=situation,
            action=action,
            lesson=lesson,
            tags=tag_list,
        )
        # Generate embedding before save (stored in experiences.embedding column)
        e.embedding = _try_embed(f"{situation} {action} {lesson}")
        e = store.save_experience(e)
        # Save latent trace for vector search
        _save_trace(store, e)
        _auto_export(store, project)
        return json.dumps({
            "success": True,
            "experience_id": e.id,
            "embedded": e.embedding is not None,
            "message": f"Recorded: {e.id} — {lesson[:50]}",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


@mcp.tool()
async def scap_context(project: str) -> str:
    """Get full project snapshot — tech stack, conventions, goals,
    and all active decisions.

    Use this at session start to understand the project.

    Args:
        project: Project name
    """
    store = _get_store()
    ctx = store.get_project_context(project)
    decisions = store.list_decisions(project=project, status="active", limit=50)
    stats = store.get_stats(project=project)

    if not ctx and not decisions:
        return json.dumps({
            "success": True,
            "found": False,
            "message": f"项目 '{project}' 暂无记录。开始工作并调用 scap_remember 来积累。",
        }, ensure_ascii=False)

    result = {
        "success": True,
        "found": True,
        "project": project,
        "tech_stack": ctx.tech_stack if ctx else [],
        "conventions": ctx.conventions if ctx else [],
        "active_goals": ctx.active_goals if ctx else [],
        "decisions": [
            {"id": d.id, "title": d.title, "decision": d.decision[:100]}
            for d in decisions
        ],
        "stats": stats,
    }
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def scap_status() -> str:
    """Check SCAP is running and see all recorded projects."""
    store = _get_store()
    stats = store.get_stats()
    return json.dumps({
        "success": True,
        "status": "running",
        "total_decisions": stats["decision_count"],
        "total_experiences": stats["experience_count"],
        "projects": stats["projects"],
        "latent_trace_count": stats.get("latent_trace_count", 0),
        "evolution_gen": stats.get("evolution_gen", 0),
    }, ensure_ascii=False)


# ═══════════════════════════════════════════════════════
# Latent Space Evolution Tools (Phase 2)
# ═══════════════════════════════════════════════════════


@mcp.tool()
async def scap_retrieve_latent(
    project: str,
    query: str,
    limit: int = 5,
) -> str:
    """Semantic similarity search over project memory using latent vectors.

    Finds decisions and experiences that are semantically similar to the query,
    even when keyword search would miss them. Requires sentence-transformers
    (install with: pip install scap-engine-v2[evolution]).

    Use this when keyword-based scap_recall doesn't surface what you need —
    e.g. searching "database performance" might find "N+1 query optimization"
    even without exact keyword overlap.

    Args:
        project: Project name
        query: Natural language query (e.g. "database performance issues")
        limit: Max results (default 5, max 20)
    """
    store = _get_store()
    limit = min(max(limit, 1), 20)

    query_vector = _try_embed(query)
    if query_vector is None:
        return json.dumps({
            "success": False,
            "error": (
                "Embedding model not available. Install with: "
                "pip install scap-engine-v2[evolution]"
            ),
        }, ensure_ascii=False)

    results = store.search_by_vector(project, query_vector, limit)
    return json.dumps({
        "success": True,
        "query": query,
        "count": len(results),
        "results": results,
    }, ensure_ascii=False)


@mcp.tool()
async def scap_consolidate(
    project: str,
    similarity_threshold: float = 0.85,
) -> str:
    """Consolidate similar latent traces, advancing the evolution generation.

    Finds pairs of latent traces with high cosine similarity and merges them:
    - The higher-fitness trace survives and gets evolution_gen + 1
    - The lower-fitness duplicate is deleted from latent_traces
    - Original Decision/Experience records are preserved untouched

    This is the "nighttime consolidation" step inspired by Mind Evolution:
    weaker memories are pruned, stronger ones are reinforced. Call this
    periodically (e.g. end of session) to keep the latent space clean.

    Args:
        project: Project name
        similarity_threshold: Cosine similarity above which traces are
            merged (default 0.85, range 0.5-0.99)
    """
    store = _get_store()
    similarity_threshold = min(max(similarity_threshold, 0.5), 0.99)

    traces = store.list_latent_traces(project=project, limit=500)
    if len(traces) < 2:
        return json.dumps({
            "success": True,
            "project": project,
            "merged": 0,
            "message": "Not enough traces to consolidate (need ≥ 2).",
        }, ensure_ascii=False)

    # Sort by fitness descending — higher fitness survives
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
            if sim >= similarity_threshold:
                # Survivor wins — advance its evolution generation
                survivor.evolution_gen += 1
                store.save_latent_trace(survivor)
                # Prune the weaker trace
                store.delete_latent_trace(trace.id)
                merged_ids.append(trace.id)
                merged_count += 1
                is_duplicate = True
                break

        if not is_duplicate:
            survivors.append(trace)

    max_gen = max((t.evolution_gen for t in survivors), default=0)
    return json.dumps({
        "success": True,
        "project": project,
        "total_traces": len(traces),
        "merged": merged_count,
        "surviving": len(survivors),
        "new_evolution_gen": max_gen,
    }, ensure_ascii=False)


@mcp.tool()
async def scap_evolved_context(
    project: str,
    task_description: str = "",
    min_fitness: float = 0.0,
) -> str:
    """Retrieve evolved project context with fitness-weighted ranking.

    Returns project memory ranked by latent space fitness and evolution
    generation, showing which decisions and experiences have been
    "evolved" (consolidated/reinforced) over time.

    When task_description is provided, results are further filtered by
    semantic similarity to the task. Use this for complex tasks where
    you want the most refined, battle-tested context.

    Args:
        project: Project name
        task_description: Optional task context for semantic filtering
        min_fitness: Minimum fitness score (0.0-1.0), default 0.0 (all)
    """
    store = _get_store()
    min_fitness = min(max(min_fitness, 0.0), 1.0)

    traces = store.list_latent_traces(project=project, limit=500)
    traces = [t for t in traces if t.fitness >= min_fitness]

    if not traces:
        return json.dumps({
            "success": True,
            "project": project,
            "returned": 0,
            "results": [],
            "message": "No latent traces found for this project.",
        }, ensure_ascii=False)

    # Optional semantic filtering by task similarity
    task_vector = _try_embed(task_description) if task_description else None

    # Score: fitness * (1 + evolution_gen * 0.1) + optional similarity bonus
    scored = []
    for t in traces:
        base_score = t.fitness * (1 + t.evolution_gen * 0.1)
        if task_vector:
            sim = MemoryStore._cosine_similarity(task_vector, t.embedding)
            base_score += sim * 0.3  # similarity as a bonus factor
        scored.append((base_score, t))
    scored.sort(key=lambda x: x[0], reverse=True)

    # Build enriched results
    results = []
    for score, t in scored[:20]:
        hit = {
            "entity_id": t.entity_id,
            "entity_type": t.entity_type,
            "fitness": t.fitness,
            "evolution_gen": t.evolution_gen,
            "score": round(score, 4),
        }
        if t.entity_type == "decision":
            d = store.get_decision(t.entity_id)
            if d:
                hit["title"] = d.title
                hit["decision"] = d.decision[:100] if d.decision else ""
        elif t.entity_type == "experience":
            exp = store.get_experience(t.entity_id)
            if exp:
                hit["title"] = exp.situation[:80] if exp.situation else ""
                hit["lesson"] = exp.lesson[:100] if exp.lesson else ""
        results.append(hit)

    stats = store.get_stats(project=project)
    return json.dumps({
        "success": True,
        "project": project,
        "evolution_gen": stats.get("evolution_gen", 0),
        "total_traces": stats.get("latent_trace_count", 0),
        "filtered_traces": len(traces),
        "returned": len(results),
        "results": results,
    }, ensure_ascii=False)


# ── Entry point ──

def main():
    import argparse
    parser = argparse.ArgumentParser(description="SCAP v2 MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logger.info(f"SCAP v2 MCP Server starting (transport={args.transport})")
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
