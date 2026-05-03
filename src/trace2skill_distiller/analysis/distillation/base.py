"""Distillation strategy protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..types import TopicCluster, TopicSkill
from ...mining.types import TrajectorySummary


@runtime_checkable
class DistillationStrategy(Protocol):
    """Distillation strategy protocol.

    Different analysis purposes use different prompts:
    - Skill distillation (current: extract reusable practices)
    - Code review analysis (extract quality issues)
    - Architecture decision analysis (extract tech choices)
    """

    def distill_topic(
        self,
        trajectories: list[TrajectorySummary],
        cluster: TopicCluster,
    ) -> TopicSkill | None:
        """Distill a single topic cluster into a skill."""
        ...

    def distill_all(
        self,
        trajectories: list[TrajectorySummary],
        clusters: list[TopicCluster],
    ) -> list[TopicSkill]:
        """Distill all topic clusters."""
        ...
