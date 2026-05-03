"""Output layer facade — protocol and default implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..core.config import OutputConfig
from ..core.console import console
from ..mining.types import TrajectorySummary
from ..analysis.types import TopicSkill
from .types import DistillReport, ShapingResult
from .formatters.skill_md import SkillMdFormatter, save_trajectories
from .formatters.knowledge_md import write_knowledge
from .presenters.base import ReportPresenter
from .presenters.html_report import HtmlReportPresenter
from .state import StateManager


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
        formatter: SkillMdFormatter | None = None,
        presenter: ReportPresenter | None = None,
        state: StateManager | None = None,
        config: OutputConfig | None = None,
    ):
        self._formatter = formatter or SkillMdFormatter()
        self._presenter = presenter or HtmlReportPresenter()
        self._state = state or StateManager()
        self._config = config or OutputConfig()

    def output(
        self,
        skills: list[TopicSkill],
        trajectories: list[TrajectorySummary],
        report: DistillReport,
        project: str,
    ) -> ShapingResult:
        output_dir = Path(self._config.skill_output_dir).expanduser()
        fmt = self._config.format

        # Run selected formatter
        written_paths: list[Path] = []
        index_path: Path | None = None

        if fmt == "knowledge_md":
            index_path = write_knowledge(skills, output_dir, project)
            console.print(f"  Knowledge: {index_path}")
        else:
            for skill in skills:
                path = self._formatter.write_or_merge(skill, output_dir, project)
                written_paths.append(path)
                console.print(f"  Written: {path}")
            index_path = self._formatter.write_index(skills, output_dir, project)
            console.print(f"  Index: {index_path}")

        # Save trajectories
        save_trajectories(trajectories, output_dir, project)

        # Update state
        self._state.save(trajectories, project)

        # HTML report — always generated
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
