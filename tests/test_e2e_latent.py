"""End-to-end tests for the latent space evolution pipeline.

Exercises the complete lifecycle across all layers (store → MCP → CLI):
  1. Record decisions/experiences → auto-embed → LatentTrace creation
  2. Semantic search → vector similarity retrieval
  3. Consolidation → merge similar traces → advance evolution gen
  4. Evolved context → fitness-weighted ranking
  5. CLI + MCP interop → same database, consistent results
  6. Graceful degradation → no embedder, FTS5 fallback still works
  7. Backfill → embed existing records retroactively
"""
import json
import math
import re

import pytest
from click.testing import CliRunner

from scap.mcp_server import (
    scap_recall,
    scap_remember,
    scap_record_experience,
    scap_retrieve_latent,
    scap_consolidate,
    scap_evolved_context,
    scap_status,
)
from scap.cli import cli
from scap.models import Decision, Experience, LatentTrace
from scap.store import MemoryStore


# ── Mock Embedder (shared deterministic implementation) ──

class MockEmbedder:
    DIMENSION = 384

    def __init__(self, available: bool = True) -> None:
        self._available = available

    @property
    def is_available(self) -> bool:
        return self._available

    @staticmethod
    def _word_hash(word: str) -> int:
        h = 0
        for c in word:
            h = (h * 31 + ord(c)) % (2**31)
        return h

    @staticmethod
    def _tokenize(text: str) -> list:
        tokens = []
        for part in text.lower().split():
            cjk = re.findall(r'[一-鿿]', part)
            non_cjk = re.sub(r'[一-鿿]', '', part).strip()
            if cjk:
                tokens.extend(cjk)
            if non_cjk:
                tokens.append(non_cjk)
        return tokens

    def embed(self, text: str):
        if not self._available:
            return None
        if not text or not text.strip():
            return None
        vec = [0.0] * self.DIMENSION
        for word in self._tokenize(text):
            idx = self._word_hash(word) % self.DIMENSION
            vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


# ── Fixtures ──

@pytest.fixture(autouse=True)
def _patch_mcp_store_and_embedder(tmp_path, monkeypatch):
    """Point MCP server at a temp DB + reset embedder for each test."""
    import scap.mcp_server as mcp_mod
    import scap.cli as cli_mod

    db_path = str(tmp_path / "e2e_test.db")
    store = MemoryStore(db_path)
    store.initialize()

    monkeypatch.setattr(mcp_mod, "_store", store)
    monkeypatch.setattr(mcp_mod, "_embedder", MockEmbedder(available=False))
    # Store db_path for CLI interop tests
    monkeypatch.setattr(cli_mod, "_get_embedder", lambda: MockEmbedder(available=False))
    pytest._e2e_db_path = db_path


@pytest.fixture
def mock_embedder(monkeypatch):
    """Enable mock embeddings for MCP + CLI."""
    import scap.mcp_server as mcp_mod
    import scap.cli as cli_mod
    embedder = MockEmbedder(available=True)
    monkeypatch.setattr(mcp_mod, "_embedder", embedder)
    monkeypatch.setattr(cli_mod, "_get_embedder", lambda: embedder)
    return embedder


def _get_mcp_store():
    """Get the current MCP store instance."""
    import scap.mcp_server as mod
    return mod._store


# ═══════════════════════════════════════════════════════
# Scenario 1: Full lifecycle — 电商平台项目
# ═══════════════════════════════════════════════════════

