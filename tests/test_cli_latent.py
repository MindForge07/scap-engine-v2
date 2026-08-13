"""Tests for CLI latent space evolution commands (Phase 3).

Covers:
  1. status command shows latent trace count + evolution gen
  2. latent — semantic similarity search
  3. consolidate — trace merging
  4. evolved — fitness-weighted context
  5. traces — list latent traces
  6. embed — backfill embeddings
"""
import math
import re

import pytest
from click.testing import CliRunner

from scap.cli import cli
from scap.models import Decision, Experience, LatentTrace


# ── Mock Embedder (same deterministic logic as test_mcp_latent.py) ──

class MockEmbedder:
    """Deterministic mock embedder with CJK-aware tokenization."""

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


# ── Fixtures ──

@pytest.fixture()
def runner(tmp_path):
    db_path = str(tmp_path / "cli_latent_test.db")
    r = CliRunner()
    return r, ["--db", db_path]


@pytest.fixture(autouse=True)
def _patch_embedder(monkeypatch):
    """Default: embedder unavailable. Use ``mock_embedder`` to enable."""
    import scap.cli as cli_mod
    monkeypatch.setattr(cli_mod, "_get_embedder", lambda: MockEmbedder(available=False))


@pytest.fixture
def mock_embedder(monkeypatch):
    import scap.cli as cli_mod
    embedder = MockEmbedder(available=True)
    monkeypatch.setattr(cli_mod, "_get_embedder", lambda: embedder)
    return embedder


def _create_trace(store, entity_id, entity_type, project, text, fitness=0.5, gen=0):
    """Helper: create a decision/experience + latent trace."""
    embedder = MockEmbedder(available=True)
    embedding = embedder.embed(text)

    if entity_type == "decision":
        d = Decision(project=project, title=text[:20], decision=text)
        d.embedding = embedding
        d = store.save_decision(d)
        entity_id = d.id
    else:
        e = Experience(project=project, situation=text[:20], lesson=text)
        e.embedding = embedding
        e = store.save_experience(e)
        entity_id = e.id

    trace = LatentTrace(
        entity_id=entity_id, entity_type=entity_type,
        project=project, embedding=embedding,
        fitness=fitness, evolution_gen=gen,
    )
    store.save_latent_trace(trace)
    return trace


# ═══════════════════════════════════════════════════════
# 1. Status — latent stats visible
# ═══════════════════════════════════════════════════════

class TestStatusLatent:
    def test_status_shows_latent_rows(self, runner):
        r, base_args = runner
        result = r.invoke(cli, base_args + ["status"])
        assert result.exit_code == 0
        assert "Latent Traces" in result.output
        assert "Evolution Gen" in result.output


# ═══════════════════════════════════════════════════════
# 2. latent — semantic search
# ═══════════════════════════════════════════════════════

class TestLatentCommand:
    def test_latent_without_embedder(self, runner):
        r, base_args = runner
        result = r.invoke(cli, base_args + ["latent", "数据库", "-p", "acme"])
        assert result.exit_code == 0
        assert "not available" in result.output.lower()

    def test_latent_with_results(self, runner, mock_embedder):
        r, base_args = runner
        # Create a trace via the store
        from scap.store import MemoryStore
        store = MemoryStore(base_args[1])
        store.initialize()
        _create_trace(store, "", "decision", "acme", "数据库性能优化")

        result = r.invoke(cli, base_args + ["latent", "数据库", "-p", "acme"])
        assert result.exit_code == 0
        assert "semantic matches" in result.output

    def test_latent_no_results(self, runner, mock_embedder):
        r, base_args = runner
        result = r.invoke(cli, base_args + ["latent", "anything", "-p", "empty"])
        assert result.exit_code == 0
        assert "No latent matches" in result.output


# ═══════════════════════════════════════════════════════
# 3. consolidate — trace merging
# ═══════════════════════════════════════════════════════

class TestConsolidateCommand:
    def test_consolidate_not_enough_traces(self, runner):
        r, base_args = runner
        result = r.invoke(cli, base_args + ["consolidate", "-p", "acme"])
        assert result.exit_code == 0
        assert "Not enough" in result.output

    def test_consolidate_merges_similar(self, runner, mock_embedder):
        r, base_args = runner
        from scap.store import MemoryStore
        store = MemoryStore(base_args[1])
        store.initialize()
        _create_trace(store, "", "decision", "acme", "数据库性能优化")
        _create_trace(store, "", "decision", "acme", "数据库性能优化方案")

        result = r.invoke(cli, base_args + ["consolidate", "-p", "acme", "-t", "0.5"])
        assert result.exit_code == 0
        assert "Merged" in result.output

    def test_consolidate_keeps_different(self, runner, mock_embedder):
        r, base_args = runner
        from scap.store import MemoryStore
        store = MemoryStore(base_args[1])
        store.initialize()
        _create_trace(store, "", "decision", "acme", "数据库选型")
        _create_trace(store, "", "decision", "acme", "前端框架选择")

        result = r.invoke(cli, base_args + ["consolidate", "-p", "acme", "-t", "0.5"])
        assert result.exit_code == 0
        assert "Merged: 0" in result.output


