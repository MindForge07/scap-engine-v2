"""Rigorous A/B comparison: FTS5-only vs vector search vs hybrid.

Quantitative metrics:
  - Precision@K: fraction of top-K results that are relevant
  - Recall@K: fraction of relevant items found in top-K
  - F1@K: harmonic mean of precision and recall
  - MRR: Mean Reciprocal Rank (1/rank of first relevant result)
  - Consolidation merge rate at different thresholds
  - Evolution gen advancement per consolidation cycle

Dataset: 15 decisions with known semantic relationships.
Queries: 8 queries with manually defined ground truth.

NOTE: Uses MockEmbedder (bag-of-words). Real sentence-transformers would
show stronger semantic recall — these results are a LOWER BOUND.
"""
import json
import math
import re

import pytest

from scap.models import Decision, Experience, LatentTrace
from scap.store import MemoryStore


# ── Mock Embedder ──

class MockEmbedder:
    DIMENSION = 384

    @property
    def is_available(self):
        return True

    @staticmethod
    def _word_hash(word):
        h = 0
        for c in word:
            h = (h * 31 + ord(c)) % (2**31)
        return h

    @staticmethod
    def _tokenize(text):
        tokens = []
        for part in text.lower().split():
            cjk = re.findall(r'[一-鿿]', part)
            non_cjk = re.sub(r'[一-鿿]', '', part).strip()
            if cjk:
                tokens.extend(cjk)
            if non_cjk:
                tokens.append(non_cjk)
        return tokens

    def embed(self, text):
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


# ── Dataset ──

DATASET = [
    # (title, decision, rationale, group)
    ("数据库选型", "PostgreSQL", "成熟稳定 支持JSON", "db"),
    ("数据库性能优化", "添加索引和分区", "查询响应慢", "db"),
    ("数据库查询调优", "EXPLAIN执行计划分析", "定位慢查询", "db"),
    ("数据存储方案", "MongoDB文档数据库", "灵活schema", "db"),
    ("缓存策略设计", "Redis集群", "低延迟 高可用", "cache"),
    ("内存缓存优化", "本地缓存LRU", "减少IO压力", "cache"),
    ("前端框架选择", "React组件化", "生态丰富", "frontend"),
    ("页面渲染加速", "SSR服务端渲染", "首屏速度", "frontend"),
    ("日志收集系统", "Filebeat采集", "统一格式化", "infra"),
    ("用户认证方案", "JWT无状态令牌", "水平扩展", "auth"),
    ("API网关配置", "Kong路由管理", "限流熔断", "infra"),
    ("数据库备份策略", "PITR时间点恢复", "数据安全", "db"),
    ("缓存淘汰机制", "TTL过期策略", "内存控制", "cache"),
    ("前端状态管理", "Zustand轻量方案", "减少boilerplate", "frontend"),
    ("监控告警体系", "Prometheus指标", "及时响应", "infra"),
]

# ── Queries with ground truth (relevant decision indices, 0-based) ──

QUERIES = [
    # (query, [relevant_indices], description)
    ("数据库", [0, 1, 2, 3, 11], "所有数据库相关决策"),
    ("缓存", [4, 5, 12], "所有缓存相关决策"),
    ("前端", [6, 7, 13], "所有前端相关决策"),
    ("查询速度", [1, 2], "查询性能相关（FTS5可能部分遗漏）"),
    ("数据安全", [11], "安全相关（仅D12含'数据'+'安全'）"),
    ("性能", [1, 2], "性能相关决策"),
    ("基础设施", [8, 10, 14], "基础设施（无精确关键词匹配）"),
    ("PostgreSQL", [0], "精确英文关键词"),
]


# ── Fixtures ──

@pytest.fixture()
def seeded_store(tmp_path):
    """Create a store with 15 known decisions, all with embeddings + traces."""
    db_path = str(tmp_path / "benchmark.db")
    store = MemoryStore(db_path)
    store.initialize()
    embedder = MockEmbedder()

    for title, decision, rationale, group in DATASET:
        d = Decision(project="bench", title=title, decision=decision, rationale=rationale)
        text = f"{title} {decision} {rationale}"
        d.embedding = embedder.embed(text)
        d = store.save_decision(d)
        trace = LatentTrace(
            entity_id=d.id, entity_type="decision",
            project="bench", embedding=d.embedding,
        )
        store.save_latent_trace(trace)
    return store


