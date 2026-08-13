"""Tests for MCP latent space evolution tools (Phase 2).

Covers:
  1. Graceful degradation when sentence-transformers is not installed
  2. Embedding + LatentTrace creation with mocked embedder
  3. scap_retrieve_latent semantic search
  4. scap_consolidate trace merging + evolution generation advance
  5. scap_evolved_context fitness-weighted ranking
  6. Backward compatibility — existing tools work with/without embeddings
"""
import json
import math
import pytest

from scap.mcp_server import (
    scap_recall,
    scap_remember,
    scap_record_experience,
    scap_retrieve_latent,
    scap_consolidate,
    scap_evolved_context,
    scap_status,
)


# ── Mock Embedder ──

class MockEmbedder:
    """Deterministic mock embedder for testing.

    Uses simple bag-of-words hashing into a 384-dim vector.
    Similar texts produce similar vectors, enabling predictable
    cosine similarity without sentence-transformers.
    """

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
        """Tokenize: Latin words by whitespace, CJK by individual character."""
        import re
        tokens = []
        for part in text.lower().split():
            cjk_chars = re.findall(r'[一-鿿]', part)
            non_cjk = re.sub(r'[一-鿿]', '', part).strip()
            if cjk_chars:
                tokens.extend(cjk_chars)
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

    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


# ── Fixtures ──

@pytest.fixture(autouse=True)
def _patch_store_and_embedder(tmp_path, monkeypatch):
    """Reset store + embedder for each test.

    By default embedder is unavailable (simulating no sentence-transformers).
    Use the ``mock_embedder`` fixture to enable embeddings.
    """
    from scap.store import MemoryStore
    import scap.mcp_server as mod

    db_path = str(tmp_path / "mcp_latent_test.db")
    store = MemoryStore(db_path)
    store.initialize()
    monkeypatch.setattr(mod, "_store", store)
    monkeypatch.setattr(mod, "_embedder", MockEmbedder(available=False))


@pytest.fixture
def mock_embedder(monkeypatch):
    """Enable mock embeddings with deterministic bag-of-words vectors."""
    import scap.mcp_server as mod
    embedder = MockEmbedder(available=True)
    monkeypatch.setattr(mod, "_embedder", embedder)
    return embedder


# ═══════════════════════════════════════════════════════
# Group 1: Graceful degradation (no embedder)
# ═══════════════════════════════════════════════════════

class TestGracefulDegradation:
    """All tools must work when sentence-transformers is not installed."""

    @pytest.mark.asyncio
    async def test_remember_without_embedder(self):
        result = json.loads(await scap_remember("acme", "选型", "PostgreSQL", "成熟稳定"))
        assert result["success"] is True
        assert result["embedded"] is False

    @pytest.mark.asyncio
    async def test_record_experience_without_embedder(self):
        result = json.loads(
            await scap_record_experience("acme", "CPU飙升", "加索引", "注意慢查询")
        )
        assert result["success"] is True
        assert result["embedded"] is False

    @pytest.mark.asyncio
    async def test_recall_without_embedder(self):
        await scap_remember("acme", "消息队列", "Kafka", "高吞吐")
        result = json.loads(await scap_recall("acme", "消息队列"))
        assert result["success"] is True
        assert "Kafka" in result["context"]

    @pytest.mark.asyncio
    async def test_retrieve_latent_without_embedder(self):
        result = json.loads(await scap_retrieve_latent("acme", "database"))
        assert result["success"] is False
        assert "not available" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_consolidate_without_traces(self):
        result = json.loads(await scap_consolidate("acme"))
        assert result["success"] is True
        assert result["merged"] == 0

    @pytest.mark.asyncio
    async def test_evolved_context_without_traces(self):
        result = json.loads(await scap_evolved_context("acme"))
        assert result["success"] is True
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_status_includes_latent_stats(self):
        result = json.loads(await scap_status())
        assert result["success"] is True
        assert "latent_trace_count" in result
        assert "evolution_gen" in result


# ═══════════════════════════════════════════════════════
# Group 2: Embedding + LatentTrace creation
# ═══════════════════════════════════════════════════════

