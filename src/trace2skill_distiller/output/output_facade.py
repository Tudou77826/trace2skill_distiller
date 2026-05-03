"""Output layer facade — protocol and default implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..llm import LLMClient
from ..core.config import OutputConfig
from ..mining.types import TrajectorySummary
from ..analysis.types import TopicSkill
from .types import DistillReport, ShapingResult
from .formatters.base import SkillFormatter
from .formatters.skill_md import SkillMdFormatter, save_trajectories
from .presenters.base import ReportPresenter
from .presenters.html_report import HtmlReportPresenter
from .state import StateManager

import sys
from rich.console import Console
_console_file = open(sys.stderr.fileno(), mode='w', encoding='utf-8', errors='replace', closefd=False)
console = Console(file=_console_file)


@runtime_checkable
class OutputLayer(Protocol):
    """Output layer public interface."""

    def output(
        self,
        skills: list[TopicSkill],
        trajectories: list[TrajectorySummary],
        report: DistillReport,
        project: str,
    ) -> ShapingResult:
        ...


class DefaultOutputLayer:
    """Default output layer using pluggable formatter and presenter."""

    def __init__(
        self,
        formatter: SkillFormatter,
        presenter: ReportPresenter,
        state: StateManager,
        config: OutputConfig | None = None,
    ):
        self._formatter = formatter
        self._presenter = presenter
        self._state = state
        self._config = config or OutputConfig()

    def output(
        self,
        skills: list[TopicSkill],
        trajectories: list[TrajectorySummary],
        report: DistillReport,
        project: str,
    ) -> ShapingResult:
        output_dir = Path(self._config.skill_output_dir).expanduser()

        # Write per-topic skill files
        written_paths = []
        for skill in skills:
            if isinstance(self._formatter, SkillMdFormatter):
                path = self._formatter.write_or_merge(skill, output_dir, project)
            else:
                path = self._formatter.write(skill, output_dir, project)
            written_paths.append(path)
            console.print(f"  Written: {path}")

        # Write index
        index_path = self._formatter.write_index(skills, output_dir, project)
        console.print(f"  Index: {index_path}")

        # Save trajectories
        save_trajectories(trajectories, output_dir, project)

        # Update state
        self._state.save(trajectories, project)

        # Write HTML report
        report_dir = Path.home() / ".trace2skill" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"{report.run_id}.html"
        self._presenter.present(report, report_path)
        console.print(f"\n[bold green]Report:[/] {report_path}")

        return ShapingResult(
            written_paths=written_paths,
            index_path=index_path,
            report_path=report_path,
        )
