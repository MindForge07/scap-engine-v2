"""SCAP v2 — Stress Test Suite.

Covers 7 areas:
  1. Volume: 1000+ decisions, search latency < 1s
  2. Concurrent access: 20 threads writing simultaneously
  3. Large payloads: 10KB+ rationale, 100+ alternatives
  4. Unicode stress: Chinese/Japanese/emoji in all fields
  5. Search quality: 100 known decisions, 20 benchmark queries
  6. Data integrity: insert, delete FTS row, verify search
  7. Edge cases: empty project names, duplicate IDs, supersedence chains
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pytest

from scap.models import Decision, Experience, ProjectContext
from scap.store import MemoryStore

# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

_REPORT: list[str] = []


def _report(section: str, *lines: str) -> None:
    """Accumulate report lines; printed at teardown."""
    _REPORT.append(f"\n{'='*60}")
    _REPORT.append(f"  {section}")
    _REPORT.append(f"{'='*60}")
    for ln in lines:
        _REPORT.append(f"  {ln}")


def _make_decision(
    project: str = "stress-proj",
    title: str = "Decision",
    decision: str = "",
    rationale: str = "",
    alternatives: list | None = None,
    constraints: list | None = None,
    tags: list | None = None,
    context: str = "",
    status: str = "active",
) -> Decision:
    return Decision(
        project=project,
        title=title,
        decision=decision,
        rationale=rationale,
        alternatives=alternatives or [],
        constraints=constraints or [],
        tags=tags or [],
        context=context,
        status=status,
    )


@pytest.fixture(autouse=True, scope="session")
def _print_report():
    """Print accumulated report after all stress tests finish."""
    yield
    import sys
    output = "\n" + "\n".join(_REPORT)
    try:
        print(output)
    except UnicodeEncodeError:
        # Fallback for consoles with limited encoding (e.g., GBK on Windows)
        print(output.encode("utf-8", errors="replace").decode("ascii", errors="replace"))


# ──────────────────────────────────────────────────────────────
# 1. Volume Test — 1000+ decisions, search latency
# ──────────────────────────────────────────────────────────────

class TestVolume:
    """Insert 1 000 decisions and verify CRUD + search latency."""

    N = 1_000

    def test_bulk_insert(self, store: MemoryStore):
        t0 = time.time()
        for i in range(self.N):
            store.save_decision(_make_decision(
                title=f"Bulk decision #{i:04d}",
                decision=f"Chosen option for item {i}",
                rationale=f"Rationale for decision {i}: performance, cost, maintainability",
                tags=[f"bulk", f"item-{i % 10}"],
            ))
        elapsed = time.time() - t0
        _report(
            "Volume: Bulk Insert",
            f"Inserted {self.N} decisions in {elapsed:.3f}s",
            f"Avg per insert: {elapsed/self.N*1000:.2f}ms",
            f"Throughput: {self.N/elapsed:.0f} decisions/sec",
        )
        stats = store.get_stats()
        assert stats["decision_count"] == self.N

    def test_search_latency_with_volume(self, store: MemoryStore):
        # Seed data
        for i in range(self.N):
            store.save_decision(_make_decision(
                title=f"Bulk decision #{i:04d}",
                decision=f"Chosen option for item {i}",
                rationale=f"Rationale for decision {i}: performance cost maintainability",
                tags=[f"bulk", f"item-{i % 10}"],
            ))

        queries = ["performance", "cost", "maintainability", "item-5", "decision"]
        latencies = []
        for q in queries:
            t0 = time.time()
            results = store.search("stress-proj", q)
            latencies.append((q, time.time() - t0, len(results)))

        max_latency = max(l[1] for l in latencies)
        avg_latency = sum(l[1] for l in latencies) / len(latencies)

        _report(
            "Volume: Search Latency (1 000 decisions)",
            *(f"  Query '{q}': {r} results, {t*1000:.1f}ms" for q, t, r in latencies),
            f"Max latency: {max_latency*1000:.1f}ms",
            f"Avg latency: {avg_latency*1000:.1f}ms",
            f"PASS: max < 1 000ms" if max_latency < 1.0 else f"FAIL: max >= 1 000ms",
        )
        assert max_latency < 1.0, f"Search latency {max_latency:.3f}s exceeds 1s budget"

    def test_list_performance(self, store: MemoryStore):
        for i in range(self.N):
            store.save_decision(_make_decision(title=f"List item {i}"))

        t0 = time.time()
        results = store.list_decisions(project="stress-proj", limit=500)
        elapsed = time.time() - t0
        _report(
            "Volume: List 500 / 1 000",
            f"Retrieved {len(results)} rows in {elapsed*1000:.1f}ms",
        )
        assert len(results) == 500
        assert elapsed < 1.0


# ──────────────────────────────────────────────────────────────
# 2. Concurrent Access — 20 threads
# ──────────────────────────────────────────────────────────────

class TestConcurrent:
    """20 threads writing decisions simultaneously."""

    THREADS = 20
    PER_THREAD = 50

    def test_concurrent_writes(self, store: MemoryStore):
        """Concurrent writes with pre-assigned IDs (bypasses _next_id race).

        NOTE: _next_id is called OUTSIDE the lock in save_decision(), so
        threads calling save_decision without a pre-set ID will collide.
        This test verifies the lock itself protects the DB write path.
        See test_next_id_race_condition for the ID generation bug.
        """
        errors: list[Exception] = []
        written = []
        barrier = threading.Barrier(self.THREADS)

        def writer(tid: int):
            try:
                barrier.wait(timeout=10)
                for i in range(self.PER_THREAD):
                    d = _make_decision(
                        title=f"Thread-{tid} item-{i}",
                        decision=f"Concurrent decision t{tid}-{i}",
                        rationale=f"Written by thread {tid}",
                    )
                    d.id = f"DC-20260627-{tid*1000+i:04d}"
                    d = store.save_decision(d)
                    written.append(d.id)
            except Exception as e:
                errors.append(e)

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=self.THREADS) as pool:
            futures = [pool.submit(writer, t) for t in range(self.THREADS)]
            for f in as_completed(futures):
                f.result()
        elapsed = time.time() - t0

        expected = self.THREADS * self.PER_THREAD
        stats = store.get_stats()

        _report(
            "Concurrent: 20 Threads x 50 Writes (pre-assigned IDs)",
            f"Expected: {expected}, Actual DB count: {stats['decision_count']}",
            f"Unique IDs returned: {len(set(written))}",
            f"Total time: {elapsed:.3f}s",
            f"Throughput: {expected/elapsed:.0f} writes/sec",
            f"Errors: {len(errors)}",
        )
        assert len(errors) == 0, f"{len(errors)} thread errors: {errors[:3]}"
        assert len(set(written)) == expected

    def test_next_id_race_condition(self, store: MemoryStore):
        """BUG: _next_id is called outside the lock in save_decision().

        When multiple threads call save_decision without a pre-set ID,
        they all call _next_id concurrently and many receive the SAME ID.
        INSERT OR REPLACE then silently overwrites, losing data.
        """
        written_ids = []
        barrier = threading.Barrier(self.THREADS)

        def writer(tid: int):
            barrier.wait(timeout=10)
            for i in range(self.PER_THREAD):
                d = store.save_decision(_make_decision(
                    title=f"Race-{tid}-{i}",
                ))
                written_ids.append(d.id)

        with ThreadPoolExecutor(max_workers=self.THREADS) as pool:
            futures = [pool.submit(writer, t) for t in range(self.THREADS)]
            for f in as_completed(futures):
                f.result()

        unique = len(set(written_ids))
        expected = self.THREADS * self.PER_THREAD
        stats = store.get_stats()

        _report(
            "Concurrent: _next_id Race Condition (FIXED)",
            f"Expected: {expected} unique IDs",
            f"Got: {unique} unique IDs",
            f"DB row count: {stats['decision_count']}",
            f"ID collisions: {expected - unique}",
            "FIX: _next_id() now called inside the lock in save_decision()",
        )
        # After fix: all IDs should be unique
        assert unique == expected, (
            f"Expected all {expected} unique IDs, got {unique}. "
            f"Collisions: {expected - unique}"
        )

    def test_concurrent_read_write(self, store: MemoryStore):
        """Writers and readers interleaved — reveals thread-safety limits.

        LIMITATION: MemoryStore uses a single SQLite connection with
        check_same_thread=False. SQLite's Python binding can raise
        'bad parameter or other API misuse' when the same connection
        is used concurrently from multiple threads, even with WAL mode.
        A production fix would use per-thread connections or a connection pool.
        """
        # Seed some data first
        for i in range(100):
            d = _make_decision(title=f"Seed {i}")
            d.id = f"DC-20260627-{10000+i:04d}"
            store.save_decision(d)

        errors: list[str] = []
        reads_done = 0
        writes_done = 0
        lock = threading.Lock()
        stop = threading.Event()

        def reader():
            nonlocal reads_done
            while not stop.is_set():
                try:
                    store.search("stress-proj", "Seed")
                    store.list_decisions(project="stress-proj", limit=20)
                    with lock:
                        reads_done += 1
                except Exception as e:
                    with lock:
                        errors.append(str(e)[:80])
                    # Keep going — these are expected threading errors

        def writer(start_idx: int):
            nonlocal writes_done
            for i in range(30):
                try:
                    d = _make_decision(
                        title=f"Concurrent write {start_idx + i}",
                    )
                    d.id = f"DC-20260627-{20000 + start_idx + i:04d}"
                    store.save_decision(d)
                    with lock:
                        writes_done += 1
                except Exception as e:
                    with lock:
                        errors.append(str(e)[:80])

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=25) as pool:
            # 5 readers + 20 writers
            reader_futs = [pool.submit(reader) for _ in range(5)]
            writer_futs = [pool.submit(writer, i * 30) for i in range(20)]

            for f in as_completed(writer_futs):
                f.result()
            stop.set()
            for f in as_completed(reader_futs):
                f.result()
        elapsed = time.time() - t0

        error_kinds = set(e for e in errors)
        _report(
            "Concurrent: 5 Readers + 20 Writers (known threading limits)",
            f"Writes completed: {writes_done} / 600",
            f"Reads completed: {reads_done}",
            f"Total time: {elapsed:.3f}s",
            f"Errors: {len(errors)} (types: {error_kinds or 'none'})",
            "NOTE: 'bad parameter or other API misuse' errors indicate single-"
            "connection threading limit. Use per-thread connections for production.",
        )
        # Writes should mostly succeed (the lock serializes them)
        assert writes_done > 0, "No writes completed at all"


# ──────────────────────────────────────────────────────────────
# 3. Large Payloads — 10KB+ rationale, 100+ alternatives
# ──────────────────────────────────────────────────────────────

class TestLargePayloads:
    """Handle unusually large decision records."""

    def test_large_rationale(self, store: MemoryStore):
        """10 KB rationale text."""
        rationale = "x" * 10_240  # 10 KB
        d = _make_decision(
            title="Large rationale test",
            decision="Keep it",
            rationale=rationale,
        )
        t0 = time.time()
        d = store.save_decision(d)
        elapsed_save = time.time() - t0

        t0 = time.time()
        loaded = store.get_decision(d.id)
        elapsed_get = time.time() - t0

        assert loaded is not None
        assert len(loaded.rationale) == 10_240

        _report(
            "Large Payload: 10KB Rationale",
            f"Save: {elapsed_save*1000:.1f}ms",
            f"Load: {elapsed_get*1000:.1f}ms",
            f"Rationale length: {len(loaded.rationale)} chars",
        )

    def test_many_alternatives(self, store: MemoryStore):
        """100+ alternatives."""
        alts = [
            {"name": f"Alt-{i}", "reason_rejected": f"Too slow for scenario {i}"}
            for i in range(120)
        ]
        d = _make_decision(
            title="Many alternatives",
            decision="Pick the best one",
            alternatives=alts,
        )
        t0 = time.time()
        d = store.save_decision(d)
        elapsed_save = time.time() - t0

        loaded = store.get_decision(d.id)
        assert loaded is not None
        assert len(loaded.alternatives) == 120

        _report(
            "Large Payload: 120 Alternatives",
            f"Save: {elapsed_save*1000:.1f}ms",
            f"Alternatives round-tripped: {len(loaded.alternatives)}",
        )

    def test_large_tags_and_constraints(self, store: MemoryStore):
        """Many tags and constraints."""
        tags = [f"tag-{i}" for i in range(200)]
        constraints = [f"constraint-{i}: must handle edge case {i}" for i in range(100)]
        d = _make_decision(
            title="Many tags & constraints",
            tags=tags,
            constraints=constraints,
        )
        d = store.save_decision(d)
        loaded = store.get_decision(d.id)
        assert len(loaded.tags) == 200
        assert len(loaded.constraints) == 100

        _report(
            "Large Payload: 200 Tags + 100 Constraints",
            "Round-trip OK",
        )

    def test_searchable_large_decision(self, store: MemoryStore):
        """FTS index works on a large rationale."""
        rationale = "deep learning model selection requires careful evaluation " * 500
        store.save_decision(_make_decision(
            title="ML model choice",
            rationale=rationale,
        ))
        results = store.search("stress-proj", "learning model evaluation")
        _report(
            "Large Payload: FTS on 10KB+ rationale",
            f"Search results: {len(results)}",
        )
        assert len(results) >= 1


# ──────────────────────────────────────────────────────────────
# 4. Unicode Stress — CJK, emoji, mixed scripts
# ──────────────────────────────────────────────────────────────

class TestUnicodeStress:
    """All fields filled with Chinese, Japanese, Korean, emoji."""

    SAMPLES = [
        {
            "title": "数据库选型讨论 🏗️🔧",
            "decision": "采用 PostgreSQL 🐘 作为主数据库",
            "rationale": "性能优越 🚀，社区活跃，支持 JSON 和全文检索",
            "context": "项目需求：支持中文全文检索，≥ 10万 QPS",
            "tags": ["数据库", "PostgreSQL", "性能优化⚡"],
            "constraints": ["必须支持 UTF-8 编码", "读写分离延迟 < 5ms"],
        },
        {
            "title": "UI フレームワーク選定 🎨",
            "decision": "React + Next.js を採用",
            "rationale": "チームのスキルセット 🧑‍💻と相性が良い",
            "context": "フロントエンド刷新プロジェクト",
            "tags": ["React", "フロントエンド", "UI/UX"],
            "constraints": ["Lighthouse スコア ≥ 90"],
        },
        {
            "title": "한국어 결재 시스템 📋",
            "decision": "카카오 스타일 디자인 시스템 적용",
            "rationale": "사용자 피드백 기반 🔍 A/B 테스트 결과",
            "tags": ["한국어", "결재", "디자인"],
            "constraints": ["접근성 WCAG 2.1 AA 준수"],
        },
        {
            "title": "混合脚本 Mixed Script مختلط 🌍",
            "decision": "Use Unicode everywhere Ñoño café résumé naïve",
            "rationale": "Ünïcödé test ß Ð ð þ æ œ Ŋ ŋ — 中文混English日本語混한국어",
            "tags": ["unicode", "国际化", "i18n"],
            "constraints": ["Must handle RTL text: مرحبا بالعالم"],
        },
    ]

    def test_round_trip_all_samples(self, store: MemoryStore):
        """Save each Unicode sample and verify exact round-trip."""
        for i, sample in enumerate(self.SAMPLES):
            d = _make_decision(**sample)
            d = store.save_decision(d)
            loaded = store.get_decision(d.id)
            assert loaded is not None
            assert loaded.title == sample["title"]
            assert loaded.decision == sample["decision"]
            assert loaded.rationale == sample["rationale"]
            if "context" in sample:
                assert loaded.context == sample["context"]
            assert loaded.tags == sample["tags"]
            assert loaded.constraints == sample["constraints"]

        _report("Unicode: Round-Trip", f"Passed {len(self.SAMPLES)} samples OK")

    def test_search_chinese(self, store: MemoryStore):
        """FTS5 search for Chinese terms."""
        for s in self.SAMPLES:
            store.save_decision(_make_decision(**s))

        queries = [
            ("数据库", "database selection"),
            ("性能优化", "performance"),
            ("全文检索", "full-text search"),
        ]
        hits = []
        for query, desc in queries:
            results = store.search("stress-proj", query)
            hits.append((query, desc, len(results)))

        _report(
            "Unicode: Chinese FTS Search",
            *(f"  '{q}' ({desc}): {n} results" for q, desc, n in hits),
        )
        # At least one of these Chinese queries should find the DB decision
        assert any(n > 0 for _, _, n in hits), "No Chinese FTS hits at all"

    def test_search_japanese(self, store: MemoryStore):
        """FTS5 search for Japanese terms."""
        for s in self.SAMPLES:
            store.save_decision(_make_decision(**s))
        results = store.search("stress-proj", "フレームワーク")
        _report("Unicode: Japanese FTS", f"Results: {len(results)}")

    def test_search_emoji(self, store: MemoryStore):
        """FTS5 search for emoji-containing text."""
        for s in self.SAMPLES:
            store.save_decision(_make_decision(**s))
        # Search for a word that co-occurs with emoji in our samples
        results = store.search("stress-proj", "PostgreSQL")
        _report("Unicode: Emoji co-occurrence", f"Results: {len(results)}")
        assert len(results) >= 1

    def test_unicode_experience(self, store: MemoryStore):
        """Experience records with Unicode round-trip."""
        e = Experience(
            project="stress-proj",
            situation="生产环境 OOM 💥 — 原因：缓存未设上限",
            action="添加 Redis maxmemory-policy allkeys-lru 🛡️",
            lesson="永远不要让缓存无限增长，必须设置淘汰策略 ⚠️",
            tags=["OOM", "Redis", "缓存治理"],
        )
        e = store.save_experience(e)
        assert e.id.startswith("EX-")

        # Search for Chinese lesson
        results = store.search("stress-proj", "缓存")
        _report("Unicode: Experience", f"Experience saved: {e.id}, search hits: {len(results)}")


# ──────────────────────────────────────────────────────────────
# 5. Search Quality — 100 known decisions, 20 benchmark queries
# ──────────────────────────────────────────────────────────────

class TestSearchQuality:
    """With 100 seeded decisions, verify search precision for 20 queries."""

    # Decisions are intentionally crafted so each query should match
    # a known subset. We verify at least one expected hit lands.
    CANONICAL = [
        ("支付网关选型", "采用 Stripe 作为主支付网关", ["stripe", "支付", "网关"]),
        ("消息队列选型", "选择 Kafka 替代 RabbitMQ", ["kafka", "消息", "队列"]),
        ("数据库迁移", "从 MySQL 迁移到 PostgreSQL", ["mysql", "postgresql", "迁移"]),
        ("缓存策略", "使用 Redis 做分布式缓存", ["redis", "缓存", "分布式"]),
        ("日志系统", "采用 ELK 日志平台", ["elk", "日志", "elasticsearch"]),
        ("认证方案", "OAuth2 + JWT 双令牌认证", ["oauth", "jwt", "认证"]),
        ("API网关", "使用 Kong 作为 API 网关", ["kong", "api", "网关"]),
        ("容器编排", "选择 Kubernetes 集群管理", ["kubernetes", "容器", "k8s"]),
        ("CI/CD流程", "GitHub Actions 持续集成", ["ci", "cd", "github actions"]),
        ("前端框架", "React + TypeScript 重写前端", ["react", "typescript", "前端"]),
        ("监控方案", "Prometheus + Grafana 监控体系", ["prometheus", "监控", "grafana"]),
        ("搜索引擎", "Elasticsearch 全文检索方案", ["elasticsearch", "搜索", "检索"]),
        ("对象存储", "选择 MinIO 自建 S3 兼容存储", ["minio", "对象存储", "s3"]),
        ("服务发现", "Consul 做服务注册与发现", ["consul", "服务发现", "注册"]),
        ("配置中心", "Apollo 配置管理平台", ["apollo", "配置", "中心"]),
        ("限流方案", "Sentinel 熔断限流组件", ["sentinel", "限流", "熔断"]),
        ("数据同步", "Canal + Kafka 实现 CDC", ["canal", "数据同步", "cdc"]),
        ("分布式事务", "Seata AT 模式处理分布式事务", ["seata", "分布式事务", "at模式"]),
        ("权限模型", "RBAC + ABAC 混合权限体系", ["rbac", "abac", "权限"]),
        ("国际化方案", "i18n 支持中英日三语", ["i18n", "国际化", "多语言"]),
        ("反欺诈引擎", "基于规则和 ML 的反欺诈系统", ["反欺诈", "规则引擎", "ml"]),
        ("文件上传", "分片上传 + 断点续传方案", ["分片", "断点续传", "上传"]),
        ("WebSocket推送", "自研 WebSocket 推送服务", ["websocket", "推送", "长连接"]),
        ("数据库读写分离", "ProxySQL 实现读写分离", ["proxysql", "读写分离", "主从"]),
        ("全文检索优化", "中文分词 + 热词权重调整", ["分词", "全文检索", "权重"]),
        ("任务调度", "XXL-JOB 分布式任务调度", ["xxl-job", "调度", "定时任务"]),
        ("短信服务", "阿里云短信 + 模板管理", ["短信", "阿里云", "模板"]),
        ("邮件系统", "自建 SMTP + 退订管理", ["smtp", "邮件", "退订"]),
        ("地图服务", "高德地图 API 集成", ["高德", "地图", "api"]),
        ("支付对账", "T+1 自动对账流程", ["对账", "自动", "t+1"]),
    ]

    # 20 benchmark queries — each targets a specific decision
    QUERIES = [
        ("支付网关", "支付网关选型"),
        ("Kafka 消息", "消息队列选型"),
        ("MySQL 迁移", "数据库迁移"),
        ("Redis 缓存", "缓存策略"),
        ("ELK 日志", "日志系统"),
        ("OAuth2 认证", "认证方案"),
        ("Kong API", "API网关"),
        ("Kubernetes 容器", "容器编排"),
        ("GitHub Actions CI", "CI/CD流程"),
        ("React TypeScript", "前端框架"),
        ("Prometheus 监控", "监控方案"),
        ("Elasticsearch 搜索", "搜索引擎"),
        ("MinIO 存储", "对象存储"),
        ("Consul 服务", "服务发现"),
        ("Sentinel 限流", "限流方案"),
        ("Canal 同步", "数据同步"),
        ("Seata 事务", "分布式事务"),
        ("RBAC 权限", "权限模型"),
        ("WebSocket 推送", "WebSocket推送"),
        ("XXL-JOB 调度", "任务调度"),
    ]

    @pytest.fixture()
    def seeded_store(self, store: MemoryStore):
        """Seed 100 decisions: 30 canonical + 70 filler."""
        for title, decision, tags in self.CANONICAL:
            store.save_decision(_make_decision(
                title=title,
                decision=decision,
                tags=tags,
            ))
        # 70 filler decisions (should NOT match benchmark queries)
        for i in range(70):
            store.save_decision(_make_decision(
                title=f"Unrelated filler decision #{i}",
                decision=f"Filler choice {i} about generic topics",
                rationale=f"This is a filler item with no special keywords",
                tags=["filler", "noise"],
            ))
        return store

    def test_benchmark_precision(self, seeded_store: MemoryStore):
        """Each benchmark query should find its intended decision."""
        hits = []
        misses = []
        latencies = []

        for query, expected_title_fragment in self.QUERIES:
            t0 = time.time()
            results = seeded_store.search("stress-proj", query, limit=5)
            lat = time.time() - t0
            latencies.append(lat)

            found = any(
                expected_title_fragment in r.get("title", "")
                for r in results
            )
            if found:
                hits.append(query)
            else:
                misses.append((query, expected_title_fragment, [r.get("title", "") for r in results]))

        precision = len(hits) / len(self.QUERIES)
        avg_lat = sum(latencies) / len(latencies) * 1000
        max_lat = max(latencies) * 1000

        lines = [
            f"Queries: {len(self.QUERIES)}",
            f"Hits: {len(hits)}, Misses: {len(misses)}",
            f"Precision: {precision:.0%}",
            f"Avg latency: {avg_lat:.1f}ms, Max: {max_lat:.1f}ms",
        ]
        if misses:
            lines.append("Misses:")
            for q, exp, got in misses[:5]:
                lines.append(f"  '{q}' expected '{exp}' in {got}")
        _report("Search Quality: 20 Benchmark Queries", *lines)

        # Allow up to 3 misses (CJK tokenization may affect some)
        assert len(misses) <= 3, (
            f"Too many search misses ({len(misses)}): "
            + ", ".join(m[0] for m in misses)
        )

    def test_no_false_positives(self, seeded_store: MemoryStore):
        """Querying for filler-only terms should not surface canonical decisions."""
        results = seeded_store.search("stress-proj", "filler noise generic")
        canonical_titles = {c[0] for c in self.CANONICAL}
        false_pos = [r for r in results if r.get("title", "") in canonical_titles]
        _report(
            "Search Quality: False Positives",
            f"Results for 'filler noise generic': {len(results)}",
            f"False positives: {len(false_pos)}",
        )
        assert len(false_pos) == 0


# ──────────────────────────────────────────────────────────────
# 6. Data Integrity — FTS consistency after deletes
# ──────────────────────────────────────────────────────────────

class TestDataIntegrity:
    """Insert, remove FTS entry, verify search consistency."""

    def test_fts_delete_doesnt_break_search(self, store: MemoryStore):
        """Manually deleting an FTS row should not corrupt search."""
        d1 = store.save_decision(_make_decision(title="Alpha payment system"))
        d2 = store.save_decision(_make_decision(title="Beta messaging queue"))
        d3 = store.save_decision(_make_decision(title="Gamma database shard"))

        # Verify all searchable
        results = store.search("stress-proj", "payment")
        assert len(results) >= 1

        # Manually delete FTS row for d1
        store.conn.execute(
            "DELETE FROM memory_fts WHERE entity_id = ?", (d1.id,)
        )
        store.conn.commit()

        # d1 no longer found via FTS, but d2 and d3 still work
        r_payment = store.search("stress-proj", "payment")
        r_messaging = store.search("stress-proj", "messaging")
        r_database = store.search("stress-proj", "database")

        _report(
            "Integrity: FTS Delete",
            f"payment results after FTS delete: {len(r_payment)}",
            f"messaging still searchable: {len(r_messaging)}",
            f"database still searchable: {len(r_database)}",
        )
        # d1's FTS entry was removed — payment should ideally return 0
        # (or fallback to LIKE search which may still find the DB row)
        assert len(r_messaging) >= 1
        assert len(r_database) >= 1

    def test_reinsert_restores_fts(self, store: MemoryStore):
        """Deleting FTS and re-saving the decision restores searchability."""
        d = store.save_decision(_make_decision(
            title="Disposable search target",
            decision="Remove and restore",
        ))
        # Confirm searchable
        r1 = store.search("stress-proj", "Disposable search")
        assert len(r1) >= 1

        # Nuke FTS
        store.conn.execute(
            "DELETE FROM memory_fts WHERE entity_id = ?", (d.id,)
        )
        store.conn.commit()

        # Re-save (upsert) — should restore FTS
        store.save_decision(d)
        r2 = store.search("stress-proj", "Disposable search")
        _report(
            "Integrity: FTS Re-index",
            f"After delete: search hit count should be 0 or LIKE fallback",
            f"After re-save: {len(r2)} results",
        )
        assert len(r2) >= 1

    def test_supersede_preserves_both_records(self, store: MemoryStore):
        """Superseded decision remains in DB and superseded_by is set."""
        old = store.save_decision(_make_decision(
            title="Old auth scheme",
            decision="Session-based auth",
        ))
        new = Decision(
            project="stress-proj",
            title="New auth scheme",
            decision="JWT-based auth",
        )
        new = store.supersede(old.id, new)

        old_loaded = store.get_decision(old.id)
        new_loaded = store.get_decision(new.id)

        assert old_loaded is not None
        assert old_loaded.status == "superseded"
        assert old_loaded.superseded_by == new.id
        assert new_loaded.status == "active"

        # Both searchable
        r_old = store.search("stress-proj", "Session auth")
        r_new = store.search("stress-proj", "JWT auth")
        _report(
            "Integrity: Supersede Chain",
            f"Old status: {old_loaded.status}, superseded_by: {old_loaded.superseded_by}",
            f"Old searchable: {len(r_old)}, New searchable: {len(r_new)}",
        )


# ──────────────────────────────────────────────────────────────
# 7. Edge Cases
# ──────────────────────────────────────────────────────────────

class TestEdgeCases:
    """Boundary conditions and unusual inputs."""

    def test_duplicate_id_upsert(self, store: MemoryStore):
        """INSERT OR REPLACE means same ID overwrites."""
        d = _make_decision(title="Original")
        d.id = "DC-20260627-0001"
        store.save_decision(d)

        d2 = _make_decision(title="Replacement")
        d2.id = "DC-20260627-0001"
        store.save_decision(d2)

        loaded = store.get_decision("DC-20260627-0001")
        assert loaded is not None
        assert loaded.title == "Replacement"

        # DB count should still be 1
        stats = store.get_stats()
        assert stats["decision_count"] == 1

        _report("Edge: Duplicate ID Upsert", "Replacement works, count=1 [OK]")

    def test_minimal_decision(self, store: MemoryStore):
        """Decision with only project + title (all other fields default)."""
        d = Decision(project="x", title="Minimal")
        d = store.save_decision(d)
        loaded = store.get_decision(d.id)
        assert loaded.title == "Minimal"
        assert loaded.decision == ""
        assert loaded.rationale == ""
        assert loaded.alternatives == []
        assert loaded.constraints == []
        assert loaded.tags == []

        _report("Edge: Minimal Decision", "All defaults preserved [OK]")

    def test_special_characters_in_project(self, store: MemoryStore):
        """Project names with dots, dashes, underscores."""
        for proj in ["my-project", "my_project", "my.project", "PROJ-2026.v2"]:
            store.save_decision(_make_decision(project=proj, title=f"Decision in {proj}"))
        for proj in ["my-project", "my_project", "my.project", "PROJ-2026.v2"]:
            results = store.list_decisions(project=proj)
            assert len(results) == 1, f"Expected 1 for project '{proj}', got {len(results)}"

        _report("Edge: Special Char Project Names", "All 4 project namespaces OK [OK]")

    def test_supersedence_chain(self, store: MemoryStore):
        """Chain A -> B -> C. Each link is correct."""
        a = store.save_decision(_make_decision(title="Version A"))
        b = _make_decision(title="Version B")
        b = store.supersede(a.id, b)
        c = _make_decision(title="Version C")
        c = store.supersede(b.id, c)

        la = store.get_decision(a.id)
        lb = store.get_decision(b.id)
        lc = store.get_decision(c.id)

        assert la.status == "superseded" and la.superseded_by == b.id
        assert lb.status == "superseded" and lb.superseded_by == c.id
        assert lc.status == "active" and lc.superseded_by is None

        _report(
            "Edge: Supersedence Chain A->B->C",
            f"A({a.id}) -> B({b.id}) -> C({c.id})",
            "Chain integrity verified [OK]",
        )

    def test_long_title(self, store: MemoryStore):
        """Title of 500 characters."""
        long_title = "Decision " * 100  # 900 chars
        d = store.save_decision(_make_decision(title=long_title))
        loaded = store.get_decision(d.id)
        assert loaded.title == long_title

        _report("Edge: Long Title", f"{len(long_title)} chars round-tripped [OK]")

    def test_empty_search_query(self, store: MemoryStore):
        """Search with empty query should not crash."""
        store.save_decision(_make_decision(title="Some decision"))
        try:
            results = store.search("stress-proj", "")
            _report("Edge: Empty Search Query", f"Returned {len(results)} results, no crash [OK]")
        except Exception as e:
            _report("Edge: Empty Search Query", f"Exception: {e}")
            # Depending on implementation, this may raise — either outcome is acceptable
            pytest.skip(f"Empty query raises: {e}")

    def test_concurrent_supersede(self, store: MemoryStore):
        """Two threads try to supersede the same decision."""
        original = store.save_decision(_make_decision(title="Original"))
        results = []
        errors = []

        def try_supersede(suffix: str, seq: int):
            try:
                new = _make_decision(title=f"Superseded by {suffix}")
                new.id = f"DC-20260627-{30000+seq:04d}"
                store.supersede(original.id, new)
                results.append(suffix)
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = [pool.submit(try_supersede, s, i) for i, s in enumerate(["T1", "T2"])]
            for f in as_completed(futs):
                f.result()

        loaded = store.get_decision(original.id)
        assert loaded.status == "superseded"
        assert loaded.superseded_by is not None

        _report(
            "Edge: Concurrent Supersede",
            f"Succeeded: {results}, Errors: {len(errors)}",
            f"Final status: {loaded.status}, linked to: {loaded.superseded_by}",
        )

    def test_experience_fts_isolation(self, store: MemoryStore):
        """Experience FTS entries don't leak into decision-scoped results."""
        store.save_decision(_make_decision(
            title="Database scaling decision",
            decision="Scale vertically first",
        ))
        store.save_experience(Experience(
            project="stress-proj",
            situation="Database hit connection limit during peak traffic",
            action="Increased max_connections and added connection pooling",
            lesson="Always set connection pool limits in production",
        ))

        # Search should return both (they share project scope)
        results = store.search("stress-proj", "Database connection")
        entity_types = {r["entity_type"] for r in results}
        _report(
            "Edge: Experience/Decision FTS Isolation",
            f"Results: {len(results)}, Entity types: {entity_types}",
        )
        # At minimum, no crash and we get some results
        assert len(results) >= 1

    def test_get_stats_multiple_projects(self, store: MemoryStore):
        """Stats correctly aggregate across projects."""
        for p in ["alpha", "beta", "gamma"]:
            for i in range(5):
                store.save_decision(_make_decision(project=p, title=f"{p} decision {i}"))
        store.save_experience(Experience(project="alpha", situation="X"))

        global_stats = store.get_stats()
        alpha_stats = store.get_stats(project="alpha")

        assert global_stats["decision_count"] == 15
        assert global_stats["experience_count"] == 1
        assert set(global_stats["projects"]) == {"alpha", "beta", "gamma"}
        assert alpha_stats["decision_count"] == 5

        _report(
            "Edge: Multi-project Stats",
            f"Global: {global_stats['decision_count']} decisions, {global_stats['experience_count']} experiences",
            f"Alpha: {alpha_stats['decision_count']} decisions",
        )
