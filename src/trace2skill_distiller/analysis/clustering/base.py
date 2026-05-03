"""Cluster strategy protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..types import ClusteringResult
from ...mining.types import TrajectorySummary


@runtime_checkable
class ClusterStrategy(Protocol):
    """Clustering strategy protocol.

    Different analysis purposes may require different clustering:
    - LLM semantic clustering (current)
    - Embedding vector clustering
    - Rule-based keyword clustering
    """

    def cluster(
        self,
        trajectories: list[TrajectorySummary],
        min_size: int = 2,
        max_topics: int = 8,
        existing_topics: dict[str, str] | None = None,
        protected_topics: list[str] | None = None,
    ) -> ClusteringResult:
        """Cluster trajectories into topic groups."""
        ...
