"""Tests for the local GUI service helpers."""

from __future__ import annotations

import json
from unittest.mock import patch

from trace2skill_distiller.core.config import DistillConfig, OutputConfig
from trace2skill_distiller.gui.server import memory_snapshot, session_rows
from trace2skill_distiller.mining.types import SessionMeta


class _GuiSource:
    def list_sessions(self, project=None, since=None):
        return [
            SessionMeta(id="old", title="Old work", project="demo", msg_count=8, tool_count=0, timestamp=1),
            SessionMeta(id="new", title="New work", project="demo", msg_count=12, tool_count=4, timestamp=3),
        ]

    def count_tools(self, session_id):
        return 2 if session_id == "old" else 4


def test_session_rows_are_sorted_and_count_tools():
    cfg = DistillConfig()
    with patch("trace2skill_distiller.gui.server.create_source", return_value=_GuiSource()):
        rows = session_rows(cfg, project="demo", limit=10)

    assert [row["id"] for row in rows] == ["new", "old"]
    assert rows[1]["tools"] == 2
    assert rows[0]["title"] == "New work"


def test_memory_snapshot_returns_quality_and_review_items(tmp_path):
    cfg = DistillConfig(output=OutputConfig(skill_output_dir=str(tmp_path)))
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    (project_dir / "memory_store.json").write_text(
        json.dumps({
            "version": 1,
            "project": "demo",
            "items": [
                {
                    "id": "ready01",
                    "type": "USER_PREFERENCE",
                    "action": "Prefer selected-session review.",
                    "confidence": 0.9,
                    "status": "active",
                    "confirmed": True,
                },
                {
                    "id": "open01",
                    "type": "OPEN_QUESTION",
                    "action": "Confirm whether GUI review should auto-install context.",
                    "confidence": 0.4,
                    "status": "review",
                },
            ],
        }),
        encoding="utf-8",
    )

    snapshot = memory_snapshot(cfg, "demo")

    assert snapshot["quality"]["agent_ready"] == 1
    assert snapshot["quality"]["open_questions"] == 1
    assert snapshot["quality"]["label"] in {"需要复审", "记忆偏薄", "可用", "健康"}
    assert snapshot["review_items"][0]["reason"] == "开放问题"
