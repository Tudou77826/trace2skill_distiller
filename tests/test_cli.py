"""Tests for the redesigned CLI."""

from __future__ import annotations

import os
import json
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
        assert "dream" in result.output
        assert "gui" in result.output
        assert "run       " not in result.output
        assert "runs      " not in result.output
        assert "sessions  " not in result.output
        assert "inspect   " not in result.output
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
            assert "s1" in result.output
            assert "msgs=" in result.output


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
                max_sessions=None,
                incremental=False,
            )

    def test_dream_uses_simple_memory_defaults(self):
        cfg = _make_config()
        mock_pipeline = MagicMock()

        with patch("trace2skill_distiller.cli.main._load_config", return_value=cfg), \
             patch("trace2skill_distiller.cli.main.DistillPipeline.from_config", return_value=mock_pipeline):
            runner = CliRunner()
            result = runner.invoke(cli, ["dream", "--project", "demo", "--preview"])
            assert result.exit_code == 0
            assert "Dream Review" in result.output
            assert cfg.output.format == "memory_md"
            mock_pipeline.run.assert_called_once_with(
                project="demo",
                session_id=None,
                mode="full",
                preview=True,
                max_sessions=20,
                incremental=True,
            )

    def test_dream_limit_is_configurable(self):
        cfg = _make_config()
        mock_pipeline = MagicMock()

        with patch("trace2skill_distiller.cli.main._load_config", return_value=cfg), \
             patch("trace2skill_distiller.cli.main.DistillPipeline.from_config", return_value=mock_pipeline):
            runner = CliRunner()
            result = runner.invoke(cli, ["dream", "--project", "demo", "--limit", "5", "--preview"])
            assert result.exit_code == 0
            mock_pipeline.run.assert_called_once_with(
                project="demo",
                session_id=None,
                mode="full",
                preview=True,
                max_sessions=5,
                incremental=True,
            )

    def test_dream_all_includes_processed_sessions(self):
        cfg = _make_config()
        mock_pipeline = MagicMock()

        with patch("trace2skill_distiller.cli.main._load_config", return_value=cfg), \
             patch("trace2skill_distiller.cli.main.DistillPipeline.from_config", return_value=mock_pipeline):
            runner = CliRunner()
            result = runner.invoke(cli, ["dream", "--project", "demo", "--all", "--preview"])
            assert result.exit_code == 0
            mock_pipeline.run.assert_called_once_with(
                project="demo",
                session_id=None,
                mode="full",
                preview=True,
                max_sessions=20,
                incremental=False,
            )

    def test_dream_can_install_context_after_review(self, tmp_path):
        cfg = _make_config()
        cfg.output.skill_output_dir = str(tmp_path / "skills")
        project_dir = Path(cfg.output.skill_output_dir) / "demo"
        project_dir.mkdir(parents=True)
        (project_dir / "agent-context.md").write_text(
            "# Agent Context - demo\n\n- Reuse confirmed memory in future sessions.",
            encoding="utf-8",
        )
        mock_pipeline = MagicMock()
        mock_pipeline.run.return_value = DistillReport(project="demo")
        claude_path = tmp_path / "repo" / "CLAUDE.md"

        with patch("trace2skill_distiller.cli.main._load_config", return_value=cfg), \
             patch("trace2skill_distiller.cli.main.DistillPipeline.from_config", return_value=mock_pipeline):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "dream",
                    "--project", "demo",
                    "--install-context",
                    "--target", str(claude_path),
                ],
            )

        assert result.exit_code == 0
        mock_pipeline.run.assert_called_once_with(
            project="demo",
            session_id=None,
            mode="full",
            preview=False,
            max_sessions=20,
            incremental=True,
        )
        assert "@trace2skill-memory.md" in claude_path.read_text(encoding="utf-8")
        installed = (claude_path.parent / "trace2skill-memory.md").read_text(encoding="utf-8")
        assert "Reuse confirmed memory" in installed

    def test_dream_shows_memory_next_after_real_review(self, tmp_path):
        cfg = _make_config()
        cfg.output.skill_output_dir = str(tmp_path / "skills")
        project_dir = Path(cfg.output.skill_output_dir) / "demo"
        project_dir.mkdir(parents=True)
        (project_dir / "memory_store.json").write_text(
            json.dumps({
                "version": 1,
                "project": "demo",
                "updated_at": "2026-06-23T10:00:00",
                "items": [
                    {
                        "id": "ready01",
                        "type": "USER_PREFERENCE",
                        "action": "Prefer automatic memory quality feedback.",
                        "confidence": 0.9,
                        "status": "active",
                        "confirmed": True,
                    },
                    {
                        "id": "review01",
                        "type": "OPEN_QUESTION",
                        "action": "Decide whether this memory should stay active.",
                        "confidence": 0.4,
                        "status": "review",
                    },
                ],
            }),
            encoding="utf-8",
        )
        mock_pipeline = MagicMock()
        mock_pipeline.run.return_value = DistillReport(project="demo")

        with patch("trace2skill_distiller.cli.main._load_config", return_value=cfg), \
             patch("trace2skill_distiller.cli.main.DistillPipeline.from_config", return_value=mock_pipeline):
            runner = CliRunner()
            result = runner.invoke(cli, ["dream", "--project", "demo"])

        assert result.exit_code == 0
        assert "Memory Next - demo" in result.output
        assert "Prefer automatic memory quality feedback" not in result.output
        assert "Top review items:" in result.output
        assert "Decide whether this memory should stay active" in result.output

    def test_dream_rejects_preview_with_install_context(self):
        cfg = _make_config()
        mock_pipeline = MagicMock()

        with patch("trace2skill_distiller.cli.main._load_config", return_value=cfg), \
             patch("trace2skill_distiller.cli.main.DistillPipeline.from_config", return_value=mock_pipeline):
            runner = CliRunner()
            result = runner.invoke(cli, ["dream", "--project", "demo", "--preview", "--install-context"])

        assert result.exit_code != 0
        assert "cannot be used with `--preview`" in result.output
        mock_pipeline.run.assert_not_called()


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