class TestFullLifecycle:
    """Complete pipeline: create → embed → search → consolidate → evolve.

    Simulates an AI agent working on an e-commerce platform project,
    using the memory system throughout its lifecycle.
    """

    @pytest.mark.asyncio
    async def test_step1_record_and_embed(self, mock_embedder):
        """Step 1: Record decisions + experiences, verify auto-embedding."""
        # Record 4 decisions (2 pairs are semantically similar)
        await scap_remember("ecom", "数据库选型", "PostgreSQL", "成熟稳定 支持JSON")
        await scap_remember("ecom", "缓存方案", "Redis集群", "高可用 低延迟")
        await scap_remember("ecom", "数据库性能优化", "添加索引和分区", "查询慢")
        await scap_remember("ecom", "数据库性能调优", "索引优化", "慢查询问题")

        # Record 2 experiences
        await scap_record_experience("ecom", "高并发连接池满", "增大连接池", "监控连接数")
        await scap_record_experience("ecom", "大促时数据库CPU飙升", "读写分离", "提前扩容")

        # Verify: all 6 records have embeddings + latent traces
        store = _get_mcp_store()
        decisions = store.list_decisions(project="ecom")
        experiences = store.list_experiences(project="ecom")
        traces = store.list_latent_traces(project="ecom")

        assert len(decisions) == 4
        assert len(experiences) == 2
        assert len(traces) == 6
        for d in decisions:
            assert d.embedding is not None
        for e in experiences:
            assert e.embedding is not None

    @pytest.mark.asyncio
    async def test_step2_semantic_search(self, mock_embedder):
        """Step 2: Semantic search finds related content beyond keywords."""
        # Setup: record data first
        await scap_remember("ecom", "数据库选型", "PostgreSQL", "成熟稳定")
        await scap_remember("ecom", "缓存方案", "Redis集群", "高可用")
        await scap_remember("ecom", "数据库性能优化", "添加索引", "查询慢")
        await scap_record_experience("ecom", "数据库CPU飙升", "读写分离", "提前扩容")

        # Search for "数据库" — should find DB-related decisions + experience
        result = json.loads(await scap_retrieve_latent("ecom", "数据库", limit=10))
        assert result["success"] is True
        assert result["count"] >= 3  # at least 3 DB-related items

        # All results should be DB-related (not cache)
        for hit in result["results"]:
            title = hit.get("title", "") + hit.get("decision", "") + hit.get("snippet", "")
            assert "数据库" in title or "PostgreSQL" in title or "索引" in title

    @pytest.mark.asyncio
    async def test_step3_consolidate_similar(self, mock_embedder):
        """Step 3: Consolidation merges similar traces, advances evolution gen."""
        # Create two very similar decisions
        await scap_remember("ecom", "数据库性能优化", "添加索引和分区", "查询慢")
        await scap_remember("ecom", "数据库性能优化方案", "添加索引和分区", "查询慢")

        store = _get_mcp_store()
        traces_before = store.list_latent_traces(project="ecom")
        assert len(traces_before) == 2

        # Consolidate with low threshold to ensure merge
        result = json.loads(await scap_consolidate("ecom", similarity_threshold=0.5))
        assert result["success"] is True
        assert result["merged"] >= 1
        assert result["surviving"] < result["total_traces"]
        assert result["new_evolution_gen"] >= 1

        # Verify: one trace deleted, survivor has evolution_gen > 0
        traces_after = store.list_latent_traces(project="ecom")
        assert len(traces_after) == 1
        assert traces_after[0].evolution_gen >= 1

        # Original Decision records preserved
        decisions = store.list_decisions(project="ecom")
        assert len(decisions) == 2

    @pytest.mark.asyncio
    async def test_step4_evolved_context_after_consolidation(self, mock_embedder):
        """Step 4: Evolved context shows consolidated traces with higher gen."""
        # Create + consolidate similar decisions
        await scap_remember("ecom", "数据库性能优化", "添加索引", "查询慢")
        await scap_remember("ecom", "数据库性能优化方案", "添加索引", "查询慢")
        await scap_consolidate("ecom", similarity_threshold=0.5)

        # Add a different decision (no consolidation)
        await scap_remember("ecom", "前端框架选择", "React", "组件丰富")

        result = json.loads(await scap_evolved_context("ecom"))
        assert result["success"] is True
        assert result["returned"] >= 2

        # The consolidated trace should have evolution_gen >= 1
        gens = [r["evolution_gen"] for r in result["results"]]
        assert max(gens) >= 1

    @pytest.mark.asyncio
    async def test_step5_recall_with_vector_search(self, mock_embedder):
        """Step 5: scap_recall uses four-tier search with vector fallback."""
        await scap_remember("ecom", "数据库性能优化", "添加索引", "查询响应慢")
        await scap_record_experience("ecom", "数据库CPU飙升", "读写分离", "提前扩容")

        # Recall with a semantically related query
        result = json.loads(await scap_recall("ecom", "数据库性能问题"))
        assert result["success"] is True
        assert "数据库" in result["context"]

    @pytest.mark.asyncio
    async def test_step6_status_shows_evolution(self, mock_embedder):
        """Step 6: Status reflects latent trace count and evolution gen."""
        await scap_remember("ecom", "测试决策", "选项A", "理由")
        await scap_consolidate("ecom", similarity_threshold=0.5)

        result = json.loads(await scap_status())
        assert result["success"] is True
        assert result["latent_trace_count"] >= 1
        assert result["evolution_gen"] >= 0


# ═══════════════════════════════════════════════════════
# Scenario 2: Cross-project isolation
# ═══════════════════════════════════════════════════════

