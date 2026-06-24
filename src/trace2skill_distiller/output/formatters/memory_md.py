"""Memory markdown formatter for session review outputs."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ...analysis.types import SkillRule, TopicSkill


MEMORY_TYPE_LABELS = {
    "USER_PREFERENCE": "User Preferences",
    "STANDING_REQUIREMENT": "Standing Requirements",
    "REPO_FACT": "Repository Facts",
    "WORKFLOW_PATTERN": "Workflow Patterns",
    "KNOWLEDGE_DISCOVERY": "Knowledge Discoveries",
    "CORRECTION": "Corrections",
    "TOOL_FEEDBACK": "Tool / Skill Feedback",
    "PITFALL": "Pitfalls",
    "OPEN_QUESTION": "Open Questions",
}

MEMORY_TYPE_ORDER = list(MEMORY_TYPE_LABELS)

STORE_FILENAME = "memory_store.json"
AGENT_CONTEXT_FILENAME = "agent-context.md"
USER_PROFILE_FILENAME = "user-profile.md"
REPO_FACTS_FILENAME = "repo-facts.md"


def memory_store_path(output_dir: Path, project: str) -> Path:
    """Return the persistent memory store path for a project."""
    return Path(output_dir).expanduser() / project / STORE_FILENAME


def load_memory_store(output_dir: Path, project: str) -> dict:
    """Load a project memory store, returning an empty store when absent."""
    path = memory_store_path(output_dir, project)
    if not path.exists():
        return {
            "version": 1,
            "project": project,
            "updated_at": "",
            "items": [],
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "version": 1,
            "project": project,
            "updated_at": "",
            "items": [],
        }
    data.setdefault("version", 1)
    data.setdefault("project", project)
    data.setdefault("updated_at", "")
    data.setdefault("items", [])
    return data


def summarize_memory_quality(store: dict) -> dict:
    """Return explainable health metrics for a memory store."""
    items = store.get("items", [])
    active_items = [item for item in items if item.get("status", "active") != "archived"]
    total = len(active_items)
    agent_ready = _agent_ready_items(store)
    review_items = [item for item in active_items if item.get("status") == "review"]
    open_questions = [item for item in active_items if item.get("type") == "OPEN_QUESTION"]
    conflicted = [item for item in active_items if item.get("conflict_with")]
    missing_evidence = [
        item for item in active_items
        if not item.get("evidence") and not item.get("confirmed")
    ]
    evidence_backed = [
        item for item in active_items
        if item.get("evidence") or item.get("confirmed")
    ]
    reinforced = [
        item for item in active_items
        if int(item.get("seen_count", 1) or 1) > 1 or item.get("confirmed")
    ]

    if not total:
        score = 0
        label = "empty"
    else:
        score = round(
            45 * len(agent_ready) / total
            + 20 * len(evidence_backed) / total
            + 15 * len(reinforced) / total
            + 10 * (1 - len(review_items) / total)
            + 10 * (1 - len(conflicted) / total)
        )
        score = max(0, min(100, score))
        if score >= 80:
            label = "healthy"
        elif score >= 60:
            label = "usable"
        elif score >= 35:
            label = "needs review"
        else:
            label = "thin"

    next_actions: list[str] = []
    if conflicted:
        next_actions.append(f"Resolve {len(conflicted)} conflicting memory item(s).")
    if missing_evidence:
        next_actions.append(f"Add evidence or manually confirm {len(missing_evidence)} memory item(s).")
    if open_questions:
        next_actions.append(f"Answer or archive {len(open_questions)} open question(s).")
    if review_items:
        next_actions.append(f"Review {len(review_items)} queued memory item(s).")
    if total and len(agent_ready) == 0:
        next_actions.append("Promote at least one confirmed, evidence-backed memory into agent context.")
    if not next_actions:
        next_actions.append("No immediate review action is needed.")

    return {
        "score": score,
        "label": label,
        "total": total,
        "agent_ready": len(agent_ready),
        "review": len(review_items),
        "open_questions": len(open_questions),
        "conflict": len(conflicted),
        "missing_evidence": len(missing_evidence),
        "evidence_backed": len(evidence_backed),
        "reinforced": len(reinforced),
        "next_actions": next_actions,
    }


def write_memory(
    skills: list[TopicSkill],
    output_dir: Path,
    project: str,
    *,
    agent_context_path: str = "",
    user_profile_path: str = "",
    repo_facts_path: str = "",
) -> Path:
    """Write a consolidated memory review file."""
    output_dir = Path(output_dir).expanduser()
    project_dir = output_dir / project
    project_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now().isoformat(timespec="seconds")
    store = _merge_memory_store(load_memory_store(output_dir, project), skills, now)
    return refresh_memory_files(
        output_dir,
        project,
        store,
        skills,
        agent_context_path=agent_context_path,
        user_profile_path=user_profile_path,
        repo_facts_path=repo_facts_path,
    )


def refresh_memory_files(
    output_dir: Path,
    project: str,
    store: dict | None = None,
    skills: list[TopicSkill] | None = None,
    *,
    agent_context_path: str = "",
    user_profile_path: str = "",
    repo_facts_path: str = "",
) -> Path:
    """Persist a memory store and regenerate all derived memory artifacts.

    The three derived .md files (agent-context / user-profile / repo-facts)
    default to ``<output_dir>/<project>/<fixed name>``. If a custom path is
    provided and points to an *already existing* file, the newly distilled
    memories are appended to it (preserving any pre-existing human-written
    content) instead of overwriting it.
    """
    output_dir = Path(output_dir).expanduser()
    project_dir = output_dir / project
    project_dir.mkdir(parents=True, exist_ok=True)

    store = store or load_memory_store(output_dir, project)
    if not store.get("updated_at"):
        store["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _write_memory_store(output_dir, project, store)
    _write_agent_context(project_dir, project, store, agent_context_path)
    _write_user_profile(project_dir, project, store, user_profile_path)
    _write_repo_facts(project_dir, project, store, repo_facts_path)

    skills = skills or []
    today = (store.get("updated_at") or datetime.now().isoformat(timespec="seconds"))[:10]

    all_items = store["items"]
    total_sessions = len({sid for item in all_items for sid in item.get("source_sessions", [])})
    by_type: dict[str, list[dict]] = defaultdict(list)
    for item in all_items:
        if item.get("status", "active") != "archived":
            by_type[_normalize_type(item.get("type", ""))].append(item)
    quality = summarize_memory_quality(store)

    lines = [
        f"# Memory Review - {project}",
        "",
        f"> Updated: {today} | Topics: {len(skills)} | Memories: {len(all_items)} | Sessions: {total_sessions}",
        "",
        "This file is meant to become long-term working memory for future AI coding sessions.",
        "Keep items that are specific, evidenced, and useful; verify or remove weak memories later.",
        "",
        "## Executive Summary",
        "",
    ]

    if not skills:
        lines.append("No memory topics were distilled.")
    else:
        for skill in skills:
            summary = skill.summary or skill.description or skill.skill_title
            lines.append(f"- **{skill.skill_title}**: {summary}")
    lines.append("")

    lines.extend([
        "## Memory Quality",
        "",
        f"Readiness score: **{quality['score']}/100** ({quality['label']}).",
        "",
        "| Metric | Count |",
        "| --- | ---: |",
        f"| Agent-ready memories | {quality['agent_ready']} |",
        f"| Evidence-backed or confirmed | {quality['evidence_backed']} |",
        f"| Reinforced by repeat sightings or confirmation | {quality['reinforced']} |",
        f"| Needs review | {quality['review']} |",
        f"| Conflicts | {quality['conflict']} |",
        f"| Missing evidence | {quality['missing_evidence']} |",
        f"| Open questions | {quality['open_questions']} |",
        "",
        "Next actions:",
    ])
    lines.extend(f"- {action}" for action in quality["next_actions"])
    lines.append("")

    lines.extend([
        "## Memory Map",
        "",
        "| Type | Count | What It Means |",
        "| --- | ---: | --- |",
    ])
    for mem_type in MEMORY_TYPE_ORDER:
        label = MEMORY_TYPE_LABELS[mem_type]
        lines.append(f"| {label} | {len(by_type.get(mem_type, []))} | {_type_hint(mem_type)} |")
    extra_types = sorted(t for t in by_type if t not in MEMORY_TYPE_LABELS)
    for mem_type in extra_types:
        lines.append(f"| {mem_type.title()} | {len(by_type[mem_type])} | Unclassified memory type. |")
    lines.append("")

    for mem_type in MEMORY_TYPE_ORDER + extra_types:
        entries = by_type.get(mem_type, [])
        if not entries:
            continue
        lines.append(f"## {MEMORY_TYPE_LABELS.get(mem_type, mem_type.title())}")
        lines.append("")
        for item in entries:
            lines.extend(_format_memory_item(item))
        lines.append("")

    open_questions = by_type.get("OPEN_QUESTION", [])
    weak_items = [
        item for item in all_items
        if item.get("confidence", 0) and item.get("confidence", 0) < 0.55
    ]
    missing_evidence_items = [
        item for item in all_items
        if item.get("status") != "archived"
        and not item.get("evidence")
        and not item.get("confirmed")
    ]
    conflicted_items = [
        item for item in all_items
        if item.get("conflict_with")
    ]
    lines.extend([
        "## Review Queue",
        "",
    ])
    if not open_questions and not weak_items and not conflicted_items and not missing_evidence_items:
        lines.append("- No open questions or low-confidence memories were found.")
    else:
        for item in open_questions:
            lines.append(f"- Verify: {item.get('action', '')} ({_topics_label(item)})")
        for item in weak_items:
            lines.append(
                f"- Re-check low confidence memory: {item.get('action', '')} "
                f"({item.get('confidence', 0):.2f}, {_topics_label(item)})"
            )
        for item in conflicted_items:
            lines.append(
                f"- Resolve conflict: {item.get('action', '')} "
                f"(conflicts with {', '.join(item.get('conflict_with', []))})"
            )
        for item in missing_evidence_items:
            lines.append(f"- Add evidence or confirm manually: {item.get('action', '')}")
    lines.append("")

    lines.extend([
        "## Source Topics",
        "",
    ])
    for skill in skills:
        sessions = ", ".join(skill.source_sessions) or "unknown"
        lines.append(f"- **{skill.skill_title}** ({skill.topic_id}): {sessions}")
    lines.append("")
    lines.append("---")
    lines.append("Generated by trace2skill dream.")

    path = project_dir / "memory.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _resolve_target(
    custom_path: str,
    project: str,
    project_dir: Path,
    default_name: str,
) -> tuple[Path, bool]:
    """Resolve where a derived .md file should land.

    Returns ``(path, append_mode)``. ``append_mode`` is True only when the user
    configured a custom path that already exists on disk — in that case we
    append fresh memories instead of overwriting the file.
    """
    if not custom_path or not str(custom_path).strip():
        return project_dir / default_name, False
    expanded = Path(custom_path.replace("{project}", project or "general")).expanduser()
    exists = expanded.exists()
    expanded.parent.mkdir(parents=True, exist_ok=True)
    return expanded, exists


def _fresh_items(store: dict) -> list[dict]:
    """Return store items whose last_seen matches this run's updated_at.

    These are the items newly added or refreshed in the current pass, i.e. the
    only items worth appending when writing into a pre-existing user file.
    """
    updated_at = store.get("updated_at", "")
    return [
        item for item in store.get("items", [])
        if item.get("last_seen") == updated_at
        and item.get("status", "active") != "archived"
    ]


def _write_agent_context(
    project_dir: Path,
    project: str,
    store: dict,
    custom_path: str = "",
) -> Path:
    """Write compact, high-confidence memory for direct agent context injection."""
    path, append_mode = _resolve_target(custom_path, project, project_dir, AGENT_CONTEXT_FILENAME)
    items = _agent_ready_items(store)
    lines = [
        f"# Agent Context - {project}",
        "",
        "Use these memories as compact working context for future coding sessions.",
        "Treat low-confidence or missing-evidence memories as hints, not facts.",
        "",
    ]

    if not items:
        lines.append("No high-confidence agent-ready memories yet.")
    else:
        for mem_type in [
            "USER_PREFERENCE",
            "STANDING_REQUIREMENT",
            "REPO_FACT",
            "WORKFLOW_PATTERN",
            "KNOWLEDGE_DISCOVERY",
            "CORRECTION",
            "TOOL_FEEDBACK",
            "PITFALL",
        ]:
            typed = [item for item in items if item.get("type") == mem_type]
            if not typed:
                continue
            lines.append(f"## {MEMORY_TYPE_LABELS[mem_type]}")
            lines.append("")
            for item in typed[:20]:
                condition = item.get("condition")
                prefix = f"When {condition}: " if condition else ""
                lines.append(f"- {prefix}{item.get('action', '')}")
            lines.append("")

    _emit(path, lines, append_mode, project)
    return path


def _emit(path: Path, lines: list[str], append_mode: bool, project: str) -> None:
    """Write or append the rendered markdown to ``path``.

    In append mode, the new content is written under a dated section header so
    repeated runs accumulate without clobbering whatever the user previously
    kept in that file.
    """
    body = "\n".join(lines).rstrip() + "\n"
    if not append_mode:
        path.write_text(body, encoding="utf-8")
        return
    today = datetime.now().strftime("%Y-%m-%d")
    header = f"\n\n---\n\n## Appended from {project} ({today})\n\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(header + body)


def _write_user_profile(
    project_dir: Path,
    project: str,
    store: dict,
    custom_path: str = "",
) -> Path:
    """Write stable user-specific memories separately."""
    path, append_mode = _resolve_target(custom_path, project, project_dir, USER_PROFILE_FILENAME)
    items = [
        item for item in _agent_ready_items(store)
        if item.get("type") in {"USER_PREFERENCE", "STANDING_REQUIREMENT"}
    ]
    lines = [
        f"# User Profile - {project}",
        "",
        "Stable user preferences and standing requirements learned from reviewed sessions.",
        "",
    ]
    if not items:
        lines.append("No stable user profile memories yet.")
    else:
        for item in items:
            lines.append(f"- **{MEMORY_TYPE_LABELS[item['type']]}**: {item.get('action', '')}")
    _emit(path, lines, append_mode, project)
    return path


def _write_repo_facts(
    project_dir: Path,
    project: str,
    store: dict,
    custom_path: str = "",
) -> Path:
    """Write repository and workflow memories separately."""
    repo_types = {
        "REPO_FACT",
        "WORKFLOW_PATTERN",
        "KNOWLEDGE_DISCOVERY",
        "CORRECTION",
        "PITFALL",
    }
    items = [item for item in _agent_ready_items(store) if item.get("type") in repo_types]
    path, append_mode = _resolve_target(custom_path, project, project_dir, REPO_FACTS_FILENAME)
    lines = [
        f"# Repository Memory - {project}",
        "",
        "Project facts, workflow patterns, corrections, and pitfalls learned from reviewed sessions.",
        "",
    ]
    if not items:
        lines.append("No stable repository memories yet.")
    else:
        for mem_type in MEMORY_TYPE_ORDER:
            typed = [item for item in items if item.get("type") == mem_type]
            if not typed:
                continue
            lines.append(f"## {MEMORY_TYPE_LABELS[mem_type]}")
            lines.append("")
            for item in typed:
                lines.append(f"- {item.get('action', '')}")
            lines.append("")
    _emit(path, lines, append_mode, project)
    return path


def _merge_memory_store(store: dict, skills: list[TopicSkill], now: str) -> dict:
    existing = {
        item.get("id") or _memory_id(item.get("type", ""), item.get("scope", ""), item.get("action", "")): item
        for item in store.get("items", [])
    }

    for skill in skills:
        for rule in skill.rules:
            item = _rule_to_item(skill, rule, now)
            previous = existing.get(item["id"])
            if previous:
                _merge_item(previous, item, now)
            else:
                conflicts = _detect_conflicts(item, existing.values())
                if conflicts:
                    item["status"] = "review"
                    item["conflict_with"] = conflicts
                    item["conflict_reason"] = "Possible contradiction with existing memory in the same type/scope."
                existing[item["id"]] = item

    items = sorted(
        existing.values(),
        key=lambda item: (
            MEMORY_TYPE_ORDER.index(_normalize_type(item.get("type", "")))
            if _normalize_type(item.get("type", "")) in MEMORY_TYPE_ORDER else 999,
            item.get("action", ""),
        ),
    )
    store["version"] = 1
    store["updated_at"] = now
    store["items"] = items
    return store


def _write_memory_store(output_dir: Path, project: str, store: dict) -> Path:
    path = memory_store_path(output_dir, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _agent_ready_items(store: dict) -> list[dict]:
    """Return active memories that are strong enough for future agent context."""
    items = [
        item for item in store.get("items", [])
        if item.get("status", "active") == "active"
        and item.get("type") != "OPEN_QUESTION"
        and float(item.get("confidence", 0) or 0) >= 0.55
        and (item.get("evidence") or item.get("confirmed"))
    ]
    return sorted(
        items,
        key=lambda item: (
            MEMORY_TYPE_ORDER.index(_normalize_type(item.get("type", "")))
            if _normalize_type(item.get("type", "")) in MEMORY_TYPE_ORDER else 999,
            -float(item.get("confidence", 0) or 0),
            -int(item.get("seen_count", 1) or 1),
            item.get("action", ""),
        ),
    )


def _rule_to_item(skill: TopicSkill, rule: SkillRule, now: str) -> dict:
    mem_type = _normalize_type(rule.type)
    scope = rule.scope or "general"
    evidence = _dedupe(rule.evidence_from_success + rule.evidence_from_failure)
    item = {
        "id": _memory_id(mem_type, scope, rule.action),
        "type": mem_type,
        "action": rule.action,
        "condition": rule.condition,
        "scope": scope,
        "confidence": rule.confidence,
        "evidence": evidence,
        "source_sessions": list(skill.source_sessions),
        "source_topics": [skill.skill_title],
        "first_seen": now,
        "last_seen": now,
        "seen_count": 1,
        "confirmed": False,
        "status": "review" if mem_type == "OPEN_QUESTION" or rule.confidence < 0.55 or not evidence else "active",
    }
    return item


def _merge_item(previous: dict, incoming: dict, now: str) -> None:
    previous["last_seen"] = now
    previous["seen_count"] = int(previous.get("seen_count", 1)) + 1
    previous["confidence"] = max(float(previous.get("confidence", 0)), float(incoming.get("confidence", 0)))
    previous["evidence"] = _dedupe(previous.get("evidence", []) + incoming.get("evidence", []))[:8]
    previous["source_sessions"] = _dedupe(previous.get("source_sessions", []) + incoming.get("source_sessions", []))
    previous["source_topics"] = _dedupe(previous.get("source_topics", []) + incoming.get("source_topics", []))
    if incoming.get("condition") and not previous.get("condition"):
        previous["condition"] = incoming["condition"]
    if previous["confidence"] >= 0.55 and previous.get("type") != "OPEN_QUESTION":
        previous["status"] = "active"


def _detect_conflicts(item: dict, existing_items) -> list[str]:
    """Find likely contradictions against existing active memories.

    This is intentionally conservative: it only checks same type/scope memories
    with overlapping words and opposite polarity cues.
    """
    if item.get("type") == "OPEN_QUESTION":
        return []
    action = item.get("action", "")
    polarity = _polarity(action)
    if polarity == 0:
        return []
    item_terms = _content_terms(action)
    if not item_terms:
        return []

    conflicts = []
    for other in existing_items:
        if other.get("status") == "archived":
            continue
        if other.get("type") != item.get("type"):
            continue
        if other.get("scope") != item.get("scope"):
            continue
        other_polarity = _polarity(other.get("action", ""))
        if other_polarity == 0 or other_polarity == polarity:
            continue
        overlap = item_terms & _content_terms(other.get("action", ""))
        if len(overlap) >= 2:
            conflicts.append(other.get("id", ""))
    return [cid for cid in conflicts if cid]


def _polarity(text: str) -> int:
    lowered = f" {text.lower()} "
    negative_markers = [
        " not ",
        " never ",
        " avoid ",
        " don't ",
        " do not ",
        " should not ",
        " must not ",
        " no longer ",
        " without ",
        "禁止",
        "不要",
        "避免",
        "不应该",
        "不能",
    ]
    positive_markers = [
        " always ",
        " prefer ",
        " should ",
        " must ",
        " use ",
        " keep ",
        " require ",
        "需要",
        "应该",
        "必须",
        "使用",
        "优先",
    ]
    if any(marker in lowered for marker in negative_markers) or any(marker in text for marker in negative_markers):
        return -1
    if any(marker in lowered for marker in positive_markers) or any(marker in text for marker in positive_markers):
        return 1
    return 0


def _content_terms(text: str) -> set[str]:
    stop_words = {
        "the", "and", "for", "with", "that", "this", "when", "from", "into",
        "should", "must", "prefer", "always", "never", "avoid", "use", "not",
        "memory", "review", "user", "agent", "context",
    }
    terms = {
        token for token in re.findall(r"[a-zA-Z0-9_./-]{3,}", text.lower())
        if token not in stop_words
    }
    # Keep simple CJK chunks as terms for Chinese memories.
    terms.update(re.findall(r"[\u4e00-\u9fff]{2,}", text))
    return terms


def _format_memory_item(item: dict) -> list[str]:
    confidence_value = item.get("confidence", 0)
    confidence = f"{confidence_value:.2f}" if confidence_value else "unknown"
    scope = item.get("scope") or "general"
    lines = [
        f"### {item.get('action', '')}",
        "",
        f"- Topics: {_topics_label(item)}",
        f"- Scope: {scope}",
        f"- Confidence: {confidence}",
        f"- Seen: {item.get('seen_count', 1)} time(s)",
        f"- Status: {item.get('status', 'active')}",
    ]
    if item.get("condition"):
        lines.append(f"- Applies when: {item['condition']}")
    evidence = item.get("evidence", [])
    if evidence:
        lines.append("- Evidence:")
        for item in evidence[:3]:
            lines.append(f"  - {item}")
    else:
        lines.append("- Evidence: missing; verify before relying on this memory.")
    lines.append("")
    return lines


def _memory_id(mem_type: str, scope: str, action: str) -> str:
    normalized = " ".join((action or "").lower().split())
    raw = f"{_normalize_type(mem_type)}|{scope or 'general'}|{normalized}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _topics_label(item: dict) -> str:
    topics = item.get("source_topics", [])
    return ", ".join(topics) if topics else "unknown"


def _normalize_type(value: str) -> str:
    normalized = (value or "KNOWLEDGE_DISCOVERY").strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "FACT": "REPO_FACT",
        "ALWAYS": "STANDING_REQUIREMENT",
        "WHEN_THEN": "WORKFLOW_PATTERN",
        "AVOID": "PITFALL",
        "NEVER": "PITFALL",
    }
    return aliases.get(normalized, normalized)


def _type_hint(mem_type: str) -> str:
    return {
        "USER_PREFERENCE": "How the user likes the assistant to work or communicate.",
        "STANDING_REQUIREMENT": "Rules future sessions should keep following.",
        "REPO_FACT": "Concrete facts about this repository or environment.",
        "WORKFLOW_PATTERN": "Repeatable investigation or implementation behavior.",
        "KNOWLEDGE_DISCOVERY": "Technical or domain knowledge worth retaining.",
        "CORRECTION": "Wrong assumptions that were corrected.",
        "TOOL_FEEDBACK": "Feedback about tools, skills, prompts, or commands.",
        "PITFALL": "Failure modes and time-wasters to avoid.",
        "OPEN_QUESTION": "Things that need later verification.",
    }.get(mem_type, "Additional memory category.")
