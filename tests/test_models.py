"""Tests for Pydantic models."""
import pytest
from datetime import datetime, timezone
from scap.models import Decision, ProjectContext, Experience


class TestDecision:
    def test_create_minimal(self):
        d = Decision(project="test", title="My decision")
        assert d.project == "test"
        assert d.title == "My decision"
        assert d.status == "active"
        assert d.id == ""  # auto-generated later by store

    def test_create_full(self):
        d = Decision(
            project="acme",
            title="支付网关选型",
            context="需要支持全球支付",
            decision="Stripe + 自研",
            rationale="Stripe覆盖主要市场",
            alternatives=[{"name": "Adyen", "reason_rejected": "合约太长"}],
            constraints=["支持15种货币"],
            tags=["支付"],
        )
        assert d.alternatives[0]["name"] == "Adyen"
        assert "支付" in d.tags

    def test_validate_id_valid(self):
        d = Decision(id="DC-20260101-0001", project="x", title="t")
        assert d.id == "DC-20260101-0001"

    def test_validate_id_invalid(self):
        with pytest.raises(ValueError, match="DC-YYYYMMDD-NNNN"):
            Decision(id="BAD-FORMAT", project="x", title="t")

    def test_validate_id_empty_ok(self):
        d = Decision(id="", project="x", title="t")
        assert d.id == ""

    def test_status_enum(self):
        d = Decision(project="x", title="t", status="superseded")
        assert d.status == "superseded"

    def test_project_required(self):
        with pytest.raises(Exception):
            Decision(project="", title="t")


class TestProjectContext:
    def test_create(self):
        ctx = ProjectContext(
            project="acme",
            tech_stack=["PostgreSQL 15", "Redis 7"],
            conventions=["所有状态变更必须走事件溯源"],
        )
        assert len(ctx.tech_stack) == 2
        assert ctx.updated_at is not None


class TestExperience:
    def test_create(self):
        e = Experience(
            project="acme",
            situation="CPU 100%",
            action="加了ReadOnly",
            lesson="JPA查询必须加@EntityGraph",
            tags=["性能"],
        )
        assert e.lesson.startswith("JPA")

    def test_validate_id(self):
        e = Experience(id="EX-20260101-0001", project="x")
        assert e.id == "EX-20260101-0001"

    def test_validate_id_bad(self):
        with pytest.raises(ValueError, match="EX-YYYYMMDD-NNNN"):
            Experience(id="WRONG", project="x")