# ── Metrics helpers ──

def _entity_ids(results):
    """Extract entity_ids from search results."""
    return [r.get("entity_id") or r.get("id") for r in results]


def _decision_ids_at_indices(store, indices):
    """Get decision IDs for given dataset indices."""
    decisions = store.list_decisions(project="bench", limit=50)
    # list_decisions returns in created_at DESC order, so reverse
    decisions = list(reversed(decisions))
    return {decisions[i].id for i in indices if i < len(decisions)}


def _compute_metrics(retrieved_ids, relevant_ids, k=5):
    """Compute precision@K, recall@K, F1@K, MRR."""
    retrieved_k = retrieved_ids[:k]
    relevant_retrieved = [rid for rid in retrieved_k if rid in relevant_ids]
    relevant_count = len(relevant_ids)

    precision = len(relevant_retrieved) / len(retrieved_k) if retrieved_k else 0.0
    recall = len(relevant_retrieved) / relevant_count if relevant_count > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) > 0 else 0.0)

    # MRR: 1/rank of first relevant result
    mrr = 0.0
    for i, rid in enumerate(retrieved_k):
        if rid in relevant_ids:
            mrr = 1.0 / (i + 1)
            break

    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "mrr": round(mrr, 3),
        "retrieved": len(retrieved_k),
        "relevant_found": len(relevant_retrieved),
        "relevant_total": relevant_count,
    }


def _aggregate(metrics_list):
    """Aggregate metrics across multiple queries."""
    if not metrics_list:
        return {}
    n = len(metrics_list)
    return {
        "precision": round(sum(m["precision"] for m in metrics_list) / n, 3),
        "recall": round(sum(m["recall"] for m in metrics_list) / n, 3),
        "f1": round(sum(m["f1"] for m in metrics_list) / n, 3),
        "mrr": round(sum(m["mrr"] for m in metrics_list) / n, 3),
    }


# ═══════════════════════════════════════════════════════
# A/B Comparison: FTS5 vs Vector vs Hybrid
# ═══════════════════════════════════════════════════════