class TestCrossProjectIsolation:
    """Verify latent traces are project-scoped — no leakage."""

    @pytest.mark.asyncio
    async def test_search_isolated(self, mock_embedder):
        await scap_remember("project-a", "数据库选型", "PostgreSQL", "成熟")
        await scap_remember("project-b", "数据库选型", "MySQL", "普及")

        result_a = json.loads(await scap_retrieve_latent("project-a", "数据库"))
        result_b = json.loads(await scap_retrieve_latent("project-b", "数据库"))

        assert result_a["count"] >= 1
        assert result_b["count"] >= 1
        a_ids = {r["entity_id"] for r in result_a["results"]}
        b_ids = {r["entity_id"] for r in result_b["results"]}
        assert a_ids & b_ids == set()  # no overlap

    @pytest.mark.asyncio
    async def test_consolidate_isolated(self, mock_embedder):
        """Consolidating project A must not affect project B's traces."""
        await scap_remember("project-a", "数据库性能优化", "索引", "查询快")
        await scap_remember("project-a", "数据库性能优化方案", "索引", "查询快")
        await scap_remember("project-b", "数据库性能优化", "索引", "查询快")

        # Consolidate project-a only
        result = json.loads(await scap_consolidate("project-a", similarity_threshold=0.5))
        assert result["merged"] >= 1

        # Project-b should still have 1 trace untouched
        store = _get_mcp_store()
        b_traces = store.list_latent_traces(project="project-b")
        assert len(b_traces) == 1
        assert b_traces[0].evolution_gen == 0  # not affected by project-a consolidation


# ═══════════════════════════════════════════════════════
# Scenario 3: Graceful degradation (no embedder)
# ═══════════════════════════════════════════════════════

class TestGracefulDegradationE2E:
    """Full pipeline without embedder — everything still works via FTS5."""

    @pytest.mark.asyncio
    async def test_record_without_embedder(self):
        """Recording works without embeddings; traces are not created."""
        result = json.loads(await scap_remember("acme", "选型", "PostgreSQL", "成熟"))
        assert result["success"] is True
        assert result["embedded"] is False

        store = _get_mcp_store()
        traces = store.list_latent_traces(project="acme")
        assert len(traces) == 0

    @pytest.mark.asyncio
    async def test_recall_fts5_fallback(self):
        """scap_recall still finds results via FTS5 without embeddings."""
        await scap_remember("acme", "消息队列选型", "Kafka", "高吞吐量")
        result = json.loads(await scap_recall("acme", "消息队列"))
        assert result["success"] is True
        assert "Kafka" in result["context"]

    @pytest.mark.asyncio
    async def test_latent_search_unavailable(self):
        result = json.loads(await scap_retrieve_latent("acme", "anything"))
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_consolidate_no_traces(self):
        result = json.loads(await scap_consolidate("acme"))
        assert result["success"] is True
        assert result["merged"] == 0

    @pytest.mark.asyncio
    async def test_evolved_context_empty(self):
        result = json.loads(await scap_evolved_context("acme"))
        assert result["success"] is True
        assert result["results"] == []


# ═══════════════════════════════════════════════════════
# Scenario 4: Backfill — retroactive embedding
# ═══════════════════════════════════════════════════════

class TestBackfillE2E:
    """Backfill embeddings for records created without an embedder."""

    @pytest.mark.asyncio
    async def test_backfill_then_search(self, mock_embedder):
        """Phase 1: record without embedder → Phase 2: backfill → Phase 3: search works."""
        # Phase 1: Create records without embeddings
        # (mock_embedder fixture hasn't been applied yet in this test...
        #  actually it has — we need to create without embedder first)

        # To simulate "created without embedder", we manually create decisions
        store = _get_mcp_store()
        d1 = Decision(project="acme", title="数据库选型", decision="PostgreSQL", rationale="成熟")
        d2 = Decision(project="acme", title="缓存方案", decision="Redis", rationale="快速")
        store.save_decision(d1)
        store.save_decision(d2)

        # Verify: no embeddings, no traces
        assert store.list_latent_traces(project="acme") == []

        # Phase 2: Backfill via CLI embed command
        runner = CliRunner()
        result = runner.invoke(cli, ["--db", pytest._e2e_db_path, "embed", "-p", "acme"])
        assert result.exit_code == 0
        assert "2/2" in result.output

        # Phase 3: Semantic search now works
        traces = store.list_latent_traces(project="acme")
        assert len(traces) == 2

        search_result = json.loads(await scap_retrieve_latent("acme", "数据库"))
        assert search_result["success"] is True
        assert search_result["count"] >= 1


# ═══════════════════════════════════════════════════════
# Scenario 5: CLI + MCP interop
# ═══════════════════════════════════════════════════════

