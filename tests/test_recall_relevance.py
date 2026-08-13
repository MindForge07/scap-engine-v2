"""P0 regression tests: recall relevance scoring + export budget.

Covers the failures demonstrated in the README-verification experiments:
  1. Chinese natural-language tasks must rank the true match first
  2. Single-letter Latin words must not create false-positive relevance
  3. Small projects must NOT inject unrelated decisions unconditionally
  4. Projects with records must not be reported as "no records"
  5. export_context() must honor a max_chars budget and fold duplicates
"""
import pytest

from scap.models import Decision
from scap.store import MemoryStore
from scap.mcp_server import _format_recall, _task_match_terms, _decision_relevance


def seed(store: MemoryStore, project: str, rows: list[tuple[str, str, str]]) -> None:
    for title, decision, rationale in rows:
        store.save_decision(Decision(
            project=project, title=title, decision=decision, rationale=rationale,
        ))


# ── Term extraction ──

class TestTaskMatchTerms:
    def test_chinese_becomes_bigrams(self):
        terms = _task_match_terms("消息队列")
        assert "消息" in terms
        assert "息队" in terms
        assert "队列" in terms

    def test_single_latin_characters_dropped(self):
        terms = _task_match_terms("choose a message queue")
        assert "a" not in terms
        assert "message" in terms

    def test_mixed_cjk_and_latin(self):
        terms = _task_match_terms("数据库 PostgreSQL 优化")
        assert "数据" in terms
        assert "postgresql" in terms

    def test_empty_text_yields_no_terms(self):
        assert _task_match_terms("") == []
        assert _task_match_terms("a") == []


# ── Relevance scoring ──

class TestDecisionRelevance:
    def test_chinese_long_sentence_ranks_true_match_first(self, store: MemoryStore):
        kafka = _decision_relevance(
            "我想评估一下用哪个消息队列更好",
            "消息队列选型 Kafka 高吞吐量，满足 50k msg/s", None, None,
        )
        pg = _decision_relevance(
            "我想评估一下用哪个消息队列更好",
            "数据库选型 PostgreSQL 需要 JSONB 与事务一致性", None, None,
        )
        assert kafka > 0
        assert kafka > pg

    def test_no_single_char_false_positive(self, store: MemoryStore):
        # 'a' must not create a false positive inside 'kafka'/'react'.
        react = _decision_relevance(
            "choose a message queue",
            "前端框架选择 React 生态丰富，团队熟悉", None, None,
        )
        assert react == 0
        # A real English keyword overlap still ranks as relevant.
        kafka = _decision_relevance(
            "choose a message queue",
            "Message Queue 选型 Kafka 高吞吐量，满足 50k msg/s", None, None,
        )
        assert kafka > 0

    def test_incidental_single_bigram_below_floor(self, store: MemoryStore):
        # One shared common bigram ("系统") out of a long task is incidental,
        # not relevance, and must not survive the floor.
        score = _decision_relevance(
            "我需要为支付系统选择消息队列",
            "日志系统 ELK 集中式日志与检索", None, None,
        )
        assert score == 0

    def test_vector_blend_counts_semantic_match(self, store: MemoryStore):
        # Lexical terms absent, but query/embedding vectors point the same way.
        score = _decision_relevance(
            "数据库性能", "前端框架 React 组件化",
            [1.0, 0.0], [0.9, 0.1],
        )
        assert score > 0

    def test_idf_scaling_does_not_re_filter_true_match(self, store: MemoryStore):
        # 3/14 hits passes the count-ratio floor; the idf-weighted fraction
        # lands below 0.15, and the floor must not re-filter it.
        from scap.mcp_server import _task_match_terms, _term_idf
        task = "我想评估一下用哪个消息队列更好"
        corpus = [
            "消息队列选型 Kafka 高吞吐量，满足 50k msg/s",
            "数据库选型 PostgreSQL 需要 JSONB 与事务一致性",
        ]
        idf = _term_idf(_task_match_terms(task), corpus)
        score = _decision_relevance(task, corpus[0], None, None, idf=idf)
        assert score > 0


