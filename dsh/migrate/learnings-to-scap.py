"""Migrate legacy v1-era learnings (.learnings/*.md) into current SCAP records.

Legacy hand-written markdown (LRN/ERR/FR blocks) is converted into current
Experience / Decision records so the layered injection picks them up:

  LRN-* / ERR-*  -> Experience (situation=Summary+Details, action=Suggested
                    action/fix, lesson=distilled lesson, importance from
                    Priority: high=5 medium=4)
  FR-*           -> Decision (the validated choice, e.g. .mcp.json placement)

Idempotent: skips records whose (project, title) / (project, situation)
already exist — same NOOP semantics as the four-op write strategy.

The legacy data/scap.db (v2.0 era) is NOT migrated: it only contains the
README demo project (acme-pay 消息队列/支付网关/数据库选型), which is
demo material, not real accumulation. Importing it would violate the
memory-correctness gate.

Usage:
  python dsh/migrate/learnings-to-scap.py [--db PATH] [--project NAME]
                                          [--learnings-dir DIR]

Defaults: production DB (.dsh/scap-exports/data/scap.db), project XDXLC.
Re-exports {project}.md + .json after writing so injection reflects it.

Safe against the running scap server: SQLite WAL allows a short writer.
"""
import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scap.models import Decision, Experience  # noqa: E402
from scap.store import MemoryStore  # noqa: E402

DEFAULT_DB = r"C:\Users\XDXLC\.dsh\scap-exports\data\scap.db"
DEFAULT_PROJECT = "XDXLC"
DEFAULT_LEARNINGS = REPO / ".learnings"

# ── Distilled lessons (quality gate: never import unexamined text). ──
LESSONS = {
    "LRN-20260627-001": (
        "Windows 上调用 Ollama API 必须用 requests 而非 httpx（httpx 返回 502）；本地模型 API 不要用 httpx",
        5),
    "LRN-20260627-002": (
        "Ollama thinking 模型即使 stream:false 也输出 NDJSON（response/thinking 逐行）；"
        "response 只在 thinking 结束后出现；num_predict 要给足（≥2048，解题用 8192）",
        5),
    "LRN-20260627-003": (
        "FTS5 虚拟表不支持 ALTER TABLE ADD COLUMN；加列需探测列失败后 DROP 重建 + rebuild_fts（数据可重建，安全）",
        4),
    "LRN-20260627-004": (
        "Windows GBK 终端下 rich 打印 Unicode（如 ✓）会崩溃；CLI 入口先 "
        "sys.stdout.reconfigure(encoding='utf-8', errors='replace')",
        4),
    "ERR-20260627-001": (
        "save_experience 的 ID 生成与 INSERT 必须共用一个锁块，否则并发下重复 ID 被 INSERT OR REPLACE 静默覆盖",
        5),
    "ERR-20260627-002": (
        "search 的 project 过滤子句必须条件化：project 为空时不加 AND project = ?，否则永远零结果",
        4),
}

# FR entries become Decisions: id -> (title, decision, rationale, importance)
FR_DECISIONS = {
    "FR-20260627-001": (
        "MCP 服务器配置方式",
        "用 .mcp.json（项目根目录或 cwd）配置 MCP 服务器，不用 settings.json",
        "settings.json 无 mcpServers 字段；.mcp.json 首次发现需用户批准；SCAP v2 实测配置后不出现，放对位置才出现",
        4),
}

ENTRY_RE = re.compile(r"^##\s+(\S+):\s*(.+)$")
FIELD_RE = re.compile(r"^-\s+\*\*([^*]+)\*\*\s*:\s*(.*)$")


def parse_learnings(dir_path: Path) -> list[tuple[str, str, dict]]:
    """Parse all .learnings/*.md into [(entry_id, title, fields_dict)]."""
    entries: list[tuple[str, str, dict]] = []
    for md in sorted(dir_path.glob("*.md")):
        current: tuple[str, str, dict] | None = None
        for line in md.read_text(encoding="utf-8").splitlines():
            m = ENTRY_RE.match(line)
            if m:
                if current:
                    entries.append(current)
                current = (m.group(1), m.group(2), {})
                continue
            if current:
                f = FIELD_RE.match(line)
                if f:
                    current[2][f.group(1).strip()] = f.group(2).strip()
        if current:
            entries.append(current)
    return entries