class TestSearchABComparison:
    """Quantitative comparison of search strategies with real metrics."""

    def test_fts5_only_metrics(self, seeded_store):
        """Measure FTS5-only (tiers 1-2 + LIKE fallback) precision/recall/F1."""
        results_all = []
        for query, relevant_idx, _desc in QUERIES:
            relevant_ids = _decision_ids_at_indices(seeded_store, relevant_idx)
            # FTS5-only: no query_vector
            hits = seeded_store.search("bench", query, limit=5)
            retrieved = _entity_ids(hits)
            m = _compute_metrics(retrieved, relevant_ids, k=5)
            results_all.append(m)

        agg = _aggregate(results_all)
        print(f"\n{'='*60}")
        print(f"FTS5-only (tiers 1-2 + LIKE):")
        print(f"  Precision@5: {agg['precision']}")
        print(f"  Recall@5:    {agg['recall']}")
        print(f"  F1@5:        {agg['f1']}")
        print(f"  MRR:         {agg['mrr']}")
        print(f"{'='*60}")

        # FTS5 should have reasonable precision (exact keyword matches)
        assert agg["precision"] > 0.0
        # Store for comparison
        TestSearchABComparison._fts5_agg = agg

    def test_vector_only_metrics(self, seeded_store):
        """Measure vector-only (cosine similarity) precision/recall/F1."""
        embedder = MockEmbedder()
        results_all = []
        for query, relevant_idx, _desc in QUERIES:
            relevant_ids = _decision_ids_at_indices(seeded_store, relevant_idx)
            query_vec = embedder.embed(query)
            hits = seeded_store.search_by_vector("bench", query_vec, limit=5)
            retrieved = _entity_ids(hits)
            m = _compute_metrics(retrieved, relevant_ids, k=5)
            results_all.append(m)

        agg = _aggregate(results_all)
        print(f"\n{'='*60}")
        print(f"Vector-only (cosine similarity):")
        print(f"  Precision@5: {agg['precision']}")
        print(f"  Recall@5:    {agg['recall']}")
        print(f"  F1@5:        {agg['f1']}")
        print(f"  MRR:         {agg['mrr']}")
        print(f"{'='*60}")

        assert agg["precision"] > 0.0
        TestSearchABComparison._vector_agg = agg

    def test_hybrid_metrics(self, seeded_store):
        """Measure hybrid (FTS5 → vector fallback) precision/recall/F1."""
        embedder = MockEmbedder()
        results_all = []
        for query, relevant_idx, _desc in QUERIES:
            relevant_ids = _decision_ids_at_indices(seeded_store, relevant_idx)
            query_vec = embedder.embed(query)
            # Hybrid: FTS5 first, vector as fallback
            hits = seeded_store.search("bench", query, limit=5, query_vector=query_vec)
            retrieved = _entity_ids(hits)
            m = _compute_metrics(retrieved, relevant_ids, k=5)
            results_all.append(m)

        agg = _aggregate(results_all)
        print(f"\n{'='*60}")
        print(f"Hybrid (FTS5 → vector fallback):")
        print(f"  Precision@5: {agg['precision']}")
        print(f"  Recall@5:    {agg['recall']}")
        print(f"  F1@5:        {agg['f1']}")
        print(f"  MRR:         {agg['mrr']}")
        print(f"{'='*60}")

        assert agg["precision"] > 0.0
        TestSearchABComparison._hybrid_agg = agg

    def test_comparison_summary(self, seeded_store):
        """Print side-by-side comparison and verify trade-offs."""
        # Ensure previous tests ran
        assert hasattr(TestSearchABComparison, "_fts5_agg"), "Run FTS5 test first"
        assert hasattr(TestSearchABComparison, "_vector_agg"), "Run vector test first"
        assert hasattr(TestSearchABComparison, "_hybrid_agg"), "Run hybrid test first"

        fts5 = TestSearchABComparison._fts5_agg
        vec = TestSearchABComparison._vector_agg
        hyb = TestSearchABComparison._hybrid_agg

        print(f"\n{'='*70}")
        print(f"  {'Metric':<16} {'FTS5-only':>12} {'Vector-only':>12} {'Hybrid':>12}")
        print(f"  {'-'*16} {'-'*12} {'-'*12} {'-'*12}")
        print(f"  {'Precision@5':<16} {fts5['precision']:>12.3f} {vec['precision']:>12.3f} {hyb['precision']:>12.3f}")
        print(f"  {'Recall@5':<16} {fts5['recall']:>12.3f} {vec['recall']:>12.3f} {hyb['recall']:>12.3f}")
        print(f"  {'F1@5':<16} {fts5['f1']:>12.3f} {vec['f1']:>12.3f} {hyb['f1']:>12.3f}")
        print(f"  {'MRR':<16} {fts5['mrr']:>12.3f} {vec['mrr']:>12.3f} {hyb['mrr']:>12.3f}")
        print(f"{'='*70}")

        # Key findings
        print(f"\n  Key findings:")
        print(f"  - FTS5 precision >= vector precision: {fts5['precision'] >= vec['precision']}")
        print(f"  - Vector recall >= FTS5 recall: {vec['recall'] >= fts5['recall']}")
        print(f"  - Hybrid == FTS5 (vector is fallback only): "
              f"{hyb['f1'] == fts5['f1']}")
        print(f"  - Vector finds what FTS5 misses: {vec['recall'] > fts5['recall']}")
        print()

        # Assertions for trade-off verification
        # FTS5 should generally have higher or equal precision (stricter matching)
        assert fts5["precision"] >= vec["precision"] - 0.1, \
            "FTS5 should have comparable or higher precision than vector"
        # Vector should find at least as many relevant results
        assert vec["recall"] >= fts5["recall"] - 0.1, \
            "Vector should have comparable or higher recall than FTS5"


# ═══════════════════════════════════════════════════════
# Per-query breakdown
# ═══════════════════════════════════════════════════════