class TestEmbeddingCreation:

    @pytest.mark.asyncio
    async def test_remember_creates_trace(self, mock_embedder):
        from scap.mcp_server import _get_store
        result = json.loads(await scap_remember("acme", "数据库选型", "PostgreSQL", "成熟稳定"))
        assert result["success"] is True
        assert result["embedded"] is True

        store = _get_store()
        traces = store.list_latent_traces(project="acme")
        assert len(traces) == 1
        assert traces[0].entity_type == "decision"
        assert traces[0].embedding is not None
        assert len(traces[0].embedding) == MockEmbedder.DIMENSION

    @pytest.mark.asyncio
    async def test_record_experience_creates_trace(self, mock_embedder):
        from scap.mcp_server import _get_store
        result = json.loads(
            await scap_record_experience("acme", "CPU飙升90%", "加了索引", "慢查询要监控")
        )
        assert result["success"] is True
        assert result["embedded"] is True

        store = _get_store()
        traces = store.list_latent_traces(project="acme")
        assert len(traces) == 1
        assert traces[0].entity_type == "experience"

    @pytest.mark.asyncio
    async def test_decision_stores_embedding(self, mock_embedder):
        from scap.mcp_server import _get_store
        await scap_remember("acme", "缓存策略", "Redis", "低延迟")
        store = _get_store()
        decisions = store.list_decisions(project="acme")
        assert len(decisions) == 1
        assert decisions[0].embedding is not None

    @pytest.mark.asyncio
    async def test_experience_stores_embedding(self, mock_embedder):
        from scap.mcp_server import _get_store
        await scap_record_experience("acme", "内存泄漏", "重启服务", "定期检查")
        store = _get_store()
        experiences = store.list_experiences(project="acme")
        assert len(experiences) == 1
        assert experiences[0].embedding is not None


# ═══════════════════════════════════════════════════════
# Group 3: scap_retrieve_latent
# ═══════════════════════════════════════════════════════

class TestRetrieveLatent:

    @pytest.mark.asyncio
    async def test_retrieve_finds_similar(self, mock_embedder):
        await scap_remember("acme", "数据库性能优化", "加索引", "查询变快")
        await scap_remember("acme", "数据库查询调优", "使用EXPLAIN", "分析执行计划")

        result = json.loads(await scap_retrieve_latent("acme", "数据库性能"))
        assert result["success"] is True
        assert result["count"] >= 1

    @pytest.mark.asyncio
    async def test_retrieve_empty_project(self, mock_embedder):
        result = json.loads(await scap_retrieve_latent("empty-project", "anything"))
        assert result["success"] is True
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_retrieve_respects_limit(self, mock_embedder):
        for i in range(5):
            await scap_remember("acme", f"决策{i}", f"选项{i}", "理由")
        result = json.loads(await scap_retrieve_latent("acme", "决策", limit=2))
        assert result["success"] is True
        assert result["count"] <= 2

    @pytest.mark.asyncio
    async def test_retrieve_returns_similarity(self, mock_embedder):
        await scap_remember("acme", "数据库选型", "PostgreSQL", "成熟")
        result = json.loads(await scap_retrieve_latent("acme", "数据库选型"))
        assert result["count"] >= 1
        assert "similarity" in result["results"][0]

    @pytest.mark.asyncio
    async def test_retrieve_includes_experiences(self, mock_embedder):
        await scap_record_experience("acme", "数据库连接池满", "增大池", "监控连接数")
        result = json.loads(await scap_retrieve_latent("acme", "数据库连接"))
        assert result["count"] >= 1
        types = [r.get("entity_type") for r in result["results"]]
        assert "experience" in types


# ═══════════════════════════════════════════════════════
# Group 4: scap_consolidate
# ═══════════════════════════════════════════════════════

class TestConsolidate:

    @pytest.mark.asyncio
    async def test_consolidate_merges_similar(self, mock_embedder):
        # Two decisions with very similar text → high cosine similarity
        await scap_remember("acme", "数据库性能优化", "加索引", "提升查询速度")
        await scap_remember("acme", "数据库性能优化方案", "加索引", "提升查询速度")

        result = json.loads(await scap_consolidate("acme", similarity_threshold=0.5))
        assert result["success"] is True
        assert result["merged"] >= 1
        assert result["surviving"] < result["total_traces"]

    @pytest.mark.asyncio
    async def test_consolidate_keeps_different(self, mock_embedder):
        # Completely different text → near-zero similarity
        await scap_remember("acme", "数据库选型", "PostgreSQL", "成熟稳定")
        await scap_remember("acme", "前端框架选择", "React", "组件丰富")

        result = json.loads(await scap_consolidate("acme", similarity_threshold=0.5))
        assert result["success"] is True
        assert result["merged"] == 0
        assert result["surviving"] == 2

    @pytest.mark.asyncio
    async def test_consolidate_advances_evolution_gen(self, mock_embedder):
        await scap_remember("acme", "数据库性能优化", "加索引", "查询快")
        await scap_remember("acme", "数据库性能优化方案", "加索引", "查询快")

        result = json.loads(await scap_consolidate("acme", similarity_threshold=0.5))
        assert result["success"] is True
        assert result["new_evolution_gen"] >= 1

    @pytest.mark.asyncio
    async def test_consolidate_preserves_originals(self, mock_embedder):
        """Consolidation deletes latent traces, not Decision/Experience records."""
        from scap.mcp_server import _get_store

        await scap_remember("acme", "数据库性能优化", "加索引", "查询快")
        await scap_remember("acme", "数据库性能优化方案", "加索引", "查询快")

        await scap_consolidate("acme", similarity_threshold=0.5)

        store = _get_store()
        decisions = store.list_decisions(project="acme")
        assert len(decisions) == 2  # Both original decisions preserved

    @pytest.mark.asyncio
    async def test_consolidate_empty_project(self, mock_embedder):
        result = json.loads(await scap_consolidate("empty-project"))
        assert result["success"] is True
        assert result["merged"] == 0