def priority_importance(priority: str, fallback: int) -> int:
    return {"high": 5, "medium": 4, "low": 3}.get(priority.strip().lower(), fallback)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB, help="target SQLite DB")
    ap.add_argument("--project", default=DEFAULT_PROJECT, help="target project namespace")
    ap.add_argument("--learnings-dir", default=str(DEFAULT_LEARNINGS))
    ap.add_argument("--dry-run", action="store_true", help="parse and report only")
    args = ap.parse_args()

    entries = parse_learnings(Path(args.learnings_dir))
    print(f"parsed {len(entries)} entries from {args.learnings_dir}")

    store = MemoryStore(args.db)
    store.initialize()

    new_exp, new_dec, skipped = 0, 0, 0
    for entry_id, title, f in entries:
        prefix = entry_id.split("-")[0]
        if prefix in ("LRN", "ERR"):
            lesson, imp = LESSONS.get(
                entry_id, (f.get("Summary", title), priority_importance(f.get("Priority", ""), 3)))
            if prefix == "ERR":
                imp = LESSONS.get(entry_id, (None, priority_importance(f.get("Priority", ""), 3)))[1]
            situation = f.get("Summary", title)
            extra = " ".join(v for k, v in (("Details", f.get("Details")),
                                            ("Context", f.get("Context")),
                                            ("Error text", f.get("Error text"))) if v)
            if extra:
                situation = f"{situation}\n{extra}"
            action = f.get("Suggested action") or f.get("Suggested fix") or ""
            tags = [t.strip() for t in f.get("Tags", "").split(",") if t.strip()]
            tags += [t for t in (f.get("Name"), f.get("Area")) if t] + ["legacy"]
            created = datetime(2026, 6, 27, tzinfo=timezone.utc)
            dup = store.conn.execute(
                "SELECT 1 FROM experiences WHERE project = ? AND situation = ? LIMIT 1",
                (args.project, situation)).fetchone()
            if dup:
                print(f"  skip  {entry_id} (existing)")
                skipped += 1
                continue
            e = Experience(project=args.project, situation=situation, action=action,
                           lesson=lesson, importance=imp, tags=tags, created_at=created)
            if not args.dry_run:
                store.save_experience(e)
            new_exp += 1
            print(f"  exp   {entry_id} [{imp}] {title}")
        elif prefix == "FR":
            t, decision, rationale, imp = FR_DECISIONS[entry_id]
            tags = [t2.strip() for t2 in f.get("Capability", "").split(",") if t2.strip()]
            tags += [t2 for t2 in (f.get("Area"),) if t2] + ["legacy"]
            created = datetime(2026, 6, 27, tzinfo=timezone.utc)
            dup = store.conn.execute(
                "SELECT 1 FROM decisions WHERE project = ? AND title = ? LIMIT 1",
                (args.project, t)).fetchone()
            if dup:
                print(f"  skip  {entry_id} (existing decision '{t}')")
                skipped += 1
                continue
            d = Decision(project=args.project, title=t, context=f.get("Summary", ""),
                         decision=decision, rationale=rationale, importance=imp,
                         tags=tags, created_at=created, updated_at=created)
            if not args.dry_run:
                store.save_decision(d)
            new_dec += 1
            print(f"  dec   {entry_id} [{imp}] {t}")
        else:
            print(f"  ?     {entry_id}: unknown prefix, skipped")

    if args.dry_run:
        print(f"dry-run: {new_exp} experiences, {new_dec} decisions, {skipped} skipped")
        store.close()
        return 0

    stats = store.get_stats(project=args.project)
    print(f"migrated {new_exp} experiences + {new_dec} decisions, {skipped} skipped")
    print(f"project {args.project}: decisions={stats['decision_count']} "
          f"experiences={stats['experience_count']}")

    export_md = Path(os.environ.get("SCAP_EXPORT_DIR") or
                     Path(args.db).parent.parent / ".scap") / f"{args.project}.md"
    export_md.parent.mkdir(parents=True, exist_ok=True)
    path = store.export_context(args.project, str(export_md), with_json=True)
    print("export:", path)
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
