"""Scale tests: production-grade data volumes (5k decisions, 2k experiences).

Verifies that retrieval, layered-injection scoring (_format_recall, which L1
runs on EVERY step) and context export stay within budget as memory grows —
the CI/coverage gate must hold at production data sizes, not toy fixtures.

Queries are real production user messages (extracted from the DSH session
replay in dsh/verify/replay.py).
"""
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from scap.mcp_server import _format_recall
from scap.models import Decision, Experience
from scap.store import MemoryStore

# Real production user messages from the DSH session replay.
PROD_QUERIES = [
    "深度分析一下这个工作区下scap这个项目",
    "分析deepseek harness的代码，进行对比分析，判断scap代码的价值",
    "scap比dsh自带原生的记忆系统的插件更好吗？为什么",
    "需要考虑scap在整个ai运行中的作用闭环",
    "先告诉我现在的scap是否是很繁杂的",
    "分层注入的逻辑是怎样的",
    "把 v2.1 提交推送到 GitHub",
    "直接注入挂载用dsh测试会不会有问题",
    "SCAp 的存在在整个ai决策中是否是正效益",
    "需要调研一下其他通用的前沿的记忆机制",
]


@pytest.fixture(scope="module")
def big_store(tmp_path_factory):
    db = str(tmp_path_factory.mktemp("scale") / "scale.db")
    store = MemoryStore(db)
    store.initialize()
    now = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    for i in range(5000):
        store.save_decision(Decision(
            project="scale", title=f"决策项 {i}: 技术选型",
            decision=f"方案 {i}: 组件 X{i % 50}",
            rationale=f"理由 {i}: 性能、成本、可维护性、生态（填充文本用于压力）",
            importance=(i % 5) + 1,
            created_at=now - timedelta(days=i % 180),
            updated_at=now - timedelta(days=i % 180),
        ))
    for i in range(2000):
        store.save_experience(Experience(
            project="scale", situation=f"事故 {i}: 高并发下的性能问题",
            action=f"修复 {i}: 优化查询与缓存",
            lesson=f"教训 {i}: 必须做性能测试与容量规划",
            importance=(i % 5) + 1,
            created_at=now - timedelta(days=i % 180),
        ))
    elapsed = time.perf_counter() - t0
    print(f"\n[scale] seeded 5000 decisions + 2000 experiences in {elapsed:.1f}s")
    yield store
    store.close()


class TestScaleRetrieval:
    def test_search_latency_budget(self, big_store):
        lat = []
        for q in PROD_QUERIES:
            t0 = time.perf_counter()
            big_store.search("scale", q, limit=5)
            lat.append(time.perf_counter() - t0)
        avg = sum(lat) / len(lat)
        print(f"[scale] search avg={avg * 1000:.1f}ms max={max(lat) * 1000:.1f}ms")
        assert avg < 1.0  # 1s budget, same as the 1k stress suite

    def test_injection_scoring_latency_budget(self, big_store):
        # L1 layered injection runs _format_recall on EVERY step over ALL active
        # decisions (5k here): it must stay cheap (200ms/step budget).
        lat = []
        for q in PROD_QUERIES:
            t0 = time.perf_counter()
            _format_recall(big_store, "scale", q)
            lat.append(time.perf_counter() - t0)
        avg = sum(lat) / len(lat)
        print(f"[scale] recall/injection avg={avg * 1000:.1f}ms max={max(lat) * 1000:.1f}ms")
        assert avg < 0.2

    def test_injection_output_sane_at_scale(self, big_store):
        out = _format_recall(big_store, "scale", PROD_QUERIES[0])
        assert out
        assert len(out) < 8000


class TestScaleExport:
    def test_export_budgeted(self, big_store, tmp_path):
        out = str(tmp_path / "scale.md")
        t0 = time.perf_counter()
        big_store.export_context("scale", out, max_chars=12000)
        elapsed = time.perf_counter() - t0
        content = open(out, encoding="utf-8").read()
        print(f"[scale] export(12000 cap) {elapsed * 1000:.0f}ms len={len(content)}")
        assert elapsed < 3.0
        assert len(content) <= 12500
        assert os.path.exists(str(tmp_path / "scale.json"))

    def test_export_full_size(self, big_store, tmp_path):
        out = str(tmp_path / "scale-full.md")
        t0 = time.perf_counter()
        big_store.export_context("scale", out)
        elapsed = time.perf_counter() - t0
        size = os.path.getsize(out)
        print(f"[scale] export(full 5k) {elapsed * 1000:.0f}ms {size / 1024:.0f}KB")
        assert elapsed < 10.0


class TestScaleIntegrity:
    def test_counts_exact(self, big_store):
        stats = big_store.get_stats(project="scale")
        assert stats["decision_count"] == 5000
        assert stats["experience_count"] == 2000

    def test_reopen_and_migrate_big_db(self, big_store):
        store2 = MemoryStore(big_store.db_path)
        store2.initialize()  # migrations run against the big DB
        assert store2.get_stats(project="scale")["decision_count"] == 5000
        store2.close()
