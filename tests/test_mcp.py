"""Tests for MCP server tools."""
import json
import pytest

from scap.mcp_server import (
    scap_recall,
    scap_remember,
    scap_record_experience,
    scap_context,
    scap_status,
)


@pytest.fixture(autouse=True)
def _patch_store(tmp_path, monkeypatch):
    """Redirect MCP server to use a temp database."""
    from scap.store import MemoryStore
    import scap.mcp_server as mod

    db_path = str(tmp_path / "mcp_test.db")
    store = MemoryStore(db_path)
    store.initialize()
    monkeypatch.setattr(mod, "_store", store)


# ── scap_recall ──

class TestScapRecall:
    @pytest.mark.asyncio
    async def test_recall_empty_project(self):
        result = json.loads(await scap_recall("new-project", "build a dashboard"))
        assert result["success"] is True
        assert "暂无" in result["context"] or "第一" in result["context"]

    @pytest.mark.asyncio
    async def test_recall_with_decisions(self):
        await scap_remember("acme", "消息队列选型", "Kafka", "高吞吐量")
        result = json.loads(await scap_recall("acme", "消息队列"))
        assert result["success"] is True
        assert "Kafka" in result["context"]

    @pytest.mark.asyncio
    async def test_recall_returns_project_context(self):
        from scap.mcp_server import _get_store, _ensure_project
        from scap.models import ProjectContext
        store = _get_store()
        store.update_project_context(ProjectContext(
            project="acme", tech_stack=["PostgreSQL", "Redis"],
            conventions=["所有变更走事件溯源"],
        ))
        result = json.loads(await scap_recall("acme", "数据库设计"))
        assert "PostgreSQL" in result["context"]
        assert "事件溯源" in result["context"]


# ── scap_remember ──

class TestScapRemember:
    @pytest.mark.asyncio
    async def test_remember_basic(self):
        result = json.loads(await scap_remember(
            "acme", "消息队列选型", "Kafka", "吞吐量需求"
        ))
        assert result["success"] is True
        assert result["decision_id"].startswith("DC-")

    @pytest.mark.asyncio
    async def test_remember_minimal(self):
        result = json.loads(await scap_remember("acme", "简单决策", "选A"))
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_remember_auto_creates_project(self):
        result = json.loads(await scap_remember("brand-new", "test", "test"))
        assert result["success"] is True
        # Project should now exist
        from scap.mcp_server import _get_store
        ctx = _get_store().get_project_context("brand-new")
        assert ctx is not None

    @pytest.mark.asyncio
    async def test_remember_persists_to_db(self):
        await scap_remember("acme", "测试持久化", "选B", "理由B")
        from scap.mcp_server import _get_store
        store = _get_store()
        decisions = store.list_decisions(project="acme")
        assert any(d.title == "测试持久化" for d in decisions)


# ── scap_record_experience ──

class TestScapRecordExperience:
    @pytest.mark.asyncio
    async def test_record_experience_basic(self):
        result = json.loads(await scap_record_experience(
            "acme", "CPU 100%", "加了 ReadOnly", "JPA 必须加 @EntityGraph"
        ))
        assert result["success"] is True
        assert result["experience_id"].startswith("EX-")

    @pytest.mark.asyncio
    async def test_record_experience_with_tags(self):
        result = json.loads(await scap_record_experience(
            "acme", "问题", "行动", "教训", "性能,JPA"
        ))
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_experience_appears_in_recall(self):
        await scap_record_experience(
            "acme", "CPU 飙到 90%", "N+1 查询修复", "JPA 必须加 fetch join"
        )
        result = json.loads(await scap_recall("acme", "JPA 性能"))
        assert "fetch join" in result["context"] or "JPA" in result["context"]


# ── scap_context ──

class TestScapContext:
    @pytest.mark.asyncio
    async def test_context_empty(self):
        result = json.loads(await scap_context("empty-project"))
        assert result["success"] is True
        assert result["found"] is False

    @pytest.mark.asyncio
    async def test_context_with_data(self):
        await scap_remember("acme", "选型A", "决定A", "理由A")
        result = json.loads(await scap_context("acme"))
        assert result["success"] is True
        assert result["found"] is True
        assert len(result["decisions"]) >= 1


# ── scap_status ──

class TestScapStatus:
    @pytest.mark.asyncio
    async def test_status_empty(self):
        result = json.loads(await scap_status())
        assert result["success"] is True
        assert result["status"] == "running"

    @pytest.mark.asyncio
    async def test_status_with_data(self):
        await scap_remember("acme", "X", "Y")
        await scap_record_experience("acme", "S", "A", "L")
        result = json.loads(await scap_status())
        assert result["total_decisions"] >= 1
        assert result["total_experiences"] >= 1
        assert "acme" in result["projects"]


# ── Auto-export ──

class TestAutoExport:
    @pytest.mark.asyncio
    async def test_remember_triggers_export(self, tmp_path, monkeypatch):
        import scap.mcp_server as mod
        monkeypatch.setattr(mod, "_EXPORT_DIR", str(tmp_path / "exports"))
        await scap_remember("acme", "test export", "decision")
        export_file = tmp_path / "exports" / "acme.md"
        assert export_file.exists()
        content = export_file.read_text(encoding="utf-8")
        assert "test export" in content

    @pytest.mark.asyncio
    async def test_experience_triggers_export(self, tmp_path, monkeypatch):
        import scap.mcp_server as mod
        monkeypatch.setattr(mod, "_EXPORT_DIR", str(tmp_path / "exports"))
        await scap_record_experience("acme", "situation", "action", "lesson")
        export_file = tmp_path / "exports" / "acme.md"
        assert export_file.exists()
        content = export_file.read_text(encoding="utf-8")
        assert "lesson" in content