# ── _format_recall output ──

class TestFormatRecall:
    def test_chinese_task_surfaces_relevant_decision(self, store: MemoryStore):
        seed(store, "demo", [
            ("消息队列选型", "Kafka", "高吞吐量"),
            ("数据库选型", "PostgreSQL", "JSONB"),
        ])
        out = _format_recall(store, "demo", "我需要为支付系统选择消息队列")
        assert "消息队列选型" in out
        assert "Kafka" in out

    def test_small_project_unrelated_task_injects_nothing(self, store: MemoryStore):
        seed(store, "mini", [
            ("UI 主题色", "深色模式", "用户偏好"),
            ("部署平台", "Vercel", "部署简单"),
        ])
        out = _format_recall(store, "mini", "帮我写一首关于秋天的诗")
        assert "相关历史决策" not in out
        assert "不相关" in out

    def test_records_exist_but_none_relevant_is_not_empty(self, store: MemoryStore):
        seed(store, "demo", [("数据库选型", "PostgreSQL", "JSONB")])
        out = _format_recall(store, "demo", "帮我写一首关于秋天的诗")
        assert "暂无相关记录" not in out
        assert "条决策" in out

    def test_empty_project_keeps_first_record_message(self, store: MemoryStore):
        out = _format_recall(store, "brand-new", "任意任务")
        assert "暂无相关记录" in out

    def test_unrelated_decision_not_injected(self, store: MemoryStore):
        seed(store, "demo", [
            ("前端框架选择", "React", "生态丰富"),
            ("消息队列选型", "Kafka", "高吞吐量"),
            ("缓存方案", "Redis", "低延迟"),
        ])
        out = _format_recall(store, "demo", "消息队列选型用哪个好")
        assert "消息队列选型" in out
        # Unrelated decisions are filtered out, not injected below the task.
        assert "前端框架选择" not in out

    def test_context_section_survives_without_relevant_decisions(self, store: MemoryStore):
        from scap.models import ProjectContext
        store.update_project_context(ProjectContext(
            project="acme", tech_stack=["PostgreSQL", "Redis"],
        ))
        out = _format_recall(store, "acme", "写一首关于秋天的诗")
        assert "PostgreSQL" in out
        assert "相关历史决策" not in out


# ── export_context budget + folding ──

class TestExportBudget:
    def test_max_chars_truncates_but_keeps_header(self, store: MemoryStore, tmp_path):
        for i in range(10):
            store.save_decision(Decision(
                project="big", title=f"决策{i}", decision=f"方案{i}",
                rationale="r" * 200,
            ))
        out = str(tmp_path / "big.md")
        store.export_context("big", out, max_chars=2_000)
        content = open(out, encoding="utf-8").read()
        assert content.startswith("# Project Memory: big")
        # Emission stops at the budget: strictly fewer than all 10 blocks.
        assert content.count("### 决策") < 10

    def test_no_budget_emits_everything(self, store: MemoryStore, tmp_path):
        for i in range(10):
            store.save_decision(Decision(
                project="big", title=f"决策{i}", decision=f"方案{i}",
                rationale="r" * 50,
            ))
        out = str(tmp_path / "big2.md")
        store.export_context("big", out)
        content = open(out, encoding="utf-8").read()
        assert content.count("### 决策") == 10

    def test_exact_duplicate_decisions_folded(self, store: MemoryStore, tmp_path):
        store.save_decision(Decision(project="p", title="选型", decision="A", rationale="r1"))
        store.save_decision(Decision(project="p", title="选型", decision="A", rationale="r2"))
        out = str(tmp_path / "p.md")
        store.export_context("p", out)
        content = open(out, encoding="utf-8").read()
        assert content.count("### 选型") == 1
