"""pytest fixtures for SCAP v2 tests."""
import os
import pytest
from scap.store import MemoryStore


class _NoEmbedder:
    """Stub that simulates embedder unavailable. Prevents real model loading."""
    @property
    def is_available(self):
        return False
    def embed(self, text):
        return None
    def embed_batch(self, texts):
        return None


@pytest.fixture(autouse=True)
def _block_real_embedder(monkeypatch):
    """Prevent real sentence-transformers model loading during tests.

    Individual test files can override via their own mock_embedder fixture.
    This safety net ensures tests never hang on model downloads.
    """
    import scap.mcp_server as mcp_mod
    import scap.cli as cli_mod
    monkeypatch.setattr(mcp_mod, "_embedder", _NoEmbedder())
    monkeypatch.setattr(cli_mod, "_get_embedder", lambda: _NoEmbedder())


@pytest.fixture()
def store(tmp_path):
    db_path = str(tmp_path / "test.db")
    s = MemoryStore(db_path)
    s.initialize()
    yield s
    s.close()
