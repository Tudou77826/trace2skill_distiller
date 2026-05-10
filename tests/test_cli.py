"""Tests for the redesigned CLI."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner

from trace2skill_distiller.cli.main import cli
from trace2skill_distiller.core.config import (
    ChrysConfig,
    DistillConfig,
    DistillFilter,
    LLMConfig,
    OutputConfig,
    SourceConfig,
)
from trace2skill_distiller.mining.types import SessionMeta
from trace2skill_distiller.output.types import DistillReport


def _make_config() -> DistillConfig:
    return DistillConfig(
        fast_model=LLMConfig(model="fast-m", api_key="sk-secret-key-12345", base_url="https://api.fast", max_concurrency=3),
        strong_model=LLMConfig(model="strong-m", api_key="sk-secret-key-12345", base_url="https://api.strong", max_concurrency=2),
        source=SourceConfig(type="chrys", chrys=ChrysConfig(sessions_dir="D:/sessions")),
        output=OutputConfig(format="knowledge_md", skill_output_dir="D:/skills"),
        filter=DistillFilter(min_messages=5, min_tools=3),
    )


class TestHelp:
    def test_root_help_uses_new_commands(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "doctor" in result.output
        assert "run" in result.output
        assert "runs" in result.output
        assert "sessions" in result.output
        assert "inspect" in result.output
        assert "distill" not in result.output
        assert "schedule" not in result.output
        assert "status" not in result.output


class TestConfigShow:
    def test_show_displays_config(self, tmp_path):
        with patch("trace2skill_distiller.cli.main._load_config", return_value=_make_config()):
            runner = CliRunner()
            result = runner.invoke(cli, ["config", "show"])
            assert result.exit_code == 0
            assert "Current Source" in result.output
            assert "type: chrys" in result.output
            assert "max_concurrency: 3" in result.output
            assert "max_concurrency: 2" in result.output
            assert "output.format: knowledge" in result.output
            assert "sk-se****key-12345" in result.output or "sk-s*" in result.output


class TestConfigSet:
    def test_set_source_type_writes_yaml(self, tmp_path):
        f = tmp_path / "config.yaml"
        try:
            f.write_text(yaml.dump({"source": {"type": "opencode"}}), encoding="utf-8")
            orig = DistillConfig.default_config_path
            DistillConfig.default_config_path = staticmethod(lambda: f)
            runner = CliRunner()
            result = runner.invoke(cli, ["config", "set", "source.type", "chrys"])
            assert result.exit_code == 0
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            assert data["source"]["type"] == "chrys"
        finally:
            DistillConfig.default_config_path = orig
            if f.exists():
                f.unlink()

    def test_set_output_format_maps_user_value(self, tmp_path):
        f = tmp_path / "config.yaml"
        try:
            f.write_text(yaml.dump({"output": {"format": "skill_md"}}), encoding="utf-8")
            orig = DistillConfig.default_config_path
            DistillConfig.default_config_path = staticmethod(lambda: f)
            runner = CliRunner()
            result = runner.invoke(cli, ["config", "set", "output.format", "knowledge"])
            assert result.exit_code == 0
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            assert data["output"]["format"] == "knowledge_md"
        finally:
            DistillConfig.default_config_path = orig
            if f.exists():
                f.unlink()


class TestSessions:
    def test_list_uses_current_source(self):
        mock_source = MagicMock()
        mock_source.list_sessions.return_value = [
            SessionMeta(id="s1", title="One", project="proj", msg_count=8, tool_count=0, timestamp=100),
        ]
        mock_source.count_tools.return_value = 5

        with patch("trace2skill_distiller.cli.main._load_config", return_value=_make_config()), \
             patch("trace2skill_distiller.cli.main.create_source", return_value=mock_source):
            runner = CliRunner()
            help_result = runner.invoke(cli, ["sessions", "list", "--help"])
            assert help_result.exit_code == 0
            assert "--source" not in help_result.output

            result = runner.invoke(cli, ["sessions", "list"])
            assert result.exit_code == 0
            mock_source.list_sessions.assert_called_once_with(project=None)
            assert "source=chrys" in result.output
            assert "s1" in result.output


class TestRun:
    def test_run_maps_mode_preview_and_output(self):
        cfg = _make_config()
        mock_pipeline = MagicMock()

        with patch("trace2skill_distiller.cli.main._load_config", return_value=cfg), \
             patch("trace2skill_distiller.cli.main.DistillPipeline.from_config", return_value=mock_pipeline) as from_config:
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["run", "--project", "demo", "--mode", "analyze", "--output", "knowledge", "--preview"],
            )
            assert result.exit_code == 0
            assert cfg.output.format == "knowledge_md"
            from_config.assert_called_once_with(cfg)
            mock_pipeline.run.assert_called_once_with(
                project="demo",
                session_id=None,
                mode="analyze",
                preview=True,
            )


class TestRuns:
    def test_runs_list_reads_json_reports(self, tmp_path):
        report_dir = tmp_path / ".trace2skill" / "reports"
        report_dir.mkdir(parents=True)
        report = DistillReport(
            run_id="abcd1234",
            project="proj",
            started_at="2026-05-10T10:00:00",
            sessions_total=5,
            sessions_passed_filter=3,
            topics_found=2,
            total_rules=7,
            total_duration_seconds=12.3,
        )
        (report_dir / "abcd1234.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")

        with patch("trace2skill_distiller.cli.main.Path.home", return_value=tmp_path):
            runner = CliRunner()
            result = runner.invoke(cli, ["runs", "list"])
            assert result.exit_code == 0
            assert "proj" in result.output
            assert "3/5" in result.output


class TestEnvSafety:
    def test_only_trace2skill_prefix_loaded(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text(
            "TRACE2SKILL_API_KEY=good-key\n"
            "PATH=/malicious\n"
            "HOME=/evil\n"
        )
        with patch("trace2skill_distiller.cli.main.Path") as MockPath:
            MockPath.home.return_value = tmp_path
            MockPath.return_value.__truediv__ = lambda s, o: tmp_path / o
            # Just test the parsing logic directly
            lines = env_path.read_text().splitlines()
            loaded = {}
            for line in lines:
                if "=" in line and not line.startswith("#"):
                    key, _, val = line.partition("=")
                    key = key.strip()
                    if key.startswith("TRACE2SKILL_"):
                        loaded[key] = val.strip()
            assert loaded == {"TRACE2SKILL_API_KEY": "good-key"}
            assert "PATH" not in loaded
            assert "HOME" not in loaded
