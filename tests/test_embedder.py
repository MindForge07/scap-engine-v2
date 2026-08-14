"""Tests for the optional embedding generator (mock sentence-transformers)."""
import builtins
import sys
import types

import pytest

from scap.embedder import Embedder


class _FakeTensor:
    def __init__(self, data):
        self._data = data

    def tolist(self):
        return self._data


class _FakeSentenceTransformer:
    def __init__(self, name):
        self.name = name

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
        if isinstance(texts, str):
            return _FakeTensor([0.1, 0.2, 0.3])
        return [_FakeTensor([0.1, 0.2, 0.3]) for _ in texts]


@pytest.fixture()
def fake_st(monkeypatch):
    """Install a fake sentence_transformers module so embed paths are testable."""
    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = _FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    return module


@pytest.fixture()
def no_st(monkeypatch):
    """Simulate sentence-transformers being uninstalled (ImportError on import)."""
    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sentence_transformers":
            raise ImportError("No module named 'sentence_transformers'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


class TestAvailability:
    def test_available_when_installed(self, fake_st):
        e = Embedder()
        assert e.is_available is True

    def test_unavailable_when_missing(self, no_st):
        e = Embedder()
        assert e.is_available is False

    def test_availability_cached(self, fake_st):
        e = Embedder()
        assert e.is_available is True
        # Second check must not re-import (cached).
        assert e.is_available is True


class TestEmbed:
    def test_embed_returns_vector(self, fake_st):
        e = Embedder()
        assert e.embed("hello world") == [0.1, 0.2, 0.3]

    def test_embed_empty_text_returns_none(self, fake_st):
        e = Embedder()
        assert e.embed("") is None
        assert e.embed("   ") is None

    def test_embed_unavailable_returns_none(self, no_st):
        e = Embedder()
        assert e.embed("anything") is None

    def test_model_loaded_lazily_once(self, fake_st):
        e = Embedder()
        e.embed("first")
        e.embed("second")
        assert e._model is not None
        assert e._model.name == "all-MiniLM-L6-v2"


class TestEmbedBatch:
    def test_batch(self, fake_st):
        e = Embedder()
        out = e.embed_batch(["a", "b"])
        assert len(out) == 2

    def test_batch_empty(self, fake_st):
        e = Embedder()
        assert e.embed_batch([]) == []

    def test_batch_unavailable(self, no_st):
        e = Embedder()
        assert e.embed_batch(["a"]) is None
