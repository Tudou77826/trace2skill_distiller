"""LLM-based skill distillation strategy."""

from __future__ import annotations

from ...llm import LLMClient
from ...core.console import console
from ...core.utils import estimate_tokens, truncate_to_token_budget
from ...mining.types import TrajectorySummary
from ..types import TopicCluster, TopicSkill, SkillRule

DISTILL_SYSTEM = """你是一个高级软件工程师，擅长从开发轨迹中提炼可直接复用的知识点。
你的核心产出是 rules — 每条 rule 必须具体、可操作、有价值，读者可以直接拿去用。"""

DISTILL_PROMPT = """## 输入轨迹

主题：{topic_name}
描述：{topic_summary}

### 成功轨迹 (T+)
{t_plus}

### 失败/问题轨迹 (T-)
{t_minus}

## 输出要求

从轨迹中提炼可直接复用的知识点，输出 JSON。

### rules — 核心产出，必须认真对待

每条 rule 是一个独立的、可直接复用的知识点。读者看到就能直接用，不需要再看其他内容。

规则类型：
- **ALWAYS**: 成功经验，总是应该这样做。action 写具体做法。
- **WHEN_THEN**: 条件经验，满足某条件时执行某动作。condition 写触发条件，action 写具体做法。
- **NEVER**: 失败教训，永远不要这样做。action 写不该做的事。
- **AVOID**: 踩过的坑，尽量避免。action 写要避免的事。
- **FACT**: 探索中发现的事实认知。action 写具体事实（文件路径、数据格式、架构关系、配置细节等）。condition 留空。

rules 质量要求：
- action 必须具体，包含可操作的细节（路径、命令、参数、代码片段等）
- 不要写"应该检查XXX"这种空话，要写具体的路径、文件名、字段名、命令等
- FACT 类型的 action 必须保留原始 Discoveries 中的具体细节，不要泛化、不要总结成一句话
- 每条 FACT 只描述一个事实，不要合并多个事实
- scope 为 project-specific 时 confidence 应相应提高

### 其他字段

- **skill_title**: 中文标题，简洁准确
- **skill_type**: procedure|knowledge|checklist|troubleshooting|reference
- **description**: 英文，格式 "[What it does]. Use when [trigger scenarios]."，限 200 字符
- **summary**: 中文，2-3 句概述
- **body**: 可选的补充 Markdown（如排查步骤、示例代码等）。如果 rules 已经完整则留空。

### 严格输出 JSON：
{{
  "skill_title": "技能标题（中文）",
  "skill_type": "procedure|knowledge|checklist|troubleshooting|reference",
  "description": "English description with trigger words (max 200 chars)",
  "summary": "中文概述（2-3 句）",
  "rules": [
    {{
      "id": "rule_xxx",
      "type": "ALWAYS|WHEN_THEN|NEVER|AVOID|FACT",
      "condition": "触发条件（仅 WHEN_THEN 填写，其他留空）",
      "action": "具体的知识点内容",
      "confidence": 0.8,
      "scope": "general|project-specific|language-specific"
    }}
  ],
  "body": "补充 Markdown（可选，可为空字符串）"
}}"""


class LLMDistillationStrategy:
    """LLM-based skill distillation."""

    def __init__(self, llm: LLMClient):
        self._llm = llm

    def distill_topic(
        self,
        trajectories: list[TrajectorySummary],
        cluster: TopicCluster,
    ) -> TopicSkill | None:
        cluster_ids = set(cluster.session_ids)
        topic_trajs = [t for t in trajectories if t.session_id in cluster_ids]

        if not topic_trajs:
            return None

        t_plus = [t for t in topic_trajs if t.label == "success"]
        t_minus = [t for t in topic_trajs if t.label in ("failure", "partial")]

        if not t_plus and not t_minus:
            return None

        t_plus_text = _format_trajectories(t_plus)
        t_minus_text = _format_trajectories(t_minus)

        budget = 50000 - estimate_tokens(DISTILL_SYSTEM)
        half_budget = budget // 2
        t_plus_text = truncate_to_token_budget(t_plus_text, half_budget)
        t_minus_text = truncate_to_token_budget(t_minus_text, half_budget)

        result = self._llm.chat_json_with_retry(
            DISTILL_SYSTEM,
            DISTILL_PROMPT.format(
                topic_name=cluster.topic_name,
                topic_summary=cluster.topic_summary or cluster.topic_name,
                t_plus=t_plus_text or "(none)",
                t_minus=t_minus_text or "(none)",
            ),
            temperature=0.3,
            max_tokens=8192,
        )

        rules = []
        for r in result.get("rules", []):
            rules.append(
                SkillRule(
                    id=r.get("id", f"rule_{len(rules)}"),
                    type=r.get("type", ""),
                    condition=r.get("condition", ""),
                    action=r.get("action", ""),
                    confidence=r.get("confidence", 0.5),
                    scope=r.get("scope", "general"),
                )
            )

        return TopicSkill(
            topic_id=cluster.topic_id,
            topic_name=cluster.topic_name,
            skill_title=result.get("skill_title", cluster.topic_name),
            skill_type=result.get("skill_type", "checklist"),
            description=result.get("description", ""),
            summary=result.get("summary", cluster.topic_summary),
            rules=rules,
            body=result.get("body", ""),
            source_sessions=cluster.session_ids,
        )

    def distill_all(
        self,
        trajectories: list[TrajectorySummary],
        clusters: list[TopicCluster],
    ) -> list[TopicSkill]:
        skills = []
        for cluster in clusters:
            try:
                console.print(f"  Distilling topic: [cyan]{cluster.topic_name}[/] ({len(cluster.session_ids)} sessions)...")
                skill = self.distill_topic(trajectories, cluster)
                if skill and (skill.rules or skill.body):
                    skills.append(skill)
                    console.print(f"    -> type={skill.skill_type}, {len(skill.rules)} rules, body={len(skill.body)} chars")
                else:
                    console.print(f"    -> skipped (no content)")
            except Exception as e:
                console.print(f"    -> [red]error: {e}[/]")
        return skills


def _format_trajectories(trajectories: list[TrajectorySummary]) -> str:
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

        if t.discoveries:
            entry += "Discoveries:\n"
            for d in t.discoveries:
                entry += f"  - {d}\n"

        parts.append(entry)

    return "\n---\n".join(parts)
