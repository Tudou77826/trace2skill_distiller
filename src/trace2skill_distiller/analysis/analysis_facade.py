"""Analysis layer facade — protocol and default implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..llm import LLMClient
from ..core.config import AnalysisConfig
from ..mining.types import TrajectorySummary
from .types import TopicCluster, ClusteringResult, TopicSkill, AnalysisResult
from .clustering.base import ClusterStrategy
from .clustering.semantic import SemanticClusterStrategy
from .distillation.base import DistillationStrategy
from .distillation.llm_distill import LLMDistillationStrategy


@runtime_checkable
class AnalysisLayer(Protocol):
    """Analysis layer public interface."""

    def analyze(
        self,
        trajectories: list[TrajectorySummary],
        project: str = "",
        output_dir: Path | None = None,
    ) -> AnalysisResult:
        ...


class DefaultAnalysisLayer:
    """Default analysis layer using pluggable clustering and distillation strategies."""

    def __init__(
        self,
        cluster_strategy: ClusterStrategy,
        distill_strategy: DistillationStrategy,
        config: AnalysisConfig | None = None,
    ):
        self._cluster = cluster_strategy
        self._distill = distill_strategy
        self._config = config or AnalysisConfig()

    def analyze(
        self,
        trajectories: list[TrajectorySummary],
        project: str = "",
        output_dir: Path | None = None,
    ) -> AnalysisResult:
        clustering = self._cluster.cluster(
            trajectories,
            min_size=self._config.clustering_min_size,
            max_topics=self._config.clustering_max_topics,
            protected_topics=self._config.protected_topics or None,
        )

        skills = self._distill.distill_all(
            trajectories,
            clustering.clusters,
        )

        return AnalysisResult(
            clustering=clustering,
            skills=skills,
        )
