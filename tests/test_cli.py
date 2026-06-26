"""Tests for CLI commands using click.testing.CliRunner."""
import json
import os
import pytest
from click.testing import CliRunner

from scap.cli import cli


@pytest.fixture()
def runner(tmp_path):
    db_path = str(tmp_path / "cli_test.db")
    r = CliRunner()
    return r, ["--db", db_path]


class TestInit:
    def test_init_creates_project(self, runner):
        r, base_args = runner
        result = r.invoke(cli, base_args + ["init", "--project", "acme", "--stack", "PostgreSQL", "--stack", "Redis"])
        assert result.exit_code == 0
        assert "acme" in result.output

    def test_init_without_stack(self, runner):
        r, base_args = runner
        result = r.invoke(cli, base_args + ["init", "--project", "acme"])
        assert result.exit_code == 0


class TestStatus:
    def test_status_empty(self, runner):
        r, base_args = runner
        result = r.invoke(cli, base_args + ["status"])
        assert result.exit_code == 0
        assert "0" in result.output  # 0 decisions


class TestSearch:
    def test_search_empty(self, runner):
        r, base_args = runner
        result = r.invoke(cli, base_args + ["search", "test"])
        assert result.exit_code == 0
        assert "No results" in result.output


class TestList:
    def test_list_empty(self, runner):
        r, base_args = runner
        result = r.invoke(cli, base_args + ["list"])
        assert result.exit_code == 0
        assert "No decisions" in result.output


class TestExport:
    def test_export_creates_file(self, runner, tmp_path):
        r, base_args = runner
        # First init and ingest
        r.invoke(cli, base_args + ["init", "--project", "acme"])
        output_path = str(tmp_path / "export.md")
        result = r.invoke(cli, base_args + ["export", "--project", "acme", "--output", output_path])
        assert result.exit_code == 0
        assert os.path.exists(output_path)
        with open(output_path, encoding="utf-8") as f:
            content = f.read()
        assert "acme" in content


class TestConfigure:
    def test_configure_updates_context(self, runner):
        r, base_args = runner
        result = r.invoke(cli, base_args + [
            "configure", "--project", "acme",
            "--stack", "Kafka", "--convention", "事件溯源"
        ])
        assert result.exit_code == 0
        assert "updated" in result.output.lower()
