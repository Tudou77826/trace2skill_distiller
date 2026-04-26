"""Step 2: Per-topic distillation — extract skill rules from trajectory summaries."""

from __future__ import annotations

from rich.console import Console

from ..llm import LLMClient, estimate_tokens, truncate_to_token_budget
from ..models import TrajectorySummary, TopicCluster, TopicSkill, SkillRule

import sys
_console_file = open(sys.stderr.fileno(), mode='w', encoding='utf-8', errors='replace', closefd=False)
console = Console(file=_console_file)

DISTILL_SYSTEM = """你是一个高级软件工程师，擅长从开发轨迹中提炼可复用的最佳实践。
请分析提供的成功和失败轨迹，提炼出 Skill。"""

DISTILL_PROMPT = """## 背景
正在分析主题「{topic_name}」相关的开发轨迹。
主题描述: {topic_summary}

## 成功轨迹 (T+)
{t_plus}

## 失败/问题轨迹 (T-)
{t_minus}

## 任务
基于以上轨迹，提炼出关于「{topic_name}」的可复用技能。

### Step 1: 判断技能类型
先判断这个技能属于哪种类型：
- **procedure**: 操作流程（安装/部署/配置等有明确步骤的任务）
- **knowledge**: 业务理解（架构调研、项目结构理解等知识性内容）
- **checklist**: 注意事项（调试/排障/安全等规则性内容）
- **troubleshooting**: 调试排障（特定问题的解决路径）
- **reference**: 工具参考（配置项说明、API 用法等）

### Step 2: 生成 description（英文）
description 写法要求：
- **必须用英文**
- 格式: "[What it does]. Use when [scenario 1], [scenario 2], or [scenario 3]."
- 不要写 "helps users" 这种废话
- 必须包含具体触发词，便于 AI Agent 自动发现匹配
- 限 200 字符

好例子: "Install oh-my-opencode CLI with correct model flags. Use when deploying opencode, setting up subscriptions (Claude/OpenAI/Gemini), or configuring z.ai.codingplan."
坏例子: "帮助用户在 CLI 环境中安装 oh-my-opencode 并配置模型参数。"

### Step 3: 生成 body（Markdown 正文，不含 frontmatter）
根据技能类型，用对应格式输出 body：

**procedure 类型：**
## 步骤
1. 第一步描述
2. 第二步描述
...
## 注意事项
- ...

**knowledge 类型：**
## 核心概念
...
## 关键关系
...
## 要点
- ...

**checklist 类型：**
## MUST
- ...
## WHEN → THEN
- ...
## NEVER
- ...

**troubleshooting 类型：**
## 常见问题
### 问题 1: ...
排查路径: ...
解决方案: ...

**reference 类型：**
## 配置项
| 参数 | 说明 | 默认值 |
...
## 示例
...
## 注意事项
- ...

body 内容要求：
- 不要出现 session ID、来源元信息等对消费方无用的内容
- 直接写可操作的知识，不要写"从轨迹中分析得出"之类的元描述
- 正文用中文，但标题和关键术语可用英文

### Step 4: 生成 rules（用于统计报告）
每条规则类型：
- **ALWAYS**: 总是应该这样做（来自 T+ 的共识模式）
- **WHEN_THEN**: 当满足某条件时，执行某动作（来自 T+ 的条件模式）
- **NEVER**: 永远不要这样做（来自 T- 的失败教训）
- **AVOID**: 尽量避免这样做（来自 T- 的问题模式）

rules 用于内部统计报告，不需要写得太长。body 已经包含了完整内容。

严格输出 JSON：
{{
  "skill_title": "技能标题（中文）",
  "skill_type": "procedure|knowledge|checklist|troubleshooting|reference",
  "description": "English description with specific trigger words (max 200 chars)",
  "summary": "中文概述（2-3 句）",
  "rules": [
    {{
      "id": "rule_xxx",
      "type": "ALWAYS|WHEN_THEN|NEVER|AVOID",
      "condition": "触发条件（WHEN_THEN 类型必填，其他为空）",
      "action": "应该做什么 / 不应该做什么",
      "confidence": 0.8,
      "scope": "general|project-specific|language-specific"
    }}
  ],
  "body": "完整的 Markdown 正文（根据 skill_type 选择对应格式，不含 YAML frontmatter）"
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
            if skill and (skill.rules or skill.body):
                skills.append(skill)
                console.print(f"    -> type={skill.skill_type}, {len(skill.rules)} rules, body={len(skill.body)} chars")
            else:
                console.print(f"    -> skipped (no content)")
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
