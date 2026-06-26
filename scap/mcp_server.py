"""SCAP v2 — MCP server. 5 tools, designed for AI self-recording."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

from scap.models import Decision, ProjectContext, Experience
from scap.store import MemoryStore

logger = logging.getLogger(__name__)

_store: MemoryStore | None = None
# Default export path — per-project .scap/context.md
_EXPORT_DIR = os.environ.get("SCAP_EXPORT_DIR", ".scap")


def _get_store() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore()
        _store.initialize()
    return _store


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


def _format_recall(store: MemoryStore, project: str, task: str) -> str:
    """Format project memory as injection-ready text for LLM system prompt."""
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

    # 3. Relevant experiences
    exp_results = store.search(project, task, limit=3)
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
    result = _format_recall(store, project, task_description)
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
        d = store.save_decision(d)
        _auto_export(store, project)
        return json.dumps({
            "success": True,
            "decision_id": d.id,
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
        e = store.save_experience(e)
        _auto_export(store, project)
        return json.dumps({
            "success": True,
            "experience_id": e.id,
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
