"""Tests for MemoryStore — SQLite + FTS5."""
import json
from datetime import datetime, timezone

import pytest

from scap.models import Decision, ProjectContext, Experience
from scap.store import MemoryStore


# ── Decision CRUD ──

class TestDecisionCRUD:
    def test_save_and_get(self, store: MemoryStore):
        d = Decision(project="acme", title="支付网关选型", decision="Stripe + 自研")
        d = store.save_decision(d)
        assert d.id.startswith("DC-")
        loaded = store.get_decision(d.id)
        assert loaded is not None
        assert loaded.title == "支付网关选型"
        assert loaded.decision == "Stripe + 自研"

    def test_list_by_project(self, store: MemoryStore):
        store.save_decision(Decision(project="acme", title="A"))
        store.save_decision(Decision(project="other", title="B"))
        store.save_decision(Decision(project="acme", title="C"))
        result = store.list_decisions(project="acme")
        assert len(result) == 2

    def test_list_by_status(self, store: MemoryStore):
        store.save_decision(Decision(project="x", title="A"))
        store.save_decision(Decision(project="x", title="B", status="deprecated"))
        result = store.list_decisions(status="active")
        assert len(result) == 1

    def test_supersede(self, store: MemoryStore):
        old = store.save_decision(Decision(project="acme", title="Old"))
        new = Decision(project="acme", title="New")
        new = store.supersede(old.id, new)

        old_loaded = store.get_decision(old.id)
        assert old_loaded.status == "superseded"
        assert old_loaded.superseded_by == new.id

    def test_get_nonexistent(self, store: MemoryStore):
        assert store.get_decision("DC-99999999-9999") is None

    def test_auto_increment_id(self, store: MemoryStore):
        d1 = store.save_decision(Decision(project="x", title="A"))
        d2 = store.save_decision(Decision(project="x", title="B"))
        # Same date, different sequence
        assert d1.id != d2.id
        assert d2.id > d1.id


# ── ProjectContext CRUD ──

class TestProjectContextCRUD:
    def test_save_and_get(self, store: MemoryStore):
        ctx = ProjectContext(
            project="acme",
            tech_stack=["PostgreSQL", "Redis"],
            conventions=["状态变更走事件溯源"],
            active_goals=["年底测试覆盖率80%"],
        )
        store.update_project_context(ctx)
        loaded = store.get_project_context("acme")
        assert loaded is not None
        assert "PostgreSQL" in loaded.tech_stack
        assert len(loaded.conventions) == 1

    def test_upsert(self, store: MemoryStore):
        ctx1 = ProjectContext(project="acme", tech_stack=["v1"])
        store.update_project_context(ctx1)
        ctx2 = ProjectContext(project="acme", tech_stack=["v1", "v2"])
        store.update_project_context(ctx2)
        loaded = store.get_project_context("acme")
        assert len(loaded.tech_stack) == 2

    def test_get_nonexistent(self, store: MemoryStore):
        assert store.get_project_context("nope") is None


# ── Experience CRUD ──

class TestExperienceCRUD:
    def test_save(self, store: MemoryStore):
        e = Experience(
            project="acme",
            situation="CPU 100%",
            action="加了ReadOnly",
            lesson="JPA查询必须加@EntityGraph",
        )
        e = store.save_experience(e)
        assert e.id.startswith("EX-")


# ── Search ──

class TestSearch:
    def test_search_decisions(self, store: MemoryStore):
        store.save_decision(Decision(
            project="acme", title="支付网关选型",
            decision="Stripe + 自研 fallback 网关",
        ))
        store.save_decision(Decision(
            project="acme", title="消息队列选型",
            decision="Kafka over RabbitMQ",
        ))
        results = store.search("acme", "支付")
        assert len(results) >= 1
        assert any("支付" in r.get("title", "") for r in results)

    def test_search_project_scoped(self, store: MemoryStore):
        store.save_decision(Decision(project="acme", title="支付网关选型"))
        store.save_decision(Decision(project="other", title="另一个项目"))
        results = store.search("acme", "网关")
        # Should only return acme results
        for r in results:
            assert r.get("entity_id", "").startswith("DC-")

    def test_search_empty(self, store: MemoryStore):
        results = store.search("acme", "完全不存在的查询xyz")
        assert len(results) == 0


# ── Stats ──

class TestStats:
    def test_empty_stats(self, store: MemoryStore):
        stats = store.get_stats()
        assert stats["decision_count"] == 0
        assert stats["experience_count"] == 0

    def test_stats_with_data(self, store: MemoryStore):
        store.save_decision(Decision(project="acme", title="A"))
        store.save_decision(Decision(project="acme", title="B"))
        store.save_experience(Experience(project="acme", situation="X"))
        stats = store.get_stats("acme")
        assert stats["decision_count"] == 2
        assert stats["experience_count"] == 1
        assert "acme" in stats["projects"]
