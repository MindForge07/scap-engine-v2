"""SCAP v2 — SQLite + FTS5 storage layer with latent vector support.

Reuses proven patterns from v1:
  - WAL mode for concurrent reads
  - FTS5 full-text search with Chinese fallback (bigram)
  - Thread-safe connection lazy init

Phase 1 additions:
  - latent_traces table for vector storage
  - Four-tier search fallback: exact → FTS5 → vector similarity → LIKE
  - Cosine similarity in pure Python (no numpy dependency)
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from scap.models import Decision, ProjectContext, Experience, LatentTrace

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
                insights TEXT DEFAULT '[]',
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

            CREATE TABLE IF NOT EXISTS latent_traces (
                id TEXT PRIMARY KEY,
                entity_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                project TEXT NOT NULL,
                embedding TEXT NOT NULL DEFAULT '[]',
                fitness REAL NOT NULL DEFAULT 0.5,
                evolution_gen INTEGER NOT NULL DEFAULT 0,
                source_tasks TEXT DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_lt_project ON latent_traces(project);
            CREATE INDEX IF NOT EXISTS idx_lt_entity ON latent_traces(entity_id);

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
            ("decisions", "embedding", "TEXT"),
            ("decisions", "evolution_gen", "INTEGER DEFAULT 0"),
            ("decisions", "importance", "INTEGER DEFAULT 3"),
            ("decisions", "source_session", "TEXT DEFAULT ''"),
            ("experiences", "tags", "TEXT DEFAULT '[]'"),
            ("experiences", "embedding", "TEXT"),
            ("experiences", "evolution_gen", "INTEGER DEFAULT 0"),
            ("experiences", "importance", "INTEGER DEFAULT 3"),
            ("experiences", "source_session", "TEXT DEFAULT ''"),
            ("project_contexts", "insights", "TEXT DEFAULT '[]'"),
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
            if not d.updated_at:
                d.updated_at = datetime.now(timezone.utc)
            # Explicit timestamps are respected (history import / replay), so a
            # record can stay "last updated" at its real time.

            self.conn.execute(
                """INSERT OR REPLACE INTO decisions
                   (id, project, title, context, decision, rationale,
                    alternatives, constraints, status, superseded_by, tags,
                    created_at, updated_at, embedding, evolution_gen,
                    importance, source_session)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    d.id, d.project, d.title, d.context, d.decision, d.rationale,
                    json.dumps(d.alternatives, ensure_ascii=False),
                    json.dumps(d.constraints, ensure_ascii=False),
                    d.status, d.superseded_by,
                    json.dumps(d.tags, ensure_ascii=False),
                    d.created_at.isoformat(), d.updated_at.isoformat(),
                    json.dumps(d.embedding) if d.embedding else None,
                    d.evolution_gen,
                    d.importance, d.source_session,
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
        self, project: str = "", status: str = "", limit: int = 50,
        importance_first: bool = False,
    ) -> List[Decision]:
        clauses, params = [], []
        if project:
            clauses.append("project = ?")
            params.append(project)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        order = "importance DESC, updated_at DESC" if importance_first else "updated_at DESC"
        rows = self.conn.execute(
            f"SELECT * FROM decisions{where} ORDER BY {order} LIMIT ?",
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
            embedding=json.loads(r["embedding"]) if r["embedding"] else None,
            evolution_gen=r["evolution_gen"] if "evolution_gen" in r.keys() else 0,
            importance=r["importance"] if "importance" in r.keys() else 3,
            source_session=r["source_session"] if "source_session" in r.keys() else "",
        )

    # ── ProjectContext CRUD ──

    def update_project_context(self, ctx: ProjectContext) -> None:
        ctx.updated_at = datetime.now(timezone.utc)
        with self._lock:
            self.conn.execute(
                """INSERT OR REPLACE INTO project_contexts
                   (project, tech_stack, conventions, active_goals, insights, updated_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    ctx.project,
                    json.dumps(ctx.tech_stack, ensure_ascii=False),
                    json.dumps(ctx.conventions, ensure_ascii=False),
                    json.dumps(ctx.active_goals, ensure_ascii=False),
                    json.dumps(ctx.insights, ensure_ascii=False),
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
            insights=json.loads(row["insights"]) if "insights" in row.keys() else [],
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
                   (id, project, situation, action, lesson, tags, created_at,
                    embedding, evolution_gen, importance, source_session)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    e.id, e.project, e.situation, e.action, e.lesson,
                    json.dumps(e.tags, ensure_ascii=False),
                    e.created_at.isoformat(),
                    json.dumps(e.embedding) if e.embedding else None,
                    e.evolution_gen,
                    e.importance, e.source_session,
                ),
            )
            self._sync_fts(e.id, "experience", e.project,
                           title="", context=e.situation,
                           decision=e.action, rationale="",
                           lesson=e.lesson, tags=" ".join(e.tags))
            self.conn.commit()
        return e

    def get_experience(self, experience_id: str) -> Optional[Experience]:
        """Get a single experience by ID."""
        row = self.conn.execute(
            "SELECT * FROM experiences WHERE id = ?", (experience_id,)
        ).fetchone()
        return self._row_to_experience(row) if row else None

    # ── Search ──

    def search(self, project: str, query: str, limit: int = 10,
               query_vector: Optional[List[float]] = None) -> List[Dict[str, Any]]:
        """Four-tier search: FTS5 → CJK bigram FTS5 → vector similarity → LIKE.

        Args:
            project: Optional project scope filter.
            query: Text query for FTS5/LIKE search.
            limit: Maximum results to return.
            query_vector: Optional embedding vector for semantic similarity search.
                         When provided and FTS5 returns no results, falls back to
                         vector similarity before LIKE search.
        """
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

        # Tier 3: Vector similarity search (if query vector provided)
        if query_vector:
            vec_results = self.search_by_vector(project, query_vector, limit)
            if vec_results:
                return vec_results

        # Tier 4: Fallback LIKE search
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

    # ── LatentTrace CRUD (Phase 1) ──

    def save_latent_trace(self, trace: LatentTrace) -> LatentTrace:
        """Save or update a latent trace record."""
        with self._lock:
            if not trace.id:
                trace.id = self._next_id("LT", "latent_traces")
            if not trace.created_at:
                trace.created_at = datetime.now(timezone.utc)

            self.conn.execute(
                """INSERT OR REPLACE INTO latent_traces
                   (id, entity_id, entity_type, project, embedding,
                    fitness, evolution_gen, source_tasks, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    trace.id, trace.entity_id, trace.entity_type,
                    trace.project,
                    json.dumps(trace.embedding),
                    trace.fitness, trace.evolution_gen,
                    json.dumps(trace.source_tasks, ensure_ascii=False),
                    trace.created_at.isoformat(),
                ),
            )
            self.conn.commit()
        return trace

    def get_latent_trace(self, trace_id: str) -> Optional[LatentTrace]:
        """Get a latent trace by ID."""
        row = self.conn.execute(
            "SELECT * FROM latent_traces WHERE id = ?", (trace_id,)
        ).fetchone()
        return self._row_to_latent_trace(row) if row else None

    def delete_latent_trace(self, trace_id: str) -> bool:
        """Delete a latent trace by ID. Returns True if a row was deleted."""
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM latent_traces WHERE id = ?", (trace_id,)
            )
            self.conn.commit()
            return cur.rowcount > 0

    def update_fitness(self, entity_id: str, helpful: bool) -> Optional[LatentTrace]:
        """Apply feedback to a memory record's latent trace (closed loop).

        EMA-updates the trace's fitness toward 1.0 (helpful) or 0.0
        (unhelpful) and nudges the owning Decision/Experience importance in
        the same direction (bounded 1..5), so feedback flows into
        consolidate / evolved_context and the injected ranking.

        Returns the updated trace, or None when the entity has no latent
        trace (no embedding was ever generated).
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM latent_traces WHERE entity_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (entity_id,),
            ).fetchone()
            if not row:
                return None
            trace = self._row_to_latent_trace(row)
            target = 1.0 if helpful else 0.0
            trace.fitness = round(0.8 * trace.fitness + 0.2 * target, 4)
            self.conn.execute(
                "UPDATE latent_traces SET fitness = ? WHERE id = ?",
                (trace.fitness, trace.id),
            )
            delta = 1 if helpful else -1
            if trace.entity_type == "decision":
                self.conn.execute(
                    "UPDATE decisions SET importance = MAX(1, MIN(5, importance + ?)) WHERE id = ?",
                    (delta, entity_id),
                )
            elif trace.entity_type == "experience":
                self.conn.execute(
                    "UPDATE experiences SET importance = MAX(1, MIN(5, importance + ?)) WHERE id = ?",
                    (delta, entity_id),
                )
            self.conn.commit()
        return trace

    def list_latent_traces(self, project: str = "", entity_id: str = "",
                           limit: int = 50) -> List[LatentTrace]:
        """List latent traces, optionally filtered by project or entity."""
        clauses, params = [], []
        if project:
            clauses.append("project = ?")
            params.append(project)
        if entity_id:
            clauses.append("entity_id = ?")
            params.append(entity_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM latent_traces{where} ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [self._row_to_latent_trace(r) for r in rows]

    def search_by_vector(self, project: str, query_vector: List[float],
                         limit: int = 10,
                         min_similarity: float = 0.15) -> List[Dict[str, Any]]:
        """Vector similarity search using cosine similarity.

        Loads all traces (optionally project-scoped) and ranks by cosine
        similarity to the query vector. Pure Python, no numpy required.

        Args:
            min_similarity: Minimum cosine similarity to include (default 0.15).
                           Real embedding models produce non-zero similarity
                           for nearly all text pairs; this threshold filters
                           noise. Increase to 0.3+ for higher precision.
        """
        if not query_vector:
            return []

        has_project = bool(project and project.strip())
        if has_project:
            rows = self.conn.execute(
                "SELECT * FROM latent_traces WHERE project = ? ORDER BY created_at DESC",
                (project,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM latent_traces ORDER BY created_at DESC"
            ).fetchall()

        if not rows:
            return []

        scored = []
        for row in rows:
            embedding = json.loads(row["embedding"])
            if not embedding:
                continue
            sim = self._cosine_similarity(query_vector, embedding)
            scored.append((sim, row))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for sim, row in scored[:limit]:
            if sim < min_similarity:
                continue
            hit = {
                "entity_id": row["entity_id"],
                "entity_type": row["entity_type"],
                "similarity": round(sim, 4),
                "evolution_gen": row["evolution_gen"],
                "fitness": row["fitness"],
            }
            # Enrich with entity data
            if row["entity_type"] == "decision":
                d = self.get_decision(row["entity_id"])
                if d:
                    hit["title"] = d.title
                    hit["snippet"] = f"{d.decision[:100]}. 理由: {d.rationale[:80]}"
                    hit["status"] = d.status
            elif row["entity_type"] == "experience":
                exp_row = self.conn.execute(
                    "SELECT * FROM experiences WHERE id = ?", (row["entity_id"],)
                ).fetchone()
                if exp_row:
                    hit["title"] = exp_row["situation"][:80]
                    hit["snippet"] = exp_row["lesson"][:150]
            results.append(hit)

        return results

    @staticmethod
    def _row_to_latent_trace(r) -> LatentTrace:
        """Convert a SQLite row to a LatentTrace model."""
        return LatentTrace(
            id=r["id"],
            entity_id=r["entity_id"],
            entity_type=r["entity_type"],
            project=r["project"],
            embedding=json.loads(r["embedding"]),
            fitness=r["fitness"],
            evolution_gen=r["evolution_gen"],
            source_tasks=json.loads(r["source_tasks"]),
            created_at=datetime.fromisoformat(r["created_at"]),
        )

    @staticmethod
    def _cosine_similarity(v1: List[float], v2: List[float]) -> float:
        """Compute cosine similarity between two vectors (pure Python)."""
        if not v1 or not v2 or len(v1) != len(v2):
            return 0.0
        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = math.sqrt(sum(a * a for a in v1))
        norm2 = math.sqrt(sum(b * b for b in v2))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

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
        lt_count = self.conn.execute(
            f"SELECT COUNT(*) FROM latent_traces{where}", params
        ).fetchone()[0]
        max_gen = self.conn.execute(
            f"SELECT COALESCE(MAX(evolution_gen), 0) FROM latent_traces{where}", params
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
            "latent_trace_count": lt_count,
            "evolution_gen": max_gen,
            "projects": projects,
        }

    # ── Context export ──

    def export_context(self, project: str, output_path: str,
                       max_chars: int = 0) -> str:
        """Export project context as a markdown file for system prompt injection.

        This file is meant to be included in the AI's system prompt so that
        scap_recall becomes unnecessary — the context is pre-loaded.

        max_chars bounds the rendered markdown (0 = uncapped): entries are
        emitted newest-first and emission stops once the budget is exhausted,
        so a large project cannot blow up the system prompt. Decisions that
        repeat an already-emitted (title, decision) pair are folded away.

        Returns the path of the written file.
        """
        lines: list[str] = []
        budget_used = 0

        def add(text: str) -> bool:
            """Append one block; False when the max_chars budget is exhausted."""
            nonlocal budget_used
            if max_chars > 0 and text and budget_used + len(text) > max_chars:
                return False
            lines.append(text)
            budget_used += len(text)
            return True

        add(f"# Project Memory: {project}")
        add("")

        ctx = self.get_project_context(project)
        if ctx:
            if ctx.tech_stack:
                add(f"## Tech Stack\n{', '.join(ctx.tech_stack)}")
                add("")
            if ctx.conventions:
                add("## Conventions")
                for c in ctx.conventions:
                    if not add(f"- {c}"):
                        break
                add("")
            if ctx.active_goals:
                add("## Active Goals")
                for g in ctx.active_goals:
                    if not add(f"- {g}"):
                        break
                add("")
            if ctx.insights:
                add("## Insights")
                for i in ctx.insights:
                    if not add(f"- {i}"):
                        break
                add("")

        decisions = self.list_decisions(
            project=project, status="active", limit=200, importance_first=True,
        )
        seen_pairs: set[tuple[str, str]] = set()
        if decisions:
            add("## Decisions")
            for d in decisions:
                pair = (d.title, d.decision)
                if pair in seen_pairs:
                    continue  # fold exact duplicates
                block = [f"\n### {d.title} ({d.created_at.strftime('%Y-%m-%d')})"]
                if d.decision:
                    block.append(f"**Chosen:** {d.decision}")
                if d.rationale:
                    block.append(f"**Why:** {d.rationale}")
                if d.alternatives:
                    for alt in d.alternatives:
                        name = alt.get("name", "?")
                        reason = alt.get("reason_rejected", "")
                        block.append(f"- ~~{name}~~ (rejected: {reason})")
                if d.constraints:
                    block.append(f"**Constraints:** {', '.join(d.constraints)}")
                if not add("\n".join(block)):
                    break
                seen_pairs.add(pair)
            add("")

        experiences = self.list_experiences(project=project, limit=50)
        if experiences:
            add("## Lessons Learned")
            for exp in experiences:
                block = [f"\n- **{exp.situation}**"]
                if exp.action:
                    block.append(f"  Action: {exp.action}")
                block.append(f"  → {exp.lesson}")
                if not add("\n".join(block)):
                    break
            add("")

        content = "\n".join(lines)

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
            embedding=json.loads(r["embedding"]) if r["embedding"] else None,
            evolution_gen=r["evolution_gen"] if "evolution_gen" in r.keys() else 0,
            importance=r["importance"] if "importance" in r.keys() else 3,
            source_session=r["source_session"] if "source_session" in r.keys() else "",
        )
