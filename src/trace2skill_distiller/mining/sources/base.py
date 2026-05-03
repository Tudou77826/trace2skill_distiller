"""Session source protocol — interface for data source adapters."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..types import Session, SessionMeta


@runtime_checkable
class SessionSource(Protocol):
    """Data source protocol.

    Each Coding Agent's data access method differs:
    - OpenCode: SQLite DB + CLI export
    - Claude Code: JSONL logs / API
    - Custom: filesystem / API / database
    """

    def list_sessions(
        self,
        project: str | None = None,
        since: int | None = None,
    ) -> list[SessionMeta]:
        """List available sessions, optionally filtered."""
        ...

    def get_session(self, session_id: str) -> Session | None:
        """Export and return a full session."""
        ...

    def count_tools(self, session_id: str) -> int:
        """Count tool-call parts for a session."""
        ...
