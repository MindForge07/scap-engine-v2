"""P1 memory-lifecycle tests: four-operation writes, stale audit, reflection."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from scap.models import Decision, ProjectContext
from scap.store import MemoryStore
from scap.mcp_server import scap_remember, scap_audit, scap_reflect


@pytest.fixture(autouse=True)
def _patch_store(tmp_path, monkeypatch):
    """Redirect MCP server to a temp database."""
    db_path = str(tmp_path / "lifecycle.db")
    store = MemoryStore(db_path)
    store.initialize()
    monkeypatch.setattr("scap.mcp_server._store", store)
    return store


# ── Four-operation writes ──

class TestFourOperationWrites:
    @pytest.mark.asyncio
    async def test_add_new_decision(self):
        result = json.loads(await scap_remember("acme", "缓存方案", "Redis", "低延迟"))
        assert result["success"] is True
        assert result["action"] == "add"
        assert result["superseded"] is None

    @pytest.mark.asyncio
    async def test_noop_on_identical_decision(self):
        first = json.loads(await scap_remember("acme", "缓存方案", "Redis", "低延迟"))
        second = json.loads(await scap_remember("acme", "缓存方案", "Redis", "低延迟"))
        assert second["action"] == "noop"
        assert second["decision_id"] == first["decision_id"]
        assert "未重复记录" in second["message"]

    @pytest.mark.asyncio
    async def test_same_title_different_choice_supersedes(self):
        first = json.loads(await scap_remember("acme", "缓存方案", "Redis", "低延迟"))
        second = json.loads(await scap_remember("acme", "缓存方案", "Memcached", "内存占用低"))
        assert second["action"] == "update"
        assert second["superseded"] == first["decision_id"]
        # Old record is superseded and linked; new one is active.
        from scap.mcp_server import _get_store
        store = _get_store()
        old = store.get_decision(first["decision_id"])
        new = store.get_decision(second["decision_id"])
        assert old.status == "superseded"
        assert old.superseded_by == new.id
        assert new.status == "active"
        assert new.decision == "Memcached"

    @pytest.mark.asyncio
    async def test_title_whitespace_normalized(self):
        await scap_remember("acme", "缓存方案", "Redis", "低延迟")
        result = json.loads(await scap_remember("acme", "  缓存方案  ", "Memcached", "理由"))
        assert result["action"] == "update"

    @pytest.mark.asyncio
    async def test_noop_does_not_inflate_count(self):
        await scap_remember("acme", "缓存方案", "Redis", "低延迟")
        await scap_remember("acme", "缓存方案", "Redis", "低延迟")
        from scap.mcp_server import _get_store
        stats = _get_store().get_stats(project="acme")
        assert stats["decision_count"] == 1

    @pytest.mark.asyncio
    async def test_superseded_excluded_from_recall(self):
        from scap.mcp_server import _get_store, _format_recall
        await scap_remember("acme", "缓存方案", "Redis", "低延迟")
        await scap_remember("acme", "缓存方案", "Memcached", "内存占用低")
        out = _format_recall(_get_store(), "acme", "缓存方案")
        assert "Memcached" in out
        # The superseded Redis choice must not be injected as an active decision.
        assert "Redis" not in out


# ── scap_audit ──

class TestAudit:
    @pytest.mark.asyncio
    async def test_audit_lists_stale_decisions(self):
        from scap.mcp_server import _get_store
        store = _get_store()
        now = datetime.now(timezone.utc)
        stale = Decision(
            project="acme", title="旧决策", decision="A", rationale="r",
            created_at=now - timedelta(days=200),
            updated_at=now - timedelta(days=200),
        )
        fresh = Decision(project="acme", title="新决策", decision="B", rationale="r")
        store.save_decision(stale)
        store.save_decision(fresh)
        result = json.loads(await scap_audit("acme", older_than_days=90))
        assert result["success"] is True
        assert result["stale_count"] == 1
        assert result["stale"][0]["title"] == "旧决策"
        assert result["stale"][0]["days_since_update"] >= 199

    @pytest.mark.asyncio
    async def test_audit_respects_threshold(self):
        from scap.mcp_server import _get_store
        store = _get_store()
        now = datetime.now(timezone.utc)
        old = Decision(
            project="acme", title="60天前", decision="A", rationale="r",
            created_at=now - timedelta(days=60),
            updated_at=now - timedelta(days=60),
        )
        store.save_decision(old)
        strict = json.loads(await scap_audit("acme", older_than_days=30))
        loose = json.loads(await scap_audit("acme", older_than_days=90))
        assert strict["stale_count"] == 1
        assert loose["stale_count"] == 0

    @pytest.mark.asyncio
    async def test_audit_empty_project(self):
        result = json.loads(await scap_audit("empty"))
        assert result["success"] is True
        assert result["stale_count"] == 0
        assert result["total_active"] == 0

    @pytest.mark.asyncio
    async def test_audit_sorts_by_importance(self):
        from scap.mcp_server import _get_store
        store = _get_store()
        now = datetime.now(timezone.utc)
        for title, imp in [("低", 1), ("高", 5)]:
            store.save_decision(Decision(
                project="acme", title=title, decision="A", rationale="r",
                importance=imp,
                created_at=now - timedelta(days=120),
                updated_at=now - timedelta(days=120),
            ))
        result = json.loads(await scap_audit("acme"))
        assert result["stale"][0]["title"] == "高"


# ── scap_reflect ──

class TestReflect:
    @pytest.mark.asyncio
    async def test_reflect_adds_insights_and_exports(self, tmp_path, monkeypatch):
        import scap.mcp_server as mod
        monkeypatch.setattr(mod, "_EXPORT_DIR", str(tmp_path / "exports"))
        result = json.loads(await scap_reflect(
            "acme", ["事件溯源是我们状态变更的默认模式", "金额一律 NUMERIC 存储"],
        ))
        assert result["success"] is True
        assert result["added"] == 2
        from scap.mcp_server import _get_store
        ctx = _get_store().get_project_context("acme")
        assert len(ctx.insights) == 2
        exported = tmp_path / "exports" / "acme.md"
        assert exported.exists()
        content = exported.read_text(encoding="utf-8")
        assert "## Insights" in content
        assert "事件溯源是我们状态变更的默认模式" in content

    @pytest.mark.asyncio
    async def test_reflect_dedup(self):
        await scap_reflect("acme", ["洞察一"])
        result = json.loads(await scap_reflect("acme", ["洞察一", "洞察二"]))
        assert result["added"] == 1
        assert result["total_insights"] == 2

    @pytest.mark.asyncio
    async def test_reflect_caps_per_call_and_total(self):
        many = [f"洞察{i}" for i in range(10)]
        result = json.loads(await scap_reflect("acme", many))
        assert result["added"] == 5  # capped at 5 per call
        from scap.mcp_server import _get_store
        ctx = _get_store().get_project_context("acme")
        assert len(ctx.insights) == 5
        # Total capped at 20.
        for i in range(4):
            await scap_reflect("acme", [f"批次{i}-{j}" for j in range(5)])
        ctx = _get_store().get_project_context("acme")
        assert len(ctx.insights) == 20

    @pytest.mark.asyncio
    async def test_reflect_rejects_empty(self):
        result = json.loads(await scap_reflect("acme", []))
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_reflect_preserves_existing_context(self):
        from scap.mcp_server import _get_store
        store = _get_store()
        store.update_project_context(ProjectContext(
            project="acme", tech_stack=["PostgreSQL"], conventions=["事件溯源"],
        ))
        await scap_reflect("acme", ["新洞察"])
        ctx = store.get_project_context("acme")
        assert ctx.tech_stack == ["PostgreSQL"]
        assert ctx.conventions == ["事件溯源"]
        assert ctx.insights == ["新洞察"]
