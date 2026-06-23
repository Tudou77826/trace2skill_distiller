"""Tests for the local GUI service helpers."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

from trace2skill_distiller.core.config import DistillConfig, OutputConfig, load_dotenv
from trace2skill_distiller.gui.server import (
    _config_view,
    _friendly_error,
    _save_config,
    _write_env,
    memory_snapshot,
    session_rows,
)
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


def test_config_view_masks_secrets(tmp_path):
    cfg = DistillConfig(
        output=OutputConfig(skill_output_dir=str(tmp_path)),
    )
    cfg.fast_model.api_key = "sk-super-secret"
    view = _config_view(cfg)
    # Secrets are never returned to the browser.
    assert view["fast"]["api_key_set"] is True
    assert "api_key" not in view["fast"]
    assert view["source"]["type"] == cfg.source.type
    assert view["output"]["format"] == cfg.output.format


def test_load_dotenv_only_loads_prefixed_vars(tmp_path, monkeypatch):
    # Clear both so the real environment can't leak into this assertion
    # (load_dotenv uses setdefault, so already-set vars win).
    monkeypatch.delenv("TRACE2SKILL_API_KEY", raising=False)
    monkeypatch.delenv("TRACE2SKILL_BASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TRACE2SKILL_API_KEY=sk-from-env\n"
        "TRACE2SKILL_BASE_URL=https://example.com/v1\n"
        "UNRELATED_SECRET=do-not-load\n",
        encoding="utf-8",
    )
    load_dotenv(env_file)
    assert os.environ["TRACE2SKILL_API_KEY"] == "sk-from-env"
    assert os.environ["TRACE2SKILL_BASE_URL"] == "https://example.com/v1"
    assert "UNRELATED_SECRET" not in os.environ


def test_save_config_writes_yaml_and_env(tmp_path, monkeypatch):
    # Point config/env at a temp home so this test never touches real files.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "trace2skill_distiller.core.config.Path.home",
        lambda: tmp_path,
    )
    for stale in ("TRACE2SKILL_API_KEY", "TRACE2SKILL_BASE_URL"):
        monkeypatch.delenv(stale, raising=False)

    view = _save_config({
        "api_key": "sk-test-key",
        "base_url": "https://api.example.com/v1",
        "fast_model": "gpt-4o-mini",
        "strong_model": "gpt-4o",
        "fast_max_concurrency": 4,
        "strong_max_concurrency": 2,
        "source_type": "claudecode",
        "source_location": "~/.claude/projects",
        "output_format": "memory_md",
        "output_skill_output_dir": "~/.trace2skill/skills",
        "filter_min_messages": 5,
        "filter_min_tools": 3,
        "fast_proxy": "",
        "fast_proxy_bypass": "",
        "fast_verify_ssl": False,
    })

    # config.yaml + .env both written under the temp home.
    config_path = tmp_path / ".trace2skill" / "config.yaml"
    env_path = tmp_path / ".trace2skill" / ".env"
    assert config_path.exists()
    assert env_path.exists()
    env_text = env_path.read_text(encoding="utf-8")
    assert "TRACE2SKILL_API_KEY=sk-test-key" in env_text
    # Persisted values are reflected back through the returned view.
    assert view["fast"]["model"] == "gpt-4o-mini"
    assert view["source"]["type"] == "claudecode"


def test_friendly_error_maps_common_failures():
    assert "API Key" in _friendly_error(RuntimeError("Unauthorized 401"))
    assert "Base URL" in _friendly_error(RuntimeError("connection timeout"))
    assert "筛选" in _friendly_error(RuntimeError("no sessions passed filter"))
    assert "提取失败" in _friendly_error(ValueError("something else"))
