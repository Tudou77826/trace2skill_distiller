"""Tests for pipeline mode and preview semantics."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trace2skill_distiller.analysis.types import (
    AnalysisResult,
    ClusteringResult,
    SkillRule,
    TopicCluster,
    TopicSkill,
)
from trace2skill_distiller.core.config import DistillConfig, OutputConfig
from trace2skill_distiller.mining.types import SessionMeta, TrajectorySummary
from trace2skill_distiller.orchestrator.pipeline import DistillPipeline
from trace2skill_distiller.output.types import ShapingResult


class _FakeLLM:
    def reset_stats(self):
        return {"calls": 0, "input_tokens": 0, "output_tokens": 0}


class _FakeMining:
    def list_available(self, project=None, since=None):
        return [SessionMeta(id="s1", title="Session", project="demo", msg_count=10, tool_count=3, timestamp=1)]

    def filter_candidates(self, sessions_meta, min_messages, min_tools):
        return sessions_meta

    def mine(self, session_ids):
        return [
            TrajectorySummary(
                session_id="s1",
                session_type="task",
                project="demo",
                intent="fix cli",
                label="success",
                label_score=0.9,
            )
        ]


class _FakeAnalysis:
    def analyze(self, trajectories, project, output_dir):
        return AnalysisResult(
            clustering=ClusteringResult(
                clusters=[
                    TopicCluster(
                        topic_id="cli-redesign",
                        topic_name="CLI redesign",
                        topic_summary="Update CLI",
                        session_ids=["s1"],
                        primary_project="demo",
                    )
                ],
                unclustered=[],
            ),
            skills=[
                TopicSkill(
                    topic_id="cli-redesign",
                    topic_name="CLI redesign",
                    skill_title="CLI redesign",
                    skill_type="procedure",
                    description="Redesign CLI commands.",
                    summary="Updated CLI structure.",
                    rules=[
                        SkillRule(
                            id="r1",
                            type="ALWAYS",
                            action="Prefer source from config",
                            confidence=0.9,
                        )
                    ],
                    body="Use config-driven source selection.",
                    source_sessions=["s1"],
                )
            ],
        )


class _FakeOutput:
    def __init__(self, output_path: Path):
        self.called = 0
        self.output_path = output_path

    def output(self, skills, trajectories, report, project):
        self.called += 1
        return ShapingResult(written_paths=[self.output_path], index_path=None, report_path=None)


def test_preview_skips_output_and_report_files(tmp_path):
    cfg = DistillConfig(output=OutputConfig(skill_output_dir=str(tmp_path / "skills")))
    fake_output = _FakeOutput(tmp_path / "skills" / "demo" / "cli-redesign" / "SKILL.md")
    pipeline = DistillPipeline(_FakeMining(), _FakeAnalysis(), fake_output, _FakeLLM(), _FakeLLM(), cfg)

    with patch("trace2skill_distiller.orchestrator.pipeline.Path.home", return_value=tmp_path):
        report = pipeline.run(project="demo", mode="analyze", preview=True)

    assert report.total_rules == 1
    assert fake_output.called == 0
    assert not (tmp_path / ".trace2skill" / "reports").exists()


def test_full_mode_writes_json_and_html_reports(tmp_path):
    cfg = DistillConfig(output=OutputConfig(skill_output_dir=str(tmp_path / "skills")))
    fake_output = _FakeOutput(tmp_path / "skills" / "demo" / "cli-redesign" / "SKILL.md")
    pipeline = DistillPipeline(_FakeMining(), _FakeAnalysis(), fake_output, _FakeLLM(), _FakeLLM(), cfg)

    with patch("trace2skill_distiller.orchestrator.pipeline.Path.home", return_value=tmp_path):
        report = pipeline.run(project="demo", mode="full", preview=False)

    report_dir = tmp_path / ".trace2skill" / "reports"
    assert fake_output.called == 1
    assert (report_dir / f"{report.run_id}.json").exists()
    assert (report_dir / f"{report.run_id}.html").exists()
