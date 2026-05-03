"""State persistence for incremental processing."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..mining.types import TrajectorySummary
from .types import RunState


class StateManager:
    """Manage RunState persistence."""

    def __init__(self, state_dir: Path | None = None):
        self._state_dir = state_dir or (Path.home() / ".trace2skill")
        self._state_file = self._state_dir / "state.json"

    def save(self, trajectories: list[TrajectorySummary], project: str | None = None) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)

        state = self.load()
        state.last_run = datetime.now().isoformat()
        state.processed_sessions = list(set(
            state.processed_sessions + [t.session_id for t in trajectories]
        ))
        state.stats["total_processed"] = len(state.processed_sessions)

        self._state_file.write_text(
            json.dumps(state.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self) -> RunState:
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                return RunState.model_validate(data)
            except Exception:
                pass
        return RunState()

    def get_last_run_ts(self) -> int | None:
        state = self.load()
        if state.last_run:
            dt = datetime.fromisoformat(state.last_run)
            return int(dt.timestamp() * 1000)
        return None