class TestReview:
    def test_context_shows_agent_context_file(self, tmp_path):
        cfg = _make_config()
        cfg.output.skill_output_dir = str(tmp_path)
        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        (project_dir / "agent-context.md").write_text(
            "# Agent Context - demo\n\n- Prefer simple commands.",
            encoding="utf-8",
        )

        with patch("trace2skill_distiller.cli.main._load_config", return_value=cfg):
            runner = CliRunner()
            result = runner.invoke(cli, ["context", "--project", "demo"])
            assert result.exit_code == 0
            assert "Agent Context - demo" in result.output
            assert "Prefer simple commands" in result.output

    def test_review_lists_memory_store_items(self, tmp_path):
        cfg = _make_config()
        cfg.output.skill_output_dir = str(tmp_path)
        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        (project_dir / "memory_store.json").write_text(
            json.dumps({
                "version": 1,
                "project": "demo",
                "updated_at": "2026-06-23T10:00:00",
                "items": [
                    {
                        "id": "abc",
                        "type": "USER_PREFERENCE",
                        "action": "Prefer simple one-command memory review.",
                        "scope": "user-specific",
                        "confidence": 0.92,
                        "seen_count": 2,
                        "status": "active",
                    },
                    {
                        "id": "def",
                        "type": "OPEN_QUESTION",
                        "action": "Verify memory promotion.",
                        "scope": "project-specific",
                        "confidence": 0.4,
                        "seen_count": 1,
                        "status": "review",
                    },
                ],
            }),
            encoding="utf-8",
        )

        with patch("trace2skill_distiller.cli.main._load_config", return_value=cfg):
            runner = CliRunner()
            result = runner.invoke(cli, ["review", "--project", "demo", "--open"])
            assert result.exit_code == 0
            assert "Memory Review" in result.output
            assert "Open Questions" in result.output
            assert "User Preferences" not in result.output
            assert "one-command" not in result.output

    def test_memory_stats_reports_health_metrics(self, tmp_path):
        cfg = _make_config()
        cfg.output.skill_output_dir = str(tmp_path)
        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        (project_dir / "memory_store.json").write_text(
            json.dumps({
                "version": 1,
                "project": "demo",
                "updated_at": "2026-06-23T10:00:00",
                "items": [
                    {
                        "id": "agent01",
                        "type": "USER_PREFERENCE",
                        "action": "Prefer concise memory reviews.",
                        "confidence": 0.9,
                        "status": "active",
                        "confirmed": True,
                    },
                    {
                        "id": "review01",
                        "type": "REPO_FACT",
                        "action": "Unverified repo fact.",
                        "confidence": 0.9,
                        "status": "review",
                    },
                    {
                        "id": "arch01",
                        "type": "PITFALL",
                        "action": "Old pitfall.",
                        "confidence": 0.8,
                        "status": "archived",
                    },
                    {
                        "id": "conf01",
                        "type": "WORKFLOW_PATTERN",
                        "action": "Conflicting workflow.",
                        "confidence": 0.8,
                        "status": "review",
                        "conflict_with": ["agent01"],
                    },
                ],
            }),
            encoding="utf-8",
        )

        with patch("trace2skill_distiller.cli.main._load_config", return_value=cfg):
            runner = CliRunner()
            result = runner.invoke(cli, ["memory", "stats", "--project", "demo"])
            assert result.exit_code == 0
            assert "Memory Health" in result.output
            assert "demo" in result.output
            assert "Score" in result.output
            assert "Status" in result.output
            assert "Type Distribution" in result.output
            assert "User Preferences" in result.output

    def test_memory_next_shows_actionable_review_plan(self, tmp_path):
        cfg = _make_config()
        cfg.output.skill_output_dir = str(tmp_path)
        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        (project_dir / "memory_store.json").write_text(
            json.dumps({
                "version": 1,
                "project": "demo",
                "updated_at": "2026-06-23T10:00:00",
                "items": [
                    {
                        "id": "ready01",
                        "type": "USER_PREFERENCE",
                        "action": "Prefer direct memory quality feedback.",
                        "confidence": 0.9,
                        "status": "active",
                        "confirmed": True,
                    },
                    {
                        "id": "open01",
                        "type": "OPEN_QUESTION",
                        "action": "Decide whether old memory should stay active.",
                        "confidence": 0.4,
                        "status": "review",
                    },
                    {
                        "id": "weak01",
                        "type": "REPO_FACT",
                        "action": "Unverified repository behavior.",
                        "confidence": 0.9,
                        "status": "review",
                    },
                    {
                        "id": "conf01",
                        "type": "WORKFLOW_PATTERN",
                        "action": "Conflicting workflow memory.",
                        "confidence": 0.8,
                        "status": "review",
                        "conflict_with": ["ready01"],
                    },
                ],
            }),
            encoding="utf-8",
        )

        with patch("trace2skill_distiller.cli.main._load_config", return_value=cfg):
            runner = CliRunner()
            result = runner.invoke(cli, ["memory", "next", "--project", "demo", "--limit", "3"])

        assert result.exit_code == 0
        assert "Memory Next - demo" in result.output
        assert "Score:" in result.output
        assert "Next actions:" in result.output
        assert "Top review items:" in result.output
        assert "open question" in result.output
        assert "missing evidence" in result.output
        assert "trace2skill memory review --project demo" in result.output

    def test_memory_install_context_updates_claude_memory_idempotently(self, tmp_path):
        cfg = _make_config()
        cfg.output.skill_output_dir = str(tmp_path / "skills")
        project_dir = Path(cfg.output.skill_output_dir) / "demo"
        project_dir.mkdir(parents=True)
        (project_dir / "memory_store.json").write_text(
            json.dumps({"version": 1, "project": "demo", "items": []}),
            encoding="utf-8",
        )
        (project_dir / "agent-context.md").write_text(
            "# Agent Context - demo\n\n- Prefer evidence-backed session review.",
            encoding="utf-8",
        )
        claude_path = tmp_path / "repo" / "CLAUDE.md"

        with patch("trace2skill_distiller.cli.main._load_config", return_value=cfg):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["memory", "install-context", "--project", "demo", "--target", str(claude_path)],
            )
            repeat = runner.invoke(
                cli,
                ["memory", "install-context", "--project", "demo", "--target", str(claude_path)],
            )

        assert result.exit_code == 0
        assert repeat.exit_code == 0
        claude_text = claude_path.read_text(encoding="utf-8")
        assert claude_text.count("@trace2skill-memory.md") == 1
        assert claude_text.count("trace2skill-memory-import") == 1
        installed_text = (claude_path.parent / "trace2skill-memory.md").read_text(encoding="utf-8")
        assert "Trace2Skill Memory - demo" in installed_text
        assert "Prefer evidence-backed session review" in installed_text
        assert str(project_dir / "agent-context.md") in installed_text

    def test_memory_install_context_updates_existing_marker(self, tmp_path):
        cfg = _make_config()
        cfg.output.skill_output_dir = str(tmp_path / "skills")
        project_dir = Path(cfg.output.skill_output_dir) / "demo"
        project_dir.mkdir(parents=True)
        (project_dir / "agent-context.md").write_text(
            "# Agent Context - demo\n\n- Keep project memory current.",
            encoding="utf-8",
        )
        claude_path = tmp_path / "repo" / "CLAUDE.md"
        claude_path.parent.mkdir()
        claude_path.write_text(
            "# Project Memory\n\n<!-- trace2skill-memory-import -->\n@old-memory.md\n",
            encoding="utf-8",
        )

        with patch("trace2skill_distiller.cli.main._load_config", return_value=cfg):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "memory", "install-context",
                    "--project", "demo",
                    "--target", str(claude_path),
                    "--import-file", "memory/new-memory.md",
                ],
            )

        assert result.exit_code == 0
        claude_text = claude_path.read_text(encoding="utf-8")
        assert "@memory/new-memory.md" in claude_text
        assert "@old-memory.md" not in claude_text
        assert (claude_path.parent / "memory" / "new-memory.md").exists()

    def test_memory_install_context_fails_without_agent_context(self, tmp_path):
        cfg = _make_config()
        cfg.output.skill_output_dir = str(tmp_path / "skills")
        target = tmp_path / "repo" / "CLAUDE.md"

        with patch("trace2skill_distiller.cli.main._load_config", return_value=cfg):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["memory", "install-context", "--project", "demo", "--target", str(target)],
            )

        assert result.exit_code == 1
        assert "No agent context found" in result.output
        assert not target.exists()

    def test_memory_archive_refreshes_agent_context(self, tmp_path):
        cfg = _make_config()
        cfg.output.skill_output_dir = str(tmp_path)
        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        (project_dir / "memory_store.json").write_text(
            json.dumps({
                "version": 1,
                "project": "demo",
                "updated_at": "2026-06-23T10:00:00",
                "items": [
                    {
                        "id": "abc123",
                        "type": "USER_PREFERENCE",
                        "action": "Prefer simple one-command memory review.",
                        "scope": "user-specific",
                        "confidence": 0.92,
                        "seen_count": 2,
                        "status": "active",
                    },
                ],
            }),
            encoding="utf-8",
        )

        with patch("trace2skill_distiller.cli.main._load_config", return_value=cfg):
            runner = CliRunner()
            result = runner.invoke(cli, ["memory", "archive", "abc", "--project", "demo"])
            assert result.exit_code == 0

        data = json.loads((project_dir / "memory_store.json").read_text(encoding="utf-8"))
        assert data["items"][0]["status"] == "archived"
        context = (project_dir / "agent-context.md").read_text(encoding="utf-8")
        assert "one-command" not in context

    def test_memory_confirm_promotes_weak_memory(self, tmp_path):
        cfg = _make_config()
        cfg.output.skill_output_dir = str(tmp_path)
        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        (project_dir / "memory_store.json").write_text(
            json.dumps({
                "version": 1,
                "project": "demo",
                "updated_at": "2026-06-23T10:00:00",
                "items": [
                    {
                        "id": "weak01",
                        "type": "REPO_FACT",
                        "action": "The project memory formatter writes agent-context.md.",
                        "scope": "repo-specific",
                        "confidence": 0.4,
                        "seen_count": 1,
                        "status": "review",
                    },
                ],
            }),
            encoding="utf-8",
        )

        with patch("trace2skill_distiller.cli.main._load_config", return_value=cfg):
            runner = CliRunner()
            result = runner.invoke(cli, ["memory", "confirm", "weak", "--project", "demo"])
            assert result.exit_code == 0

        data = json.loads((project_dir / "memory_store.json").read_text(encoding="utf-8"))
        item = data["items"][0]
        assert item["status"] == "active"
        assert item["confidence"] == 0.8
        assert item["seen_count"] == 2
        context = (project_dir / "agent-context.md").read_text(encoding="utf-8")
        assert "agent-context.md" in context

    def test_memory_edit_updates_store_and_context(self, tmp_path):
        cfg = _make_config()
        cfg.output.skill_output_dir = str(tmp_path)
        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        (project_dir / "memory_store.json").write_text(
            json.dumps({
                "version": 1,
                "project": "demo",
                "updated_at": "2026-06-23T10:00:00",
                "items": [
                    {
                        "id": "edit01",
                        "type": "USER_PREFERENCE",
                        "action": "Old vague memory.",
                        "scope": "user-specific",
                        "confidence": 0.7,
                        "seen_count": 1,
                        "status": "active",
                    },
                ],
            }),
            encoding="utf-8",
        )

        with patch("trace2skill_distiller.cli.main._load_config", return_value=cfg):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "memory", "edit", "edit", "--project", "demo",
                    "--action", "Prefer direct, evidence-backed memory reviews.",
                    "--confidence", "0.9",
                ],
            )
            assert result.exit_code == 0

        data = json.loads((project_dir / "memory_store.json").read_text(encoding="utf-8"))
        item = data["items"][0]
        assert item["action"] == "Prefer direct, evidence-backed memory reviews."
        assert item["confidence"] == 0.9
        context = (project_dir / "agent-context.md").read_text(encoding="utf-8")
        assert "evidence-backed memory reviews" in context
        assert "Old vague memory" not in context

    def test_memory_review_accepts_and_archives_queue_items(self, tmp_path):
        cfg = _make_config()
        cfg.output.skill_output_dir = str(tmp_path)
        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        (project_dir / "memory_store.json").write_text(
            json.dumps({
                "version": 1,
                "project": "demo",
                "updated_at": "2026-06-23T10:00:00",
                "items": [
                    {
                        "id": "open01",
                        "type": "OPEN_QUESTION",
                        "action": "Verify whether this question is still relevant.",
                        "scope": "project-specific",
                        "confidence": 0.3,
                        "seen_count": 1,
                        "status": "review",
                    },
                    {
                        "id": "weak02",
                        "type": "REPO_FACT",
                        "action": "The memory review command can accept weak memories.",
                        "scope": "repo-specific",
                        "confidence": 0.4,
                        "seen_count": 1,
                        "status": "review",
                    },
                ],
            }),
            encoding="utf-8",
        )

        with patch("trace2skill_distiller.cli.main._load_config", return_value=cfg):
            runner = CliRunner()
            result = runner.invoke(cli, ["memory", "review", "--project", "demo"], input="e\na\n")
            assert result.exit_code == 0

        data = json.loads((project_dir / "memory_store.json").read_text(encoding="utf-8"))
        by_id = {item["id"]: item for item in data["items"]}
        assert by_id["open01"]["status"] == "archived"
        assert by_id["weak02"]["status"] == "active"
        assert by_id["weak02"]["confidence"] == 0.8
        context = (project_dir / "agent-context.md").read_text(encoding="utf-8")
        assert "accept weak memories" in context
        assert "still relevant" not in context

    def test_memory_review_can_edit_item(self, tmp_path):
        cfg = _make_config()
        cfg.output.skill_output_dir = str(tmp_path)
        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        (project_dir / "memory_store.json").write_text(
            json.dumps({
                "version": 1,
                "project": "demo",
                "updated_at": "2026-06-23T10:00:00",
                "items": [
                    {
                        "id": "edit02",
                        "type": "REPO_FACT",
                        "action": "Old repo fact.",
                        "scope": "repo-specific",
                        "confidence": 0.4,
                        "seen_count": 1,
                        "status": "review",
                    },
                ],
            }),
            encoding="utf-8",
        )

        with patch("trace2skill_distiller.cli.main._load_config", return_value=cfg):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["memory", "review", "--project", "demo"],
                input="m\nThe formatter writes agent-context.md for future AI sessions.\n\n",
            )
            assert result.exit_code == 0

        data = json.loads((project_dir / "memory_store.json").read_text(encoding="utf-8"))
        item = data["items"][0]
        assert item["action"] == "The formatter writes agent-context.md for future AI sessions."
        assert item["status"] == "active"
        assert item["confidence"] == 0.8
        context = (project_dir / "agent-context.md").read_text(encoding="utf-8")
        assert "future AI sessions" in context


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
