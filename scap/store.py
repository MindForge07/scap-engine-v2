"""SCAP v2 — SQLite + FTS5 storage layer.

Reuses proven patterns from v1:
  - WAL mode for concurrent reads
  - FTS5 full-text search with Chinese fallback (bigram)
  - Thread-safe connection lazy init
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from scap.models import Decision, ProjectContext, Experience

logger = logging.getLogger(__name__)


class MemoryStore:
    """SQLite-backed project memory store."""

    def __init__(self, db_path: str = "./data/scap.db") -> None:
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            with self._lock:
                if self._conn is None:
                    self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
                    self._conn.row_factory = sqlite3.Row
                    self._conn.execute("PRAGMA journal_mode=WAL")
                    self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Schema ──

    def initialize(self) -> None:
        """Create tables + FTS5 index if they don't exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                title TEXT NOT NULL,
                context TEXT DEFAULT '',
                decision TEXT DEFAULT '',
                rationale TEXT DEFAULT '',
                alternatives TEXT DEFAULT '[]',
                constraints TEXT DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'active',
                superseded_by TEXT,
                tags TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_dec_project ON decisions(project);
            CREATE INDEX IF NOT EXISTS idx_dec_status ON decisions(status);

            CREATE TABLE IF NOT EXISTS project_contexts (
                project TEXT PRIMARY KEY,
                tech_stack TEXT DEFAULT '[]',
                conventions TEXT DEFAULT '[]',
                active_goals TEXT DEFAULT '[]',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS experiences (
                id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                situation TEXT DEFAULT '',
                action TEXT DEFAULT '',
                lesson TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_exp_project ON experiences(project);

            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                entity_id UNINDEXED,
                entity_type UNINDEXED,
                project UNINDEXED,
                title,
                context,
                decision,
                rationale,
                lesson,
                tags,
                tokenize='unicode61'
            );
        """)
        self.conn.commit()

        # Schema migrations: add new columns to existing tables gracefully
        _migrations = [
            ("decisions", "alternatives", "TEXT DEFAULT '[]'"),
            ("decisions", "constraints", "TEXT DEFAULT '[]'"),
            ("decisions", "tags", "TEXT DEFAULT '[]'"),
            ("decisions", "superseded_by", "TEXT"),
            ("experiences", "tags", "TEXT DEFAULT '[]'"),
        ]
        for table, col, col_type in _migrations:
            try:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass  # column already exists
        self.conn.commit()

        logger.info(f"Memory store initialized: {self.db_path}")

    # ── ID generation ──

    def _next_id(self, prefix: str, table: str) -> str:
        """Generate next ID: PREFIX-YYYYMMDD-NNNN.

        Uses SELECT MAX + retry to avoid duplicate IDs under concurrent access.
        """
        date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
        pattern = f"{prefix}-{date_part}-%"
        try:
            row = self.conn.execute(
                f"SELECT MAX(CAST(SUBSTR(id, -4) AS INTEGER)) FROM {table} WHERE id LIKE ?",
                (pattern,),
            ).fetchone()
            if row and row[0] is not None:
                return f"{prefix}-{date_part}-{row[0] + 1:04d}"
        except Exception as e:
            logger.warning(f"ID generation query failed: {e}")
        return f"{prefix}-{date_part}-0001"

    # ── Decision CRUD ──

    def save_decision(self, d: Decision) -> Decision:
        """Save or update a decision record."""
        with self._lock:
            if not d.id:
                d.id = self._next_id("DC", "decisions")
            if not d.created_at:
                d.created_at = datetime.now(timezone.utc)
            d.updated_at = datetime.now(timezone.utc)

            self.conn.execute(
                """INSERT OR REPLACE INTO decisions
                   (id, project, title, context, decision, rationale,
                    alternatives, constraints, status, superseded_by, tags,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    d.id, d.project, d.title, d.context, d.decision, d.rationale,
                    json.dumps(d.alternatives, ensure_ascii=False),
                    json.dumps(d.constraints, ensure_ascii=False),
                    d.status, d.superseded_by,
                    json.dumps(d.tags, ensure_ascii=False),
                    d.created_at.isoformat(), d.updated_at.isoformat(),
                ),
            )
            self._sync_fts(d.id, "decision", d.project,
                           title=d.title, context=d.context,
                           decision=d.decision, rationale=d.rationale,
                           tags=" ".join(d.tags))
            self.conn.commit()
        return d

    def get_decision(self, decision_id: str) -> Optional[Decision]:
        row = self.conn.execute(
            "SELECT * FROM decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        return self._row_to_decision(row) if row else None

    def list_decisions(
        self, project: str = "", status: str = "", limit: int = 50
    ) -> List[Decision]:
        clauses, params = [], []
        if project:
            clauses.append("project = ?")
            params.append(project)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM decisions{where} ORDER BY updated_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [self._row_to_decision(r) for r in rows]

    def supersede(self, old_id: str, new_decision: Decision) -> Decision:
        """Mark old decision as superseded, link to new one."""
        new_decision = self.save_decision(new_decision)
        self.conn.execute(
            "UPDATE decisions SET status = 'superseded', superseded_by = ?, updated_at = ? WHERE id = ?",
            (new_decision.id, datetime.now(timezone.utc).isoformat(), old_id),
        )
        self.conn.commit()
        return new_decision

    @staticmethod
    def _row_to_decision(r) -> Decision:
        return Decision(
            id=r["id"],
            project=r["project"],
            title=r["title"],
            context=r["context"],
            decision=r["decision"],
            rationale=r["rationale"],
            alternatives=json.loads(r["alternatives"]),
            constraints=json.loads(r["constraints"]),
            status=r["status"],
            superseded_by=r["superseded_by"],
            tags=json.loads(r["tags"]),
            created_at=datetime.fromisoformat(r["created_at"]),
            updated_at=datetime.fromisoformat(r["updated_at"]),
        )

    # ── ProjectContext CRUD ──

    def update_project_context(self, ctx: ProjectContext) -> None:
        ctx.updated_at = datetime.now(timezone.utc)
        with self._lock:
            self.conn.execute(
                """INSERT OR REPLACE INTO project_contexts
                   (project, tech_stack, conventions, active_goals, updated_at)
                   VALUES (?,?,?,?,?)""",
                (
                    ctx.project,
                    json.dumps(ctx.tech_stack, ensure_ascii=False),
                    json.dumps(ctx.conventions, ensure_ascii=False),
                    json.dumps(ctx.active_goals, ensure_ascii=False),
                    ctx.updated_at.isoformat(),
                ),
            )
            self.conn.commit()

    def get_project_context(self, project: str) -> Optional[ProjectContext]:
        row = self.conn.execute(
            "SELECT * FROM project_contexts WHERE project = ?", (project,)
        ).fetchone()
        if not row:
            return None
        return ProjectContext(
            project=row["project"],
            tech_stack=json.loads(row["tech_stack"]),
            conventions=json.loads(row["conventions"]),
            active_goals=json.loads(row["active_goals"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # ── Experience CRUD ──

    def save_experience(self, e: Experience) -> Experience:
        with self._lock:
            if not e.id:
                e.id = self._next_id("EX", "experiences")
            if not e.created_at:
                e.created_at = datetime.now(timezone.utc)

            self.conn.execute(
                """INSERT OR REPLACE INTO experiences
                   (id, project, situation, action, lesson, tags, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    e.id, e.project, e.situation, e.action, e.lesson,
                    json.dumps(e.tags, ensure_ascii=False),
                    e.created_at.isoformat(),
                ),
            )
            self._sync_fts(e.id, "experience", e.project,
                           title="", context=e.situation,
                           decision=e.action, rationale="",
                           lesson=e.lesson, tags=" ".join(e.tags))
            self.conn.commit()
        return e

    # ── Search ──

    def search(self, project: str, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """FTS5 full-text search, optionally project-scoped."""
        has_project = bool(project and project.strip())
        # Build FTS query: try original, then CJK bigram fallback
        queries_to_try = [query]
        bigram = self._fts_chinese_fallback(query)
        if bigram != query:
            queries_to_try.append(bigram)

        for q in queries_to_try:
            try:
                if has_project:
                    rows = self.conn.execute(
                        """SELECT entity_id, entity_type, rank FROM memory_fts
                           WHERE memory_fts MATCH ?
                           AND project = ?
                           ORDER BY rank LIMIT ?""",
                        (q, project, limit),
                    ).fetchall()
                else:
                    rows = self.conn.execute(
                        """SELECT entity_id, entity_type, rank FROM memory_fts
                           WHERE memory_fts MATCH ?
                           ORDER BY rank LIMIT ?""",
                        (q, limit),
                    ).fetchall()
                if rows:
                    return [self._enrich_hit(r) for r in rows]
            except sqlite3.OperationalError:
                continue

        # Fallback: LIKE search
        return self._like_search(project, query, limit)

    def _like_search(self, project: str, query: str, limit: int) -> List[Dict[str, Any]]:
        """Fallback LIKE search across decisions and experiences."""
        pattern = f"%{query}%"
        has_project = bool(project and project.strip())
        results = []

        if has_project:
            dec_rows = self.conn.execute(
                """SELECT id, title, decision FROM decisions
                   WHERE project = ?
                   AND (title LIKE ? OR context LIKE ? OR decision LIKE ? OR rationale LIKE ?)
                   ORDER BY updated_at DESC LIMIT ?""",
                (project, pattern, pattern, pattern, pattern, limit),
            ).fetchall()
            exp_rows = self.conn.execute(
                """SELECT id, situation, lesson FROM experiences
                   WHERE project = ?
                   AND (situation LIKE ? OR action LIKE ? OR lesson LIKE ?)
                   ORDER BY created_at DESC LIMIT ?""",
                (project, pattern, pattern, pattern, limit),
            ).fetchall()
        else:
            dec_rows = self.conn.execute(
                """SELECT id, title, decision FROM decisions
                   WHERE title LIKE ? OR context LIKE ? OR decision LIKE ? OR rationale LIKE ?
                   ORDER BY updated_at DESC LIMIT ?""",
                (pattern, pattern, pattern, pattern, limit),
            ).fetchall()
            exp_rows = self.conn.execute(
                """SELECT id, situation, lesson FROM experiences
                   WHERE situation LIKE ? OR action LIKE ? OR lesson LIKE ?
                   ORDER BY created_at DESC LIMIT ?""",
                (pattern, pattern, pattern, limit),
            ).fetchall()
        for r in dec_rows:
            results.append({
                "entity_id": r["id"],
                "entity_type": "decision",
                "title": r["title"],
                "snippet": (r["decision"] or "")[:150],
            })

        for r in exp_rows:
            results.append({
                "entity_id": r["id"],
                "entity_type": "experience",
                "title": (r["situation"] or "")[:80],
                "snippet": (r["lesson"] or "")[:150],
            })

        return results[:limit]

    def _enrich_hit(self, row) -> Dict[str, Any]:
        """Enrich an FTS hit with full entity data."""
        eid = row["entity_id"]
        etype = row["entity_type"]
        hit = {"entity_id": eid, "entity_type": etype}

        if etype == "decision":
            d = self.get_decision(eid)
            if d:
                hit["title"] = d.title
                hit["snippet"] = f"{d.decision[:100]}. 理由: {d.rationale[:80]}"
                hit["constraints"] = d.constraints
                hit["status"] = d.status
        elif etype == "experience":
            row_exp = self.conn.execute(
                "SELECT * FROM experiences WHERE id = ?", (eid,)
            ).fetchone()
            if row_exp:
                hit["title"] = row_exp["situation"][:80]
                hit["snippet"] = row_exp["lesson"][:150]
        return hit

    # ── FTS sync ──

    def _sync_fts(self, entity_id: str, entity_type: str, project: str,
                  title: str = "", context: str = "", decision: str = "",
                  rationale: str = "", lesson: str = "", tags: str = "") -> None:
        """Upsert FTS5 record for an entity."""
        self.conn.execute(
            "DELETE FROM memory_fts WHERE entity_id = ?", (entity_id,)
        )
        self.conn.execute(
            """INSERT INTO memory_fts
               (entity_id, entity_type, project, title, context, decision, rationale, lesson, tags)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (entity_id, entity_type, project, title, context, decision, rationale, lesson, tags),
        )

    @staticmethod
    def _fts_chinese_fallback(query: str) -> str:
        """Convert Chinese chars to bigram OR while preserving English/Latin terms."""
        cjk = re.findall(r'[一-鿿]', query)
        if len(cjk) < 2:
            return query  # No CJK bigram to generate

        # Extract English/Latin terms to preserve
        latin = re.findall(r'[a-zA-Z0-9_]+', query)

        bigrams = [f'"{cjk[i]}{cjk[i+1]}"' for i in range(len(cjk) - 1)]
        parts = bigrams + latin
        return " OR ".join(parts)

    # ── Stats ──

    def get_stats(self, project: str = "") -> Dict[str, Any]:
        """Aggregate stats, optionally project-scoped."""
        where = " WHERE project = ?" if project else ""
        params = (project,) if project else ()

        dec_count = self.conn.execute(
            f"SELECT COUNT(*) FROM decisions{where}", params
        ).fetchone()[0]
        exp_count = self.conn.execute(
            f"SELECT COUNT(*) FROM experiences{where}", params
        ).fetchone()[0]

        projects = list(set(
            [r[0] for r in self.conn.execute(
                "SELECT DISTINCT project FROM decisions"
            ).fetchall()] +
            [r[0] for r in self.conn.execute(
                "SELECT DISTINCT project FROM experiences"
            ).fetchall()]
        ))

        return {
            "decision_count": dec_count,
            "experience_count": exp_count,
            "projects": projects,
        }

    # ── Context export ──

    def export_context(self, project: str, output_path: str) -> str:
        """Export project context as a markdown file for system prompt injection.

        This file is meant to be included in the AI's system prompt so that
        scap_recall becomes unnecessary — the context is pre-loaded.

        Returns the path of the written file.
        """
        lines = [f"# Project Memory: {project}", ""]

        ctx = self.get_project_context(project)
        if ctx:
            if ctx.tech_stack:
                lines.append(f"## Tech Stack\n{', '.join(ctx.tech_stack)}\n")
            if ctx.conventions:
                lines.append("## Conventions")
                for c in ctx.conventions:
                    lines.append(f"- {c}")
                lines.append("")
            if ctx.active_goals:
                lines.append("## Active Goals")
                for g in ctx.active_goals:
                    lines.append(f"- {g}")
                lines.append("")

        decisions = self.list_decisions(project=project, status="active", limit=200)
        if decisions:
            lines.append("## Decisions")
            for d in decisions:
                lines.append(f"\n### {d.title} ({d.created_at.strftime('%Y-%m-%d')})")
                if d.decision:
                    lines.append(f"**Chosen:** {d.decision}")
                if d.rationale:
                    lines.append(f"**Why:** {d.rationale}")
                if d.alternatives:
                    for alt in d.alternatives:
                        name = alt.get("name", "?")
                        reason = alt.get("reason_rejected", "")
                        lines.append(f"- ~~{name}~~ (rejected: {reason})")
                if d.constraints:
                    lines.append(f"**Constraints:** {', '.join(d.constraints)}")
            lines.append("")

        experiences = self.list_experiences(project=project, limit=50)
        if experiences:
            lines.append("## Lessons Learned")
            for exp in experiences:
                lines.append(f"\n- **{exp.situation}**")
                if exp.action:
                    lines.append(f"  Action: {exp.action}")
                lines.append(f"  → {exp.lesson}")
            lines.append("")

        content = "\n".join(lines)

        import os
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        return output_path

    def list_experiences(self, project: str = "", limit: int = 50) -> List[Experience]:
        """List experience records, optionally project-scoped."""
        if project:
            rows = self.conn.execute(
                "SELECT * FROM experiences WHERE project = ? ORDER BY created_at DESC LIMIT ?",
                (project, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM experiences ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_experience(r) for r in rows]

    @staticmethod
    def _row_to_experience(r) -> Experience:
        return Experience(
            id=r["id"],
            project=r["project"],
            situation=r["situation"],
            action=r["action"],
            lesson=r["lesson"],
            tags=json.loads(r["tags"]),
            created_at=datetime.fromisoformat(r["created_at"]),
        )