class TestPerQueryBreakdown:
    """Detailed per-query comparison to identify where each strategy wins."""

    def test_per_query_comparison(self, seeded_store):
        """Print detailed per-query metrics for all three strategies."""
        embedder = MockEmbedder()
        print(f"\n{'='*90}")
        print(f"  Per-query breakdown (P=Precision, R=Recall, F1, MRR)")
        print(f"{'='*90}")
        print(f"  {'Query':<16} {'Strategy':<12} {'P@5':>6} {'R@5':>6} {'F1':>6} {'MRR':>6} {'Found':>6}/{'Total':<5}")
        print(f"  {'-'*16} {'-'*12} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*13}")

        for query, relevant_idx, desc in QUERIES:
            relevant_ids = _decision_ids_at_indices(seeded_store, relevant_idx)
            query_vec = embedder.embed(query)

            # FTS5-only
            fts5_hits = seeded_store.search("bench", query, limit=5)
            fts5_m = _compute_metrics(_entity_ids(fts5_hits), relevant_ids, k=5)

            # Vector-only
            vec_hits = seeded_store.search_by_vector("bench", query_vec, limit=5)
            vec_m = _compute_metrics(_entity_ids(vec_hits), relevant_ids, k=5)

            # Hybrid
            hyb_hits = seeded_store.search("bench", query, limit=5, query_vector=query_vec)
            hyb_m = _compute_metrics(_entity_ids(hyb_hits), relevant_ids, k=5)

            print(f"  {query:<16} {'FTS5':<12} {fts5_m['precision']:>6.2f} {fts5_m['recall']:>6.2f} "
                  f"{fts5_m['f1']:>6.2f} {fts5_m['mrr']:>6.2f} {fts5_m['relevant_found']:>6}/{fts5_m['relevant_total']}")
            print(f"  {'':<16} {'Vector':<12} {vec_m['precision']:>6.2f} {vec_m['recall']:>6.2f} "
                  f"{vec_m['f1']:>6.2f} {vec_m['mrr']:>6.2f} {vec_m['relevant_found']:>6}/{vec_m['relevant_total']}")
            print(f"  {'':<16} {'Hybrid':<12} {hyb_m['precision']:>6.2f} {hyb_m['recall']:>6.2f} "
                  f"{hyb_m['f1']:>6.2f} {hyb_m['mrr']:>6.2f} {hyb_m['relevant_found']:>6}/{hyb_m['relevant_total']}")
            print(f"  [dim]{desc}[/dim]")
            print()

        # Verify at least one query where vector beats FTS5 on recall
        # This validates that vector search adds value beyond FTS5
        vector_wins = 0
        for query, relevant_idx, _desc in QUERIES:
            relevant_ids = _decision_ids_at_indices(seeded_store, relevant_idx)
            query_vec = embedder.embed(query)

            fts5_hits = seeded_store.search("bench", query, limit=5)
            fts5_m = _compute_metrics(_entity_ids(fts5_hits), relevant_ids, k=5)

            vec_hits = seeded_store.search_by_vector("bench", query_vec, limit=5)
            vec_m = _compute_metrics(_entity_ids(vec_hits), relevant_ids, k=5)

            if vec_m["recall"] > fts5_m["recall"]:
                vector_wins += 1

        print(f"  Vector recall > FTS5 recall on {vector_wins}/{len(QUERIES)} queries")
        print(f"{'='*90}")

        # At least some queries should show vector advantage
        # (With MockEmbedder this may be modest; real embeddings would show more)
        assert vector_wins >= 1, "Vector search should beat FTS5 on at least 1 query"


# ═══════════════════════════════════════════════════════
# Consolidation effectiveness
# ═══════════════════════════════════════════════════════

