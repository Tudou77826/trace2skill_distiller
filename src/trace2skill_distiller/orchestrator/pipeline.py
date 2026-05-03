"""Distill pipeline — composes the four modules into a unified workflow."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from pathlib import Path

from rich.panel import Panel
from rich.table import Table

from ..core.config import DistillConfig
from ..core.console import console
from ..llm import LLMClient
from ..llm.providers.openai_compatible import OpenAICompatibleProvider
from ..mining.mining_facade import MiningLayer, DefaultMiningLayer
from ..mining.sources.opencode import OpenCodeSource
from ..mining.types import SessionMeta, TrajectorySummary
from ..analysis.analysis_facade import AnalysisLayer, DefaultAnalysisLayer
from ..analysis.clustering.semantic import SemanticClusterStrategy
from ..analysis.distillation.llm_distill import LLMDistillationStrategy
from ..analysis.types import TopicSkill
from ..output.output_facade import OutputLayer, DefaultOutputLayer
from ..output.formatters.skill_md import SkillMdFormatter, save_trajectories
from ..output.presenters.html_report import HtmlReportPresenter
from ..output.state import StateManager
from ..output.types import (
    DistillReport, SessionEntry, TopicEntry, StepTiming, LLMUsage, ShapingResult,
)


class DistillPipeline:
    """Orchestrates the full distillation workflow using pluggable modules."""

    def __init__(
        self,
        mining: MiningLayer,
        analysis: AnalysisLayer,
        output: OutputLayer,
        fast_llm: LLMClient,
        strong_llm: LLMClient,
        config: DistillConfig,
    ):
        self._mining = mining
        self._analysis = analysis
        self._output = output
        self._fast_llm = fast_llm
        self._strong_llm = strong_llm
        self._config = config

    def run(
        self,
        project: str | None = None,
        session_id: str | None = None,
        since: int | None = None,
        step: int | None = None,
        dry_run: bool = False,
    ) -> DistillReport:
        """Run the full distillation pipeline."""
        run_id = uuid.uuid4().hex[:8]
        project_name = project or "general"
        report = DistillReport(
            run_id=run_id,
            project=project_name,
            started_at=datetime.now().isoformat(),
        )
        run_start = time.monotonic()

        console.print(Panel(
            f"[bold]Trace2Skill Distiller v0.1[/]",
            subtitle=f"Project: {project or 'all'} | Run: {run_id}",
        ))

        # ── Step 0: List sessions ──
        if session_id:
            sessions_meta = [SessionMeta(id=session_id, title="specified session", msg_count=999)]
        else:
            sessions_meta = self._mining.list_available(project=project, since=since)

        if not sessions_meta:
            console.print("[yellow]No sessions found.[/]")
            _finalize_report(report, run_start, self._fast_llm, self._strong_llm, self._config, project_name)
            return report

        report.sessions_total = len(sessions_meta)

        # ── Filter candidates ──
        table = Table(title="Session Candidates")
        table.add_column("#", width=3)
        table.add_column("Session ID", width=20)
        table.add_column("Title", width=40)
        table.add_column("Msgs", width=5)
        table.add_column("Tools", width=5)

        candidates = self._mining.filter_candidates(
            sessions_meta,
            min_messages=self._config.filter.min_messages,
            min_tools=self._config.filter.min_tools,
        )

        for i, s in enumerate(candidates):
            table.add_row(
                str(i + 1),
                s.id[:20],
                (s.title or "")[:40],
                str(s.msg_count),
                str(s.tool_count),
            )

        console.print(table)
        console.print(f"\n[green]{len(candidates)}[/] sessions pass quality threshold "
                      f"(out of {len(sessions_meta)} total)")

        report.sessions_passed_filter = len(candidates)

        if not candidates:
            console.print("[yellow]No suitable sessions for distillation.[/]")
            _finalize_report(report, run_start, self._fast_llm, self._strong_llm, self._config, project_name)
            return report

        # ── Step 1: Preprocessing ──
        console.print("\n[bold]Step 1: Preprocessing (Level 0 → 1 → 2)...[/]")
        step_start = time.monotonic()
        trajectories = self._mining.mine([s.id for s in candidates])
        report.steps.append(StepTiming(
            name="Preprocessing (L0→L1→L2)",
            start=datetime.now().isoformat(),
            duration_seconds=time.monotonic() - step_start,
        ))

        # Collect session entries for report
        for t in trajectories:
            reason = ""
            if t.label != "success":
                parts = []
                if t.problems_encountered:
                    parts.append(t.problems_encountered[0].problem[:80])
                if t.lessons_learned:
                    parts.append(t.lessons_learned[0][:80])
                reason = "；".join(parts) if parts else "综合评分不足"
            report.sessions.append(SessionEntry(
                session_id=t.session_id,
                project=t.project,
                label=t.label,
                label_score=t.label_score,
                intent=t.intent,
                msg_count=sum(1 for s in candidates if s.id == t.session_id),
                problems_count=len(t.problems_encountered),
                lessons_count=len(t.lessons_learned),
                label_reason=reason,
            ))

        console.print(
            f"\nPreprocessing complete: "
            f"T+={sum(1 for t in trajectories if t.label == 'success')} "
            f"T±={sum(1 for t in trajectories if t.label == 'partial')} "
            f"T-={sum(1 for t in trajectories if t.label == 'failure')}"
        )

        if not trajectories:
            console.print("[yellow]No trajectories passed preprocessing.[/]")
            _finalize_report(report, run_start, self._fast_llm, self._strong_llm, self._config, project_name)
            return report

        if step == 1:
            output_dir = Path(self._config.output.skill_output_dir).expanduser()
            path = save_trajectories(trajectories, output_dir, project or "all")
            console.print(f"Trajectories saved to: {path}")
            _finalize_report(report, run_start, self._fast_llm, self._strong_llm, self._config, project_name)
            return report

        # ── Step 1.5 + 2: Analysis (clustering + distillation) ──
        console.print("\n[bold]Step 1.5: Clustering trajectories by topic...[/]")
        step_start = time.monotonic()
        output_dir = Path(self._config.output.skill_output_dir).expanduser()

        analysis_result = self._analysis.analyze(
            trajectories,
            project=project_name,
            output_dir=output_dir,
        )

        report.steps.append(StepTiming(
            name="Topic Clustering + Distillation",
            duration_seconds=time.monotonic() - step_start,
        ))
        report.topics_found = len(analysis_result.clustering.clusters)
        report.unclustered_count = len(analysis_result.clustering.unclustered)

        console.print(f"  Clustered into [green]{len(analysis_result.clustering.clusters)}[/] topics "
                      f"({len(analysis_result.clustering.unclustered)} unclustered)")
        for c in analysis_result.clustering.clusters:
            console.print(f"    - {c.topic_name} ({len(c.session_ids)} sessions)")

        total_rules = sum(len(s.rules) for s in analysis_result.skills)
        report.total_rules = total_rules
        console.print(f"\nDistilled {total_rules} rules across {len(analysis_result.skills)} topic skills")

        if not analysis_result.skills:
            console.print("[yellow]No rules distilled.[/]")
            _finalize_report(report, run_start, self._fast_llm, self._strong_llm, self._config, project_name)
            return report

        # Collect topic entries for report
        for s in analysis_result.skills:
            report.topics.append(TopicEntry(
                topic_id=s.topic_id,
                topic_name=s.topic_name,
                topic_summary=s.summary,
                session_count=len(s.source_sessions),
                session_ids=s.source_sessions,
                rule_count=len(s.rules),
                skill_title=s.skill_title,
                description=s.description,
                rules=s.rules,
            ))

        if step == 2 or dry_run:
            for s in analysis_result.skills:
                console.print(f"\n[bold]Topic: {s.skill_title}[/] ({len(s.rules)} rules)")
                console.print(f"  {s.summary}")
                for r in s.rules:
                    console.print(f"  [{r.type}] {r.action} (confidence: {r.confidence:.2f})")
            _finalize_report(report, run_start, self._fast_llm, self._strong_llm, self._config, project_name)
            return report

        # ── Step 3: Output ──
        console.print("\n[bold]Step 3: Writing topic skill files...[/]")
        step_start = time.monotonic()

        shaping = self._output.output(
            skills=analysis_result.skills,
            trajectories=trajectories,
            report=report,
            project=project_name,
        )

        # Update report with output paths
        for t in report.topics:
            for p in shaping.written_paths:
                if t.topic_id in str(p):
                    t.output_path = str(p)

        report.steps.append(StepTiming(
            name="Write SKILL.md Files",
            duration_seconds=time.monotonic() - step_start,
        ))

        console.print(Panel(
            f"Sessions analyzed: {len(trajectories)} "
            f"(T+={sum(1 for t in trajectories if t.label == 'success')}, "
            f"T-={sum(1 for t in trajectories if t.label != 'success')})\n"
            f"Topics discovered: {len(analysis_result.clustering.clusters)}\n"
            f"Skills written: {len(shaping.written_paths)}\n"
            f"Total rules: {total_rules}\n"
            f"Output dir: {output_dir / project_name}/",
            title="Distillation Complete",
        ))

        _finalize_report(report, run_start, self._fast_llm, self._strong_llm, self._config, project_name)
        return report

    @classmethod
    def from_config(cls, config: DistillConfig) -> "DistillPipeline":
        """Create a pipeline from configuration."""
        # Build LLM clients
        fast_provider = OpenAICompatibleProvider(config.fast_model)
        strong_provider = OpenAICompatibleProvider(config.strong_model)
        fast_llm = LLMClient(fast_provider)
        strong_llm = LLMClient(strong_provider)

        # Build mining layer
        source = OpenCodeSource(config.opencode.db_path, config.opencode.export_command)
        mining = DefaultMiningLayer(source, fast_llm, config)

        # Build analysis layer
        output_dir = Path(config.output.skill_output_dir).expanduser()
        analysis = DefaultAnalysisLayer(
            SemanticClusterStrategy(fast_llm, output_dir),
            LLMDistillationStrategy(strong_llm),
            config.analysis,
        )

        # Build output layer
        output = DefaultOutputLayer(
            SkillMdFormatter(merge_llm=fast_llm, max_rules=config.output.max_rules_per_skill),
            HtmlReportPresenter(),
            StateManager(),
            config.output,
        )

        return cls(mining, analysis, output, fast_llm, strong_llm, config)


def _finalize_report(
    report: DistillReport,
    run_start: float,
    fast_llm: LLMClient,
    strong_llm: LLMClient | None,
    config: DistillConfig,
    project_name: str,
):
    """Finalize report data and write HTML."""
    report.finished_at = datetime.now().isoformat()
    report.total_duration_seconds = time.monotonic() - run_start
    report.output_dir = str(Path(config.output.skill_output_dir).expanduser() / project_name)

    # Collect LLM stats
    fast_stats = fast_llm.reset_stats()
    report.llm_usage.append(LLMUsage(
        label=f"fast ({config.fast_model.model})",
        calls=fast_stats["calls"],
        input_tokens=fast_stats["input_tokens"],
        output_tokens=fast_stats["output_tokens"],
    ))
    if strong_llm:
        strong_stats = strong_llm.reset_stats()
        report.llm_usage.append(LLMUsage(
            label=f"strong ({config.strong_model.model})",
            calls=strong_stats["calls"],
            input_tokens=strong_stats["input_tokens"],
            output_tokens=strong_stats["output_tokens"],
        ))

    # Write HTML report
    report_dir = Path.home() / ".trace2skill" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{report.run_id}.html"
    presenter = HtmlReportPresenter()
    presenter.present(report, report_path)
    console.print(f"\n[bold green]Report:[/] {report_path}")
