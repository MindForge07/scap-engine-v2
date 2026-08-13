"""P0 memory-quality tests: importance/quality gate, recency decay, fitness loop."""
import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from scap.models import Decision, Experience
from scap.store import MemoryStore
from scap.mcp_server import (
    _decision_relevance,
    _format_recall,
    scap_remember,
    scap_record_experience,
    scap_feedback,
)


def seed(store: MemoryStore, project: str, rows: list[tuple[str, str, str]]) -> None:
    for title, decision, rationale in rows:
        store.save_decision(Decision(
            project=project, title=title, decision=decision, rationale=rationale,
        ))


# ── importance field ──

class TestImportanceField:
    def test_default_importance_is_3(self, store: MemoryStore):
        d = store.save_decision(Decision(project="x", title="A", decision="B"))
        assert d.importance == 3
        assert store.get_decision(d.id).importance == 3

    def test_range_validation(self):
        with pytest.raises(Exception):
            Decision(project="x", title="A", decision="B", importance=0)
        with pytest.raises(Exception):
            Decision(project="x", title="A", decision="B", importance=6)

    def test_roundtrip_importance_and_source(self, store: MemoryStore):
        d = store.save_decision(Decision(
            project="x", title="A", decision="B", importance=5,
            source_session="sess-123",
        ))
        loaded = store.get_decision(d.id)
        assert loaded.importance == 5
        assert loaded.source_session == "sess-123"

    def test_old_db_migration_defaults(self, tmp_path):
        # Simulate a pre-P0 DB: create table without new columns, then initialize().
        import sqlite3
        db = str(tmp_path / "old.db")
        conn = sqlite3.connect(db)
        conn.execute("""CREATE TABLE decisions (
            id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL,
            context TEXT DEFAULT '', decision TEXT DEFAULT '', rationale TEXT DEFAULT '',
            alternatives TEXT DEFAULT '[]', constraints TEXT DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'active', superseded_by TEXT,
            tags TEXT DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        conn.execute(
            "INSERT INTO decisions (id, project, title, created_at, updated_at) VALUES (?,?,?,?,?)",
            ("DC-20260101-0001", "old", "Old", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
        conn.close()
        store = MemoryStore(db)
        store.initialize()
        d = store.get_decision("DC-20260101-0001")
        assert d is not None
        assert d.importance == 3
        assert d.source_session == ""


# ── quality gate (MCP layer) ──

class TestQualityGate:
    @pytest.fixture(autouse=True)
    def _patch_store(self, tmp_path, monkeypatch):
        from scap.mcp_server import _get_store
        db_path = str(tmp_path / "gate.db")
        store = MemoryStore(db_path)
        store.initialize()
        monkeypatch.setattr("scap.mcp_server._store", store)

    @pytest.mark.asyncio
    async def test_empty_decision_rejected(self):
        result = json.loads(await scap_remember("acme", "标题", "  "))
        assert result["success"] is False
        assert "decision 不能为空" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_rationale_downgrades_importance(self):
        result = json.loads(await scap_remember("acme", "无理由决策", "Kafka"))
        assert result["success"] is True
        assert result["importance"] == 2
        assert "降为 2" in result["message"]

    @pytest.mark.asyncio
    async def test_explicit_importance_respected_with_rationale(self):
        result = json.loads(await scap_remember(
            "acme", "关键决策", "Kafka", "吞吐量需求", importance=5,
        ))
        assert result["success"] is True
        assert result["importance"] == 5

    @pytest.mark.asyncio
    async def test_quality_gate_overrides_explicit_importance(self):
        result = json.loads(await scap_remember(
            "acme", "无理由", "Kafka", "", importance=5,
        ))
        assert result["success"] is True
        assert result["importance"] == 2

    @pytest.mark.asyncio
    async def test_importance_clamped(self):
        result = json.loads(await scap_remember(
            "acme", "超界", "Kafka", "理由", importance=99,
        ))
        assert result["success"] is True
        assert result["importance"] == 5

    @pytest.mark.asyncio
    async def test_empty_lesson_rejected(self):
        result = json.loads(await scap_record_experience("acme", "情况", "行动", "  "))
        assert result["success"] is False
        assert "lesson 不能为空" in result["error"]


# ── recency decay ──

class TestRecencyDecay:
    def test_older_decision_scores_lower(self, store: MemoryStore):
        text = "消息队列选型 Kafka 高吞吐量，满足 50k msg/s"
        fresh = _decision_relevance("消息队列选型", text, None, None, age_days=0)
        stale = _decision_relevance("消息队列选型", text, None, None, age_days=90)
        assert fresh > 0
        assert stale > 0
        assert fresh > stale
        # 90 days → ~14% of the fresh weight.
        assert stale < fresh * 0.3

    def test_zero_relevance_stays_zero(self, store: MemoryStore):
        assert _decision_relevance("写诗", "消息队列选型 Kafka", None, None, age_days=0) == 0

    def test_recall_ranks_fresh_above_stale(self, store: MemoryStore):
        now = datetime.now(timezone.utc)
        old = Decision(
            project="demo", title="消息队列选型", decision="Kafka",
            rationale="高吞吐量，满足 50k msg/s",
            created_at=now - timedelta(days=120),
            updated_at=now - timedelta(days=120),
        )
        new = Decision(
            project="demo", title="消息队列选型", decision="Kafka",
            rationale="高吞吐量，满足 50k msg/s",
            created_at=now,
            updated_at=now,
        )
        store.save_decision(old)
        store.save_decision(new)
        out = _format_recall(store, "demo", "消息队列选型")
        # Locate each decision block by its created date string.
        old_date = (now - timedelta(days=120)).strftime("%Y-%m-%d")
        new_date = now.strftime("%Y-%m-%d")
        assert old_date != new_date
        assert out.index(new_date) < out.index(old_date)


# ── fitness feedback loop ──

class TestFeedbackLoop:
    def _store_with_trace(self, store: MemoryStore) -> str:
        d = Decision(project="demo", title="缓存方案", decision="Redis", rationale="低延迟")
        d.embedding = [1.0, 0.0, 0.0]
        d = store.save_decision(d)
        from scap.models import LatentTrace
        store.save_latent_trace(LatentTrace(
            entity_id=d.id, entity_type="decision", project="demo",
            embedding=[1.0, 0.0, 0.0],
        ))
        return d.id

    def test_feedback_updates_fitness_and_importance(self, store: MemoryStore):
        eid = self._store_with_trace(store)
        trace = store.update_fitness(eid, True)
        assert trace is not None
        assert trace.fitness > 0.5  # 0.5 → 0.6
        assert store.get_decision(eid).importance == 4  # nudged up
        store.update_fitness(eid, True)
        assert store.get_latent_trace(trace.id).fitness > 0.6
        store.update_fitness(eid, False)
        assert store.get_latent_trace(trace.id).fitness < 0.6

    def test_feedback_without_trace_returns_none(self, store: MemoryStore):
        d = store.save_decision(Decision(project="demo", title="无向量", decision="A"))
        assert store.update_fitness(d.id, True) is None

    def test_importance_bounded(self, store: MemoryStore):
        eid = self._store_with_trace(store)
        from scap.models import LatentTrace
        for _ in range(10):
            store.update_fitness(eid, True)
        assert store.get_decision(eid).importance == 5

    @pytest.mark.asyncio
    async def test_scap_feedback_tool(self, tmp_path, monkeypatch):
        from scap.mcp_server import _get_store
        db_path = str(tmp_path / "fb.db")
        store = MemoryStore(db_path)
        store.initialize()
        monkeypatch.setattr("scap.mcp_server._store", store)
        eid = self._store_with_trace(store)
        result = json.loads(await scap_feedback(eid, True, project="demo"))
        assert result["success"] is True
        assert result["fitness"] > 0.5
        # Wrong project rejected.
        bad = json.loads(await scap_feedback(eid, True, project="other"))
        assert bad["success"] is False
        # No-trace entity reports clearly.
        d2 = store.save_decision(Decision(project="demo", title="无向量", decision="A"))
        no_trace = json.loads(await scap_feedback(d2.id, True))
        assert no_trace["success"] is False
        assert "没有 latent trace" in no_trace["error"]


# ── export ordering ──

class TestExportImportanceOrder:
    def test_high_importance_decisions_first(self, store: MemoryStore, tmp_path):
        store.save_decision(Decision(
            project="p", title="低重要", decision="A", rationale="r", importance=1,
        ))
        store.save_decision(Decision(
            project="p", title="高重要", decision="B", rationale="r", importance=5,
        ))
        out = str(tmp_path / "p.md")
        store.export_context("p", out)
        content = open(out, encoding="utf-8").read()
        assert content.index("高重要") < content.index("低重要")
