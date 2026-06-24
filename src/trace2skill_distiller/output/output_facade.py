"""Output layer facade — protocol and default implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..core.config import OutputConfig
from ..core.console import console
from ..mining.types import TrajectorySummary
from ..analysis.types import TopicSkill
from .types import DistillReport, ShapingResult
from .formatters.trajectories import save_trajectories
from .formatters.memory_md import (
    AGENT_CONTEXT_FILENAME,
    REPO_FACTS_FILENAME,
    USER_PROFILE_FILENAME,
    write_memory,
)
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
    """Default output layer writing the memory_md review format.

    The pipeline only supports the memory_md format now. The three derived
    .md files (agent-context / user-profile / repo-facts) honor per-file
    destination paths from OutputConfig and append to an existing file when
    one is configured.
    """

    def __init__(
        self,
        config: OutputConfig | None = None,
        state: StateManager | None = None,
    ):
        self._config = config or OutputConfig()
        self._state = state or StateManager()

    def output(
        self,
        skills: list[TopicSkill],
        trajectories: list[TrajectorySummary],
        report: DistillReport,
        project: str,
    ) -> ShapingResult:
        output_dir = Path(self._config.skill_output_dir).expanduser()
        cfg = self._config

        index_path = write_memory(
            skills,
            output_dir,
            project,
            agent_context_path=cfg.agent_context_path,
            user_profile_path=cfg.user_profile_path,
            repo_facts_path=cfg.repo_facts_path,
        )
        written_paths: list[Path] = [index_path]
        project_dir = output_dir / project
        written_paths.extend([
            project_dir / AGENT_CONTEXT_FILENAME,
            project_dir / USER_PROFILE_FILENAME,
            project_dir / REPO_FACTS_FILENAME,
        ])
        console.print(f"  Memory: {index_path}")
        console.print(f"  Agent context: {project_dir / AGENT_CONTEXT_FILENAME}")

        # Save trajectories
        save_trajectories(trajectories, output_dir, project)

        # Update state
        self._state.save(trajectories, project)

        return ShapingResult(
            written_paths=written_paths,
            index_path=index_path,
            report_path=None,
        )