# ═══════════════════════════════════════════════════════
# 4. evolved — fitness-weighted context
# ═══════════════════════════════════════════════════════

class TestEvolvedCommand:
    def test_evolved_empty(self, runner):
        r, base_args = runner
        result = r.invoke(cli, base_args + ["evolved", "-p", "acme"])
        assert result.exit_code == 0
        assert "No latent traces" in result.output

    def test_evolved_shows_table(self, runner, mock_embedder):
        r, base_args = runner
        from scap.store import MemoryStore
        store = MemoryStore(base_args[1])
        store.initialize()
        _create_trace(store, "", "decision", "acme", "数据库选型", fitness=0.8)

        result = r.invoke(cli, base_args + ["evolved", "-p", "acme"])
        assert result.exit_code == 0
        assert "Evolved Context" in result.output

    def test_evolved_min_fitness_filter(self, runner, mock_embedder):
        r, base_args = runner
        from scap.store import MemoryStore
        store = MemoryStore(base_args[1])
        store.initialize()
        _create_trace(store, "", "decision", "acme", "低质量", fitness=0.3)

        # Filter above 0.5 → no results
        result = r.invoke(cli, base_args + ["evolved", "-p", "acme", "-f", "0.5"])
        assert result.exit_code == 0
        assert "No latent traces" in result.output


# ═══════════════════════════════════════════════════════
# 5. traces — list latent traces
# ═══════════════════════════════════════════════════════

class TestTracesCommand:
    def test_traces_empty(self, runner):
        r, base_args = runner
        result = r.invoke(cli, base_args + ["traces"])
        assert result.exit_code == 0
        assert "No latent traces" in result.output

    def test_traces_shows_table(self, runner, mock_embedder):
        r, base_args = runner
        from scap.store import MemoryStore
        store = MemoryStore(base_args[1])
        store.initialize()
        _create_trace(store, "", "decision", "acme", "测试决策")

        result = r.invoke(cli, base_args + ["traces", "-p", "acme"])
        assert result.exit_code == 0
        assert "Latent Traces" in result.output
        assert "acme" in result.output


# ═══════════════════════════════════════════════════════
# 6. embed — backfill embeddings
# ═══════════════════════════════════════════════════════

class TestEmbedCommand:
    def test_embed_without_embedder(self, runner):
        r, base_args = runner
        result = r.invoke(cli, base_args + ["embed", "-p", "acme"])
        assert result.exit_code == 0
        assert "not available" in result.output.lower()

    def test_embed_backfills_decisions(self, runner, mock_embedder):
        r, base_args = runner
        from scap.store import MemoryStore
        store = MemoryStore(base_args[1])
        store.initialize()
        # Create decisions without embeddings
        d1 = Decision(project="acme", title="数据库选型", decision="PostgreSQL")
        store.save_decision(d1)
        d2 = Decision(project="acme", title="缓存策略", decision="Redis")
        store.save_decision(d2)

        result = r.invoke(cli, base_args + ["embed", "-p", "acme"])
        assert result.exit_code == 0
        assert "backfill complete" in result.output.lower()
        assert "2/2" in result.output  # both decisions embedded

        # Verify traces were created
        traces = store.list_latent_traces(project="acme")
        assert len(traces) == 2

    def test_embed_backfills_experiences(self, runner, mock_embedder):
        r, base_args = runner
        from scap.store import MemoryStore
        store = MemoryStore(base_args[1])
        store.initialize()
        e1 = Experience(project="acme", situation="CPU飙升", lesson="加索引")
        store.save_experience(e1)

        result = r.invoke(cli, base_args + ["embed", "-p", "acme"])
        assert result.exit_code == 0
        assert "1/1" in result.output  # one experience embedded

    def test_embed_idempotent(self, runner, mock_embedder):
        r, base_args = runner
        from scap.store import MemoryStore
        store = MemoryStore(base_args[1])
        store.initialize()
        d1 = Decision(project="acme", title="测试", decision="选项A")
        store.save_decision(d1)

        # First run: embeds 1 decision
        r.invoke(cli, base_args + ["embed", "-p", "acme"])
        # Second run: should skip (0 new)
        result = r.invoke(cli, base_args + ["embed", "-p", "acme"])
        assert result.exit_code == 0
        assert "0/1" in result.output  # 0 new embeddings

    def test_embed_mixed_records(self, runner, mock_embedder):
        r, base_args = runner
        from scap.store import MemoryStore
        store = MemoryStore(base_args[1])
        store.initialize()
        store.save_decision(Decision(project="acme", title="决策一", decision="A"))
        store.save_experience(Experience(project="acme", situation="情况一", lesson="教训一"))

        result = r.invoke(cli, base_args + ["embed", "-p", "acme"])
        assert result.exit_code == 0
        assert "1/1" in result.output  # 1 decision
        assert "1/1" in result.output  # 1 experience
