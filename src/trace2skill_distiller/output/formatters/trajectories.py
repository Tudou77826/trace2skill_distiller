"""Persist raw trajectory summaries for later inspection.

Split out of the former skill_md formatter so the memory-only pipeline still
has a stable import path after the skill/knowledge formatters were removed.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def save_trajectories(
    trajectories: list,
    output_dir: Path,
    project: str,
) -> Path:
    """Save trajectory summaries as JSON for future reference."""
    traj_dir = Path(output_dir).expanduser() / "trajectories"
    traj_dir.mkdir(parents=True, exist_ok=True)

    data = [t.model_dump() for t in trajectories]
    today = datetime.now().strftime("%Y-%m-%d")
    path = traj_dir / f"{project}_{today}.json"

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return path