class TestCLIMCPInterop:
    """Verify CLI commands read data created by MCP tools (same DB)."""

    @pytest.mark.asyncio
    async def test_cli_status_reads_mcp_data(self, mock_embedder):
        """CLI status shows correct counts after MCP records."""
        await scap_remember("ecom", "数据库选型", "PostgreSQL", "成熟")
        await scap_remember("ecom", "缓存方案", "Redis", "快速")
        await scap_record_experience("ecom", "CPU飙升", "扩容", "监控")

        runner = CliRunner()
        result = runner.invoke(cli, ["--db", pytest._e2e_db_path, "status"])
        assert result.exit_code == 0
        assert "Latent Traces" in result.output
        assert "3" in result.output  # 3 traces total

    @pytest.mark.asyncio
    async def test_cli_traces_reads_mcp_data(self, mock_embedder):
        """CLI traces lists traces created by MCP."""
        await scap_remember("ecom", "数据库选型", "PostgreSQL", "成熟")

        runner = CliRunner()
        result = runner.invoke(cli, ["--db", pytest._e2e_db_path, "traces", "-p", "ecom"])
        assert result.exit_code == 0
        assert "Latent Traces" in result.output
        assert "ecom" in result.output

    @pytest.mark.asyncio
    async def test_cli_latent_reads_mcp_data(self, mock_embedder):
        """CLI latent search finds MCP-created records."""
        await scap_remember("ecom", "数据库性能优化", "添加索引", "查询快")

        runner = CliRunner()
        result = runner.invoke(cli, ["--db", pytest._e2e_db_path,
                                      "latent", "数据库", "-p", "ecom"])
        assert result.exit_code == 0
        assert "semantic matches" in result.output

    @pytest.mark.asyncio
    async def test_cli_evolved_reads_mcp_data(self, mock_embedder):
        """CLI evolved shows traces created by MCP."""
        await scap_remember("ecom", "数据库选型", "PostgreSQL", "成熟")

        runner = CliRunner()
        result = runner.invoke(cli, ["--db", pytest._e2e_db_path,
                                      "evolved", "-p", "ecom"])
        assert result.exit_code == 0
        assert "Evolved Context" in result.output

    @pytest.mark.asyncio
    async def test_cli_consolidate_after_mcp_create(self, mock_embedder):
        """CLI consolidate merges traces created by MCP."""
        await scap_remember("ecom", "数据库性能优化", "索引", "查询快")
        await scap_remember("ecom", "数据库性能优化方案", "索引", "查询快")

        runner = CliRunner()
        result = runner.invoke(cli, ["--db", pytest._e2e_db_path,
                                      "consolidate", "-p", "ecom", "-t", "0.5"])
        assert result.exit_code == 0
        assert "Merged" in result.output

        # Verify via MCP that consolidation took effect
        store = _get_mcp_store()
        traces = store.list_latent_traces(project="ecom")
        assert len(traces) == 1  # merged down to 1


# ═══════════════════════════════════════════════════════
# Scenario 6: Consolidation idempotency
# ═══════════════════════════════════════════════════════

class TestConsolidationIdempotency:
    """Running consolidate twice should not merge already-unique traces."""

    @pytest.mark.asyncio
    async def test_double_consolidate(self, mock_embedder):
        await scap_remember("ecom", "数据库性能优化", "索引", "查询快")
        await scap_remember("ecom", "数据库性能优化方案", "索引", "查询快")
        await scap_remember("ecom", "前端框架选择", "React", "组件丰富")

        # First consolidation: merges the 2 similar DB traces
        r1 = json.loads(await scap_consolidate("ecom", similarity_threshold=0.5))
        assert r1["merged"] >= 1

        # Second consolidation: nothing left to merge
        r2 = json.loads(await scap_consolidate("ecom", similarity_threshold=0.5))
        assert r2["merged"] == 0
        assert r2["surviving"] == r1["surviving"]  # unchanged


# ═══════════════════════════════════════════════════════
# Scenario 7: Fitness-weighted ranking
# ═══════════════════════════════════════════════════════

class TestFitnessRanking:
    """Verify evolved_context ranks by fitness × evolution_gen."""

    @pytest.mark.asyncio
    async def test_higher_fitness_ranks_first(self, mock_embedder):
        """A higher-fitness trace should rank above a lower-fitness one."""
        await scap_remember("ecom", "低质量决策", "选项A", "随便")
        await scap_remember("ecom", "高质量决策", "选项B", "深思熟虑")

        # Manually set fitness scores
        store = _get_mcp_store()
        traces = store.list_latent_traces(project="ecom")
        assert len(traces) == 2

        for t in traces:
            d = store.get_decision(t.entity_id)
            if d and "高质量" in d.title:
                t.fitness = 0.9
            else:
                t.fitness = 0.1
            store.save_latent_trace(t)

        result = json.loads(await scap_evolved_context("ecom"))
        assert result["returned"] == 2

        # First result should be the high-fitness one
        first = result["results"][0]
        assert first["fitness"] == 0.9