# ═══════════════════════════════════════════════════════
# Group 5: scap_evolved_context
# ═══════════════════════════════════════════════════════

class TestEvolvedContext:

    @pytest.mark.asyncio
    async def test_evolved_context_returns_traces(self, mock_embedder):
        await scap_remember("acme", "数据库选型", "PostgreSQL", "成熟")
        result = json.loads(await scap_evolved_context("acme"))
        assert result["success"] is True
        assert result["returned"] >= 1
        assert "fitness" in result["results"][0]
        assert "evolution_gen" in result["results"][0]

    @pytest.mark.asyncio
    async def test_evolved_context_min_fitness_filter(self, mock_embedder):
        from scap.mcp_server import _get_store
        from scap.models import LatentTrace

        await scap_remember("acme", "低质量决策", "选项A", "理由")

        # Manually set a high-fitness trace
        store = _get_store()
        traces = store.list_latent_traces(project="acme")
        assert len(traces) == 1
        traces[0].fitness = 0.9
        store.save_latent_trace(traces[0])

        # With min_fitness=0.8, should still return the trace
        result = json.loads(await scap_evolved_context("acme", min_fitness=0.8))
        assert result["returned"] == 1

        # With min_fitness=0.95, should return nothing
        result = json.loads(await scap_evolved_context("acme", min_fitness=0.95))
        assert result["returned"] == 0

    @pytest.mark.asyncio
    async def test_evolved_context_with_task_filter(self, mock_embedder):
        await scap_remember("acme", "数据库性能优化", "加索引", "查询快")
        await scap_remember("acme", "前端UI设计", "Tailwind", "样式一致")

        result = json.loads(await scap_evolved_context("acme", task_description="数据库"))
        assert result["success"] is True
        # Database decision should rank higher than UI decision
        titles = [r.get("title", "") for r in result["results"]]
        db_idx = next((i for i, t in enumerate(titles) if "数据库" in t), -1)
        ui_idx = next((i for i, t in enumerate(titles) if "前端" in t), -1)
        if db_idx >= 0 and ui_idx >= 0:
            assert db_idx < ui_idx

    @pytest.mark.asyncio
    async def test_evolved_context_includes_stats(self, mock_embedder):
        await scap_remember("acme", "测试决策", "选项A", "理由")
        result = json.loads(await scap_evolved_context("acme"))
        assert "evolution_gen" in result
        assert "total_traces" in result
        assert "filtered_traces" in result

    @pytest.mark.asyncio
    async def test_evolved_context_empty_project(self, mock_embedder):
        result = json.loads(await scap_evolved_context("empty-project"))
        assert result["success"] is True
        assert result["results"] == []


# ═══════════════════════════════════════════════════════
# Group 6: Backward compatibility
# ═══════════════════════════════════════════════════════

class TestBackwardCompatibility:

    @pytest.mark.asyncio
    async def test_recall_with_embedder(self, mock_embedder):
        """scap_recall should work normally when embedder is available."""
        await scap_remember("acme", "消息队列选型", "Kafka", "高吞吐量")
        result = json.loads(await scap_recall("acme", "消息队列"))
        assert result["success"] is True
        assert "Kafka" in result["context"]

    @pytest.mark.asyncio
    async def test_remember_returns_decision_id(self, mock_embedder):
        result = json.loads(await scap_remember("acme", "测试", "选项A", "理由"))
        assert result["success"] is True
        assert "decision_id" in result
        assert result["decision_id"].startswith("DC-")

    @pytest.mark.asyncio
    async def test_record_experience_returns_id(self, mock_embedder):
        result = json.loads(
            await scap_record_experience("acme", "情况", "行动", "教训")
        )
        assert result["success"] is True
        assert "experience_id" in result
        assert result["experience_id"].startswith("EX-")

    @pytest.mark.asyncio
    async def test_multiple_projects_isolated(self, mock_embedder):
        """Latent traces should be project-scoped."""
        await scap_remember("project-a", "数据库", "PostgreSQL", "成熟")
        await scap_remember("project-b", "数据库", "MySQL", "普及")

        result_a = json.loads(await scap_retrieve_latent("project-a", "数据库"))
        result_b = json.loads(await scap_retrieve_latent("project-b", "数据库"))

        assert result_a["count"] >= 1
        assert result_b["count"] >= 1
        # Results from project-a should not contain project-b's decisions
        a_ids = [r["entity_id"] for r in result_a["results"]]
        b_ids = [r["entity_id"] for r in result_b["results"]]
        assert not set(a_ids) & set(b_ids)
