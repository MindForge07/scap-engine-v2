"""pytest fixtures for SCAP v2 tests."""
import os
import pytest
from scap.store import MemoryStore


@pytest.fixture()
def store(tmp_path):
    db_path = str(tmp_path / "test.db")
    s = MemoryStore(db_path)
    s.initialize()
    return s