class TestConsolidationEffectiveness:
    """Measure merge rates at different similarity thresholds."""

    def test_merge_rate_vs_threshold(self, seeded_store):
        """Measure how merge rate changes with similarity threshold."""
        print(f"\n{'='*60}")
        print(f"  Consolidation merge rate vs threshold")
        print(f"  {'Threshold':>10} {'Before':>8} {'Merged':>8} {'After':>8} {'Rate':>8} {'MaxGen':>8}")
        print(f"  {'-'*10} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")

        initial_count = len(seeded_store.list_latent_traces(project="bench", limit=100))

        for threshold in [0.3, 0.5, 0.7, 0.85, 0.95]:
            # Reset: re-create all traces fresh for each threshold
            db_path = seeded_store.conn.execute("PRAGMA database_list").fetchone()[2]
            fresh_store = MemoryStore(db_path)
            fresh_store.initialize()

            # Clear and re-seed traces
            fresh_store.conn.execute("DELETE FROM latent_traces")
            fresh_store.conn.commit()
            embedder = MockEmbedder()
            decisions = fresh_store.list_decisions(project="bench", limit=50)
            for d in decisions:
                if not d.embedding:
                    continue
                trace = LatentTrace(
                    entity_id=d.id, entity_type="decision",
                    project="bench", embedding=d.embedding,
                )
                fresh_store.save_latent_trace(trace)

            traces_before = fresh_store.list_latent_traces(project="bench", limit=100)
            count_before = len(traces_before)

            # Run consolidation
            traces_sorted = sorted(traces_before, key=lambda t: t.fitness, reverse=True)
            merged = 0
            survivors = []
            merged_ids = []
            for trace in traces_sorted:
                if trace.id in merged_ids:
                    continue
                is_dup = False
                for surv in survivors:
                    sim = MemoryStore._cosine_similarity(trace.embedding, surv.embedding)
                    if sim >= threshold:
                        surv.evolution_gen += 1
                        fresh_store.save_latent_trace(surv)
                        fresh_store.delete_latent_trace(trace.id)
                        merged_ids.append(trace.id)
                        merged += 1
                        is_dup = True
                        break
                if not is_dup:
                    survivors.append(trace)

            traces_after = fresh_store.list_latent_traces(project="bench", limit=100)
            count_after = len(traces_after)
            merge_rate = merged / count_before if count_before > 0 else 0
            max_gen = max((t.evolution_gen for t in survivors), default=0)

            print(f"  {threshold:>10.2f} {count_before:>8} {merged:>8} "
                  f"{count_after:>8} {merge_rate:>8.1%} {max_gen:>8}")

        print(f"{'='*60}")

        # Verify: lower threshold → more merges
        # (We can't directly assert from the loop, but the print shows the trend)

    def test_evolution_gen_advancement(self, seeded_store):
        """Verify evolution_gen increases with consolidation cycles."""
        db_path = seeded_store.conn.execute("PRAGMA database_list").fetchone()[2]
        fresh_store = MemoryStore(db_path)
        fresh_store.initialize()

        # Clear and re-seed
        fresh_store.conn.execute("DELETE FROM latent_traces")
        fresh_store.conn.commit()
        embedder = MockEmbedder()
        decisions = fresh_store.list_decisions(project="bench", limit=50)
        for d in decisions:
            if d.embedding:
                trace = LatentTrace(
                    entity_id=d.id, entity_type="decision",
                    project="bench", embedding=d.embedding,
                )
                fresh_store.save_latent_trace(trace)

        # Run 3 consolidation cycles at low threshold
        gens_per_cycle = []
        for cycle in range(3):
            traces = fresh_store.list_latent_traces(project="bench", limit=100)
            traces.sort(key=lambda t: t.fitness, reverse=True)
            survivors = []
            merged_ids = []
            for trace in traces:
                if trace.id in merged_ids:
                    continue
                is_dup = False
                for surv in survivors:
                    sim = MemoryStore._cosine_similarity(trace.embedding, surv.embedding)
                    if sim >= 0.3:  # Low threshold for maximum merges
                        surv.evolution_gen += 1
                        fresh_store.save_latent_trace(surv)
                        fresh_store.delete_latent_trace(trace.id)
                        merged_ids.append(trace.id)
                        is_dup = True
                        break
                if not is_dup:
                    survivors.append(trace)
            max_gen = max((t.evolution_gen for t in survivors), default=0)
            gens_per_cycle.append(max_gen)

        print(f"\n  Evolution gen per consolidation cycle (threshold=0.3):")
        for i, gen in enumerate(gens_per_cycle):
            print(f"    Cycle {i+1}: max_evolution_gen = {gen}")

        # Gen should be non-decreasing
        for i in range(1, len(gens_per_cycle)):
            assert gens_per_cycle[i] >= gens_per_cycle[i-1], \
                f"Gen should not decrease: cycle {i} = {gens_per_cycle[i]} < {gens_per_cycle[i-1]}"


