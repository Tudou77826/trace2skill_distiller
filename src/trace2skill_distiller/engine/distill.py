"""Step 2: Per-topic distillation — extract skill rules from trajectory summaries."""

from __future__ import annotations

from rich.console import Console

from ..llm import LLMClient, estimate_tokens, truncate_to_token_budget
from ..models import TrajectorySummary, TopicCluster, TopicSkill, SkillRule

import sys
_console_file = open(sys.stderr.fileno(), mode='w', encoding='utf-8', errors='replace', closefd=False)
console = Console(file=_console_file)

DISTILL_SYSTEM = """你是一个高级软件工程师，擅长从开发轨迹中提炼可复用的最佳实践。
请分析提供的成功和失败轨迹，提炼出 Skill 规则。"""

DISTILL_PROMPT = """## 背景
正在分析主题「{topic_name}」相关的开发轨迹。
主题描述: {topic_summary}

## 成功轨迹 (T+)
{t_plus}

## 失败/问题轨迹 (T-)
{t_minus}

## 任务
基于以上轨迹，提炼出关于「{topic_name}」的可复用技能。

请生成：
1. **skill_title**: 技能标题（简洁，如"JWT 认证配置技能"）
2. **description**: 一句话描述这个技能做什么、何时使用。这是给 AI Agent 自动发现用的，必须包含：(a) 技能解决什么问题 (b) 什么场景下应该触发。例如："Configure JWT authentication for APIs. Use when setting up auth, adding token refresh, or debugging JWT issues."（限 200 字符以内）
3. **summary**: 2-3句话的详细概述
4. 具体规则

每条规则类型：
- **ALWAYS**: 总是应该这样做（来自 T+ 的共识模式）
- **WHEN_THEN**: 当满足某条件时，执行某动作（来自 T+ 的条件模式）
- **NEVER**: 永远不要这样做（来自 T- 的失败教训）
- **AVOID**: 尽量避免这样做（来自 T- 的问题模式）

严格输出 JSON：
{{
  "skill_title": "技能标题",
  "description": "简短描述：做什么 + 何时使用（限 200 字符）",
  "summary": "一段概述",
  "rules": [
    {{
      "id": "rule_xxx",
      "type": "ALWAYS|WHEN_THEN|NEVER|AVOID",
      "condition": "触发条件（WHEN_THEN 类型必填，其他为空）",
      "action": "应该做什么 / 不应该做什么",
      "evidence_success": ["支持此规则的 T+ 证据"],
      "evidence_failure": ["反向验证此规则的 T- 证据"],
      "confidence": 0.8,
      "scope": "general|project-specific|language-specific"
    }}
  ]
}}"""


def distill_topic(
    trajectories: list[TrajectorySummary],
    cluster: TopicCluster,
    llm: LLMClient,
) -> TopicSkill | None:
    """Run distillation for a single topic cluster."""

    # Get trajectories belonging to this cluster
    cluster_ids = set(cluster.session_ids)
    topic_trajs = [t for t in trajectories if t.session_id in cluster_ids]

    if not topic_trajs:
        return None

    # Separate T+ and T-
    t_plus = [t for t in topic_trajs if t.label == "success"]
    t_minus = [t for t in topic_trajs if t.label in ("failure", "partial")]

    if not t_plus and not t_minus:
        return None

    # Format summaries
    t_plus_text = _format_trajectories(t_plus)
    t_minus_text = _format_trajectories(t_minus)

    # Truncate to token budget
    budget = 50000 - estimate_tokens(DISTILL_SYSTEM)
    half_budget = budget // 2
    t_plus_text = truncate_to_token_budget(t_plus_text, half_budget)
    t_minus_text = truncate_to_token_budget(t_minus_text, half_budget)

    result = llm.chat_json_with_retry(
        DISTILL_SYSTEM,
        DISTILL_PROMPT.format(
            topic_name=cluster.topic_name,
            topic_summary=cluster.topic_summary or cluster.topic_name,
            t_plus=t_plus_text or "(none)",
            t_minus=t_minus_text or "(none)",
        ),
        temperature=0.3,
        max_tokens=4096,
    )

    rules = []
    for r in result.get("rules", []):
        rules.append(
            SkillRule(
                id=r.get("id", f"rule_{len(rules)}"),
                type=r.get("type", ""),
                condition=r.get("condition", ""),
                action=r.get("action", ""),
                evidence_from_success=r.get("evidence_success", []),
                evidence_from_failure=r.get("evidence_failure", []),
                confidence=r.get("confidence", 0.5),
                scope=r.get("scope", "general"),
            )
        )

    return TopicSkill(
        topic_id=cluster.topic_id,
        topic_name=cluster.topic_name,
        skill_title=result.get("skill_title", cluster.topic_name),
        description=result.get("description", ""),
        summary=result.get("summary", cluster.topic_summary),
        rules=rules,
        source_sessions=cluster.session_ids,
    )


def distill_all_topics(
    trajectories: list[TrajectorySummary],
    clusters: list[TopicCluster],
    llm: LLMClient,
) -> list[TopicSkill]:
    """Run distillation across all topic clusters."""
    skills = []
    for cluster in clusters:
        try:
            console.print(f"  Distilling topic: [cyan]{cluster.topic_name}[/] ({len(cluster.session_ids)} sessions)...")
            skill = distill_topic(trajectories, cluster, llm)
            if skill and skill.rules:
                skills.append(skill)
                console.print(f"    -> {len(skill.rules)} rules extracted")
            else:
                console.print(f"    -> skipped (no rules)")
        except Exception as e:
            console.print(f"    -> [red]error: {e}[/]")
    return skills


def _format_trajectories(trajectories: list[TrajectorySummary]) -> str:
    """Format trajectory summaries for the distillation prompt."""
    parts = []
    for t in trajectories:
        entry = f"### Session: {t.session_id}\n"
        entry += f"Type: {t.session_type} | Label: {t.label}\n"
        entry += f"Intent: {t.intent}\n"

        if t.what_happened:
            entry += "Phases:\n"
            for phase in t.what_happened:
                entry += f"  - {phase.phase}: {phase.summary}\n"

        if t.problems_encountered:
            entry += "Problems:\n"
            for p in t.problems_encountered:
                entry += f"  - {p.problem} -> {p.how_resolved}\n"

        if t.key_decisions:
            entry += "Decisions:\n"
            for d in t.key_decisions:
                entry += f"  - {d.decision} (reason: {d.rationale})\n"

        if t.lessons_learned:
            entry += "Lessons: " + "; ".join(t.lessons_learned) + "\n"

        parts.append(entry)

    return "\n---\n".join(parts)
