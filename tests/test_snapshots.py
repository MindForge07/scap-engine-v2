"""Golden snapshot tests: lock export_context output format (md + json).

Any change to the injected context format must update these expectations —
this is the format contract consumed by dsh/scap-injection and by humans.
"""
import json
from datetime import datetime, timezone

from scap.models import Decision, Experience, ProjectContext
from scap.store import MemoryStore


def _seed(store: MemoryStore) -> None:
    store.update_project_context(ProjectContext(
        project="acme",
        tech_stack=["PostgreSQL 15", "Redis 7"],
        conventions=["所有状态变更必须走事件溯源"],
        insights=["事件溯源是默认模式"],
    ))
    store.save_decision(Decision(
        project="acme", title="消息队列选型", decision="Kafka",
        rationale="吞吐量需求 50k msg/s",
        alternatives=[{"name": "RabbitMQ", "reason_rejected": "性能瓶颈"}],
        constraints=["必须支持 15 种货币"],
        importance=5,
        created_at=datetime(2026, 6, 26, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 26, tzinfo=timezone.utc),
    ))
    store.save_experience(Experience(
        project="acme", situation="CPU 飙到 90%", action="加了索引",
        lesson="查询必须加索引防 N+1", importance=4,
        created_at=datetime(2026, 6, 26, tzinfo=timezone.utc),
    ))


class TestMarkdownGolden:
    def test_export_markdown_format(self, store: MemoryStore, tmp_path):
        _seed(store)
        out = str(tmp_path / "acme.md")
        store.export_context("acme", out)
        content = open(out, encoding="utf-8").read()
        assert content == (
            "# Project Memory: acme\n"
            "\n"
            "## Tech Stack\n"
            "PostgreSQL 15, Redis 7\n"
            "\n"
            "## Conventions\n"
            "- 所有状态变更必须走事件溯源\n"
            "\n"
            "## Insights\n"
            "- 事件溯源是默认模式\n"
            "\n"
            "## Decisions\n"
            "\n"
            "### 消息队列选型 (2026-06-26)\n"
            "**Chosen:** Kafka\n"
            "**Why:** 吞吐量需求 50k msg/s\n"
            "- ~~RabbitMQ~~ (rejected: 性能瓶颈)\n"
            "**Constraints:** 必须支持 15 种货币\n"
            "\n"
            "## Lessons Learned\n"
            "\n"
            "- **CPU 飙到 90%**\n"
            "  Action: 加了索引\n"
            "  → 查询必须加索引防 N+1\n"
        )


class TestJsonGolden:
    def test_export_json_format(self, store: MemoryStore, tmp_path):
        _seed(store)
        out = str(tmp_path / "acme.md")
        store.export_context("acme", out)
        payload = json.load(open(str(tmp_path / "acme.json"), encoding="utf-8"))
        assert payload["format_version"] == 1
        assert list(payload.keys()) == [
            "format_version", "project", "exported_at", "context", "decisions", "experiences",
        ]
        assert payload["project"] == "acme"
        assert payload["context"] == {
            "tech_stack": ["PostgreSQL 15", "Redis 7"],
            "conventions": ["所有状态变更必须走事件溯源"],
            "active_goals": [],
            "insights": ["事件溯源是默认模式"],
        }
        assert len(payload["decisions"]) == 1
        d = payload["decisions"][0]
        assert list(d.keys()) == [
            "id", "title", "decision", "rationale", "status",
            "importance", "created_at", "updated_at",
        ]
        assert d["importance"] == 5
        assert d["status"] == "active"
        assert len(payload["experiences"]) == 1
        e = payload["experiences"][0]
        assert list(e.keys()) == [
            "id", "situation", "action", "lesson", "importance", "created_at",
        ]