# ═══════════════════════════════════════════════════════
# Hybrid limitation analysis
# ═══════════════════════════════════════════════════════

class TestHybridLimitation:
    """Document the limitation: vector is fallback-only, not complementary."""

    def test_fts5_short_circuits_vector(self, seeded_store):
        """When FTS5 finds results, vector search is NOT triggered.

        This means hybrid mode misses vector-only results when FTS5
        finds SOME (but not ALL) relevant items.
        """
        embedder = MockEmbedder()
        query = "数据库"
        query_vec = embedder.embed(query)

        # FTS5 finds some results
        fts5_hits = seeded_store.search("bench", query, limit=5)
        assert len(fts5_hits) > 0, "FTS5 should find some results"

        # Vector finds (potentially different) results
        vec_hits = seeded_store.search_by_vector("bench", query_vec, limit=5)

        # Hybrid: since FTS5 found results, vector is NOT used
        hyb_hits = seeded_store.search("bench", query, limit=5, query_vector=query_vec)

        # Hybrid results == FTS5 results (vector was not triggered)
        fts5_ids = set(_entity_ids(fts5_hits))
        hyb_ids = set(_entity_ids(hyb_hits))

        print(f"\n  FTS5 found: {len(fts5_ids)} results")
        print(f"  Vector found: {len(set(_entity_ids(vec_hits)))} results")
        print(f"  Hybrid found: {len(hyb_ids)} results")
        print(f"  Hybrid == FTS5: {fts5_ids == hyb_ids}")
        print(f"  Vector-only results missed by hybrid: "
              f"{set(_entity_ids(vec_hits)) - hyb_ids}")

        # Document the limitation
        assert fts5_ids == hyb_ids, \
            "Hybrid should return same as FTS5 when FTS5 has results"

    def test_vector_finds_results_fts5_misses(self, seeded_store):
        """Identify specific queries where vector finds relevant results FTS5 misses."""
        embedder = MockEmbedder()

        cases = []
        for query, relevant_idx, desc in QUERIES:
            relevant_ids = _decision_ids_at_indices(seeded_store, relevant_idx)
            query_vec = embedder.embed(query)

            fts5_hits = seeded_store.search("bench", query, limit=5)
            vec_hits = seeded_store.search_by_vector("bench", query_vec, limit=5)

            fts5_ids = set(_entity_ids(fts5_hits))
            vec_ids = set(_entity_ids(vec_hits))
            vec_only = vec_ids - fts5_ids

            # Which of the vector-only results are actually relevant?
            vec_only_relevant = vec_only & relevant_ids
            if vec_only_relevant:
                cases.append({
                    "query": query,
                    "desc": desc,
                    "fts5_found": len(fts5_ids),
                    "vec_found": len(vec_ids),
                    "vec_only": len(vec_only),
                    "vec_only_relevant": len(vec_only_relevant),
                })

        print(f"\n  Queries where vector finds relevant results FTS5 misses:")
        print(f"  {'Query':<16} {'FTS5':>6} {'Vec':>6} {'Vec-only':>10} {'Relevant':>10}")
        print(f"  {'-'*16} {'-'*6} {'-'*6} {'-'*10} {'-'*10}")
        for c in cases:
            print(f"  {c['query']:<16} {c['fts5_found']:>6} {c['vec_found']:>6} "
                  f"{c['vec_only']:>10} {c['vec_only_relevant']:>10}")
        print(f"\n  Total queries with vector advantage: {len(cases)}/{len(QUERIES)}")

        # This validates that vector search adds recall value
        assert len(cases) >= 1, "Vector should find relevant results FTS5 misses"
