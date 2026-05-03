"""Mining layer facade — protocol and default implementation."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..llm import LLMClient
from ..core.config import DistillConfig
from ..core.console import console
from .types import SessionMeta, TrajectorySummary
from .sources.base import SessionSource
from .sources.opencode import OpenCodeSource
from .preprocess.pipeline import run_pipeline, run_batch


@runtime_checkable
class MiningLayer(Protocol):
    """Mining layer public interface."""

    def list_available(
        self,
        project: str | None = None,
        since: int | None = None,
    ) -> list[SessionMeta]:
        """List available sessions from the data source."""
        ...

    def filter_candidates(
        self,
        sessions: list[SessionMeta],
        min_messages: int = 5,
        min_tools: int = 3,
    ) -> list[SessionMeta]:
        """Filter sessions by quality thresholds."""
        ...

    def mine(self, session_ids: list[str]) -> list[TrajectorySummary]:
        """Run the full L0→L1→L2 preprocessing pipeline on given sessions."""
        ...


class DefaultMiningLayer:
    """Default mining layer implementation using a SessionSource and LLM."""

    def __init__(
        self,
        source: SessionSource,
        llm: LLMClient,
        config: DistillConfig | None = None,
    ):
        self._source = source
        self._llm = llm
        if config is not None:
            self._min_messages = config.filter.min_messages
            self._min_tools = config.filter.min_tools
            self._distill_config = config
        else:
            self._min_messages = 5
            self._min_tools = 3
            self._distill_config = None

    def list_available(
        self,
        project: str | None = None,
        since: int | None = None,
    ) -> list[SessionMeta]:
        return self._source.list_sessions(project=project, since=since)

    def filter_candidates(
        self,
        sessions: list[SessionMeta],
        min_messages: int = 5,
        min_tools: int = 3,
    ) -> list[SessionMeta]:
        candidates = []
        for s in sessions:
            tc = self._source.count_tools(s.id)
            mc = s.msg_count
            if mc >= min_messages and tc >= min_tools:
                # Update tool count in the meta
                s.tool_count = tc
                candidates.append(s)
        return candidates

    def mine(self, session_ids: list[str]) -> list[TrajectorySummary]:
        return run_batch(
            session_ids,
            self._llm,
            self._source,
            self._distill_config,
        )
