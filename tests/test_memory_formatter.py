"""Tests for memory review output."""

from __future__ import annotations

from trace2skill_distiller.analysis.types import SkillRule, TopicSkill
from trace2skill_distiller.output.formatters.memory_md import (
    load_memory_store,
    summarize_memory_quality,
    write_memory,
)


def test_memory_formatter_groups_types_and_evidence(tmp_path):
    skill = TopicSkill(
        topic_id="review-quality",
        topic_name="Review quality",
        skill_title="会话回顾质量",
        skill_type="memory",
        description="Use when reviewing coding sessions.",
        summary="用户希望回顾不要走马观花，要沉淀多类型记忆。",
        source_sessions=["s1", "s2"],
        rules=[
            SkillRule(
                id="m1",
                type="USER_PREFERENCE",
                action="The user dislikes shallow summaries and wants evidence-backed session review.",
                evidence_from_failure=["用户说输出感觉都白学了，啥也没学着"],
                confidence=0.91,
                scope="user-specific",
            ),
            SkillRule(
                id="m2",
                type="REPO_FACT",
                action="The CLI source lives in src/trace2skill_distiller/cli/main.py.",
                evidence_from_success=["Project scan found the Click CLI entrypoint."],
                confidence=0.8,
                scope="repo-specific",
            ),
            SkillRule(
                id="m3",
                type="OPEN_QUESTION",
                action="Decide whether memories should be promoted into agent-readable SKILL.md files.",
                confidence=0.4,
                scope="project-specific",
            ),
        ],
    )

    path = write_memory([skill], tmp_path, "demo")
    text = path.read_text(encoding="utf-8")

    assert path.name == "memory.md"
    assert "Memory Review - demo" in text
    assert "User Preferences" in text
    assert "Repository Facts" in text
    assert "Open Questions" in text
    assert "Memory Quality" in text
    assert "Readiness score" in text
    assert "Agent-ready memories" in text
    assert "用户说输出感觉都白学了" in text
    assert "Review Queue" in text
    assert "Re-check low confidence memory" in text

    agent_context = (tmp_path / "demo" / "agent-context.md").read_text(encoding="utf-8")
    user_profile = (tmp_path / "demo" / "user-profile.md").read_text(encoding="utf-8")
    repo_facts = (tmp_path / "demo" / "repo-facts.md").read_text(encoding="utf-8")

    assert "The user dislikes shallow summaries" in agent_context
    assert "The CLI source lives" in agent_context
    assert "Decide whether memories" not in agent_context
    assert "The user dislikes shallow summaries" in user_profile
    assert "The CLI source lives" not in user_profile
    assert "The CLI source lives" in repo_facts

    quality = summarize_memory_quality(load_memory_store(tmp_path, "demo"))
    assert quality["agent_ready"] == 2
    assert quality["open_questions"] == 1
    assert quality["review"] == 1
    assert quality["score"] > 0


def test_memory_formatter_merges_existing_store(tmp_path):
    first = TopicSkill(
        topic_id="cli",
        topic_name="CLI",
        skill_title="CLI memory",
        skill_type="memory",
        description="",
        summary="",
        source_sessions=["s1"],
        rules=[
            SkillRule(
                id="m1",
                type="REPO_FACT",
                action="The simple review command is trace2skill dream.",
                evidence_from_success=["The CLI exposes dream as the memory review entrypoint."],
                confidence=0.7,
                scope="repo-specific",
            )
        ],
    )
    second = first.model_copy(deep=True)
    second.source_sessions = ["s2"]
    second.rules[0].evidence_from_success = ["A later run used the same dream command."]
    second.rules[0].confidence = 0.9

    write_memory([first], tmp_path, "demo")
    write_memory([second], tmp_path, "demo")

    store = load_memory_store(tmp_path, "demo")
    assert len(store["items"]) == 1
    item = store["items"][0]
    assert item["seen_count"] == 2
    assert item["confidence"] == 0.9
    assert item["source_sessions"] == ["s1", "s2"]
    assert len(item["evidence"]) == 2


def test_memory_formatter_queues_conflicting_memory(tmp_path):
    first = TopicSkill(
        topic_id="testing",
        topic_name="Testing",
        skill_title="Testing memory",
        skill_type="memory",
        description="",
        summary="",
        source_sessions=["s1"],
        rules=[
            SkillRule(
                id="m1",
                type="WORKFLOW_PATTERN",
                action="Always use pytest for memory formatter tests.",
                evidence_from_success=["Existing test suite uses pytest for formatter tests."],
                confidence=0.9,
                scope="repo-specific",
            )
        ],
    )
    second = first.model_copy(deep=True)
    second.source_sessions = ["s2"]
    second.rules[0].action = "Do not use pytest for memory formatter tests."
    second.rules[0].evidence_from_failure = ["A later session questioned pytest usage for formatter tests."]
    second.rules[0].confidence = 0.95

    write_memory([first], tmp_path, "demo")
    write_memory([second], tmp_path, "demo")

    store = load_memory_store(tmp_path, "demo")
    assert len(store["items"]) == 2
    conflicted = next(item for item in store["items"] if item["action"].startswith("Do not"))
    assert conflicted["status"] == "review"
    assert conflicted["conflict_with"]

    context = (tmp_path / "demo" / "agent-context.md").read_text(encoding="utf-8")
    assert "Always use pytest" in context
    assert "Do not use pytest" not in context

    memory_md = (tmp_path / "demo" / "memory.md").read_text(encoding="utf-8")
    assert "Resolve conflict" in memory_md


def test_memory_formatter_requires_evidence_or_confirmation_for_agent_context(tmp_path):
    skill = TopicSkill(
        topic_id="quality",
        topic_name="Quality",
        skill_title="Quality memory",
        skill_type="memory",
        description="",
        summary="",
        source_sessions=["s1"],
        rules=[
            SkillRule(
                id="m1",
                type="REPO_FACT",
                action="The project has an undocumented hidden behavior.",
                confidence=0.95,
                scope="repo-specific",
            )
        ],
    )

    write_memory([skill], tmp_path, "demo")
    store = load_memory_store(tmp_path, "demo")
    item = store["items"][0]
    assert item["status"] == "review"
    assert item["confirmed"] is False

    context = (tmp_path / "demo" / "agent-context.md").read_text(encoding="utf-8")
    assert "undocumented hidden behavior" not in context

    memory_md = (tmp_path / "demo" / "memory.md").read_text(encoding="utf-8")
    assert "Add evidence or confirm manually" in memory_md
