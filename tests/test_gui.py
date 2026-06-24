"""Tests for the local GUI service helpers."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

from trace2skill_distiller.core.config import DistillConfig, OutputConfig, load_dotenv
from trace2skill_distiller.gui.services import (
    _write_env,
    config_view as _config_view,
    friendly_error as _friendly_error,
    run_status_payload as _run_status_payload,
    save_config as _save_config,
    update_memory_item as _update_memory_item,
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
    with patch("trace2skill_distiller.gui.services.create_source", return_value=_GuiSource()):
        rows = session_rows(cfg, project="demo", limit=10)

    assert [row["id"] for row in rows] == ["new", "old"]
    assert rows[1]["tools"] == 2
    assert rows[1]["eligible"] is False
    assert "工具少于" in rows[1]["hint"]
    assert rows[0]["title"] == "New work"
    assert rows[0]["eligible"] is True


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
    assert "沉淀" in snapshot["summary"]
    assert snapshot["learned_items"][0]["status"] in {"可直接使用", "需要确认"}
    assert {group["id"] for group in snapshot["groups"]} == {"keep", "review", "discard"}
    assert snapshot["learned_items"][0]["action"]
    assert snapshot["review_items"][0]["reason"] == "开放问题"


def test_update_memory_item_confirm_and_archive(tmp_path):
    cfg = DistillConfig(output=OutputConfig(skill_output_dir=str(tmp_path)))
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    (project_dir / "memory_store.json").write_text(
        json.dumps({
            "version": 1,
            "project": "demo",
            "items": [
                {
                    "id": "review01",
                    "type": "WORKFLOW_PATTERN",
                    "action": "Ask what file to inspect first.",
                    "confidence": 0.7,
                    "status": "review",
                    "evidence": ["User wanted clearer file workflow."],
                },
                {
                    "id": "fact01",
                    "type": "REPO_FACT",
                    "action": "One-off file list.",
                    "confidence": 0.6,
                    "status": "active",
                    "scope": "project-specific",
                    "evidence": ["Listed once."],
                },
            ],
        }),
        encoding="utf-8",
    )

    confirmed = _update_memory_item(cfg, {"project": "demo", "id": "review01", "action": "confirm"})
    confirmed_item = next(item for group in confirmed["groups"] for item in group["items"] if item["id"] == "review01")
    assert confirmed_item["ai_use"] == "已确认，可进入长期上下文"

    archived = _update_memory_item(cfg, {"project": "demo", "id": "fact01", "action": "archive"})
    assert all(item["id"] != "fact01" for group in archived["groups"] for item in group["items"])


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
    assert view["output"]["skill_output_dir"] == cfg.output.skill_output_dir
    assert "agent_context_path" in view["output"]
    assert "user_profile_path" in view["output"]
    assert "repo_facts_path" in view["output"]


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
        "output_skill_output_dir": "~/.trace2skill/skills",
        "output_agent_context_path": "",
        "output_user_profile_path": "",
        "output_repo_facts_path": "",
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


class _Report:
    def __init__(self, sessions: int, topics: int, rules: int):
        self.sessions_passed_filter = sessions
        self.topics_found = topics
        self.total_rules = rules


def test_run_status_payload_distinguishes_empty_results():
    no_candidates = _run_status_payload(_Report(sessions=0, topics=0, rules=0))
    assert no_candidates["status"] == "no_candidates"
    assert "没有生成结果" in no_candidates["message"]
    assert "筛选" in no_candidates["action"]

    no_rules = _run_status_payload(_Report(sessions=1, topics=0, rules=0))
    assert no_rules["status"] == "no_rules"
    assert "没有生成记忆" in no_rules["message"]

    ok = _run_status_payload(_Report(sessions=2, topics=1, rules=3))
    assert ok["status"] == "ok"
    assert "提取完成" in ok["message"]


def test_gui_extract_decision_copy_matches_legacy_text():
    # The decision-button copy that drove the old HTML now lives in qt_app;
    # keep the user-facing wording stable across the rewrite.
    from trace2skill_distiller.gui.qt_app import _DECISION_COPY

    assert _DECISION_COPY["confirm"]["label"] == "保存到记忆文件"
    assert _DECISION_COPY["confirm"]["effect"] == "写入 {target}，后续 AI 会按这条记忆工作。"
    assert _DECISION_COPY["review"]["label"] == "放入待确认"
    assert _DECISION_COPY["review"]["effect"] == "留在待确认区，暂不写入 {target}。"
    assert _DECISION_COPY["archive"]["label"] == "不保存"
    assert _DECISION_COPY["archive"]["effect"] == "从有效记忆里移除，不写入 {target}。"
    # {target} interpolates to the agent-context filename.
    assert "后续 AI" in _DECISION_COPY["confirm"]["done"].format(target="agent-context.md")
