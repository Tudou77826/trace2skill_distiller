"""Step 2: Big-LLM distillation — extract skill rules from trajectory summaries."""

from __future__ import annotations

import json
from typing import Any

from ..llm import LLMClient, estimate_tokens, truncate_to_token_budget
from ..models import TrajectorySummary, SkillPatch, SkillRule

DIMENSIONS = {
    "architecture": {
        "focus": "项目结构探索、模块划分、依赖管理、文件组织",
        "tools_of_interest": ["glob", "read", "grep"],
    },
    "implementation": {
        "focus": "代码编写策略、工具选择、编辑模式（write vs edit）",
        "tools_of_interest": ["write", "edit", "bash"],
    },
    "debugging": {
        "focus": "问题定位、错误处理、修复策略、重试模式",
        "tools_of_interest": ["bash", "read", "edit"],
    },
    "testing": {
        "focus": "测试策略、验证方法、质量保障",
        "tools_of_interest": ["bash", "write", "edit"],
    },
}

DISTILL_SYSTEM = """你是一个高级软件工程师，擅长从开发轨迹中提炼可复用的最佳实践。
请分析提供的成功和失败轨迹，提炼出 Skill 规则补丁。"""

DISTILL_PROMPT = """## 背景
正在分析 {project} 项目的开发轨迹，分析维度: {dimension}
维度说明: {focus}

## 成功轨迹 (T⁺)
{t_plus}

## 失败/问题轨迹 (T⁻)
{t_minus}

## 任务
基于以上轨迹，提炼出 {dimension} 维度的 Skill 规则补丁。

每条规则类型：
- **ALWAYS**: 总是应该这样做（来自 T⁺ 的共识模式）
- **WHEN_THEN**: 当满足某条件时，执行某动作（来自 T⁺ 的条件模式）
- **NEVER**: 永远不要这样做（来自 T⁻ 的失败教训）
- **AVOID**: 尽量避免这样做（来自 T⁻ 的问题模式）

严格输出 JSON：
{{
  "dimension": "{dimension}",
  "project": "{project}",
  "rules": [
    {{
      "id": "rule_xxx",
      "type": "ALWAYS|WHEN_THEN|NEVER|AVOID",
      "condition": "触发条件（WHEN_THEN 类型必填，其他为空）",
      "action": "应该做什么 / 不应该做什么",
      "evidence_success": ["支持此规则的 T⁺ 证据"],
      "evidence_failure": ["反向验证此规则的 T⁻ 证据"],
      "confidence": 0.8,
      "scope": "general|project-specific|language-specific"
    }}
  ]
}}"""


def distill_dimension(
    trajectories: list[TrajectorySummary],
    dimension: str,
    project: str,
    llm: LLMClient,
) -> SkillPatch | None:
    """Run distillation for a single dimension."""

    dim_config = DIMENSIONS.get(dimension)
    if not dim_config:
        return None

    # Separate T+ and T-
    t_plus = [t for t in trajectories if t.label == "success"]
    t_minus = [t for t in trajectories if t.label in ("failure", "partial")]

    if not t_plus and not t_minus:
        return None

    # Format summaries
    t_plus_text = _format_trajectories(t_plus)
    t_minus_text = _format_trajectories(t_minus)

    # Truncate to token budget (split budget between T+ and T-)
    budget = 50000 - estimate_tokens(DISTILL_SYSTEM)
    half_budget = budget // 2
    t_plus_text = truncate_to_token_budget(t_plus_text, half_budget)
    t_minus_text = truncate_to_token_budget(t_minus_text, half_budget)

    result = llm.chat_json_with_retry(
        DISTILL_SYSTEM,
        DISTILL_PROMPT.format(
            project=project,
            dimension=dimension,
            focus=dim_config["focus"],
            t_plus=t_plus_text or "（无）",
            t_minus=t_minus_text or "（无）",
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

    return SkillPatch(
        dimension=dimension,
        project=project,
        rules=rules,
    )


def distill_all_dimensions(
    trajectories: list[TrajectorySummary],
    project: str,
    llm: LLMClient,
) -> list[SkillPatch]:
    """Run distillation across all dimensions, with error isolation."""
    patches = []
    for dim in DIMENSIONS:
        console_print = f"  Distilling dimension: {dim}..."
        try:
            patch = distill_dimension(trajectories, dim, project, llm)
            if patch and patch.rules:
                patches.append(patch)
                console_print += f" {len(patch.rules)} rules found"
            else:
                console_print += " skipped (no data)"
        except Exception as e:
            console_print += f" [red]error: {e}[/]"
        from rich.console import Console
        Console().print(console_print)
    return patches


def _format_trajectories(trajectories: list[TrajectorySummary]) -> str:
    """Format trajectory summaries for the distillation prompt."""
    parts = []
    for t in trajectories:
        entry = f"### Session: {t.session_id}\n"
        entry += f"类型: {t.session_type} | 标签: {t.label}\n"
        entry += f"意图: {t.intent}\n"

        if t.what_happened:
            entry += "阶段:\n"
            for phase in t.what_happened:
                entry += f"  - {phase.phase}: {phase.summary}\n"

        if t.problems_encountered:
            entry += "问题:\n"
            for p in t.problems_encountered:
                entry += f"  - {p.problem} → {p.how_resolved}\n"

        if t.key_decisions:
            entry += "关键决策:\n"
            for d in t.key_decisions:
                entry += f"  - {d.decision} (原因: {d.rationale})\n"

        if t.lessons_learned:
            entry += "经验教训: " + "; ".join(t.lessons_learned) + "\n"

        parts.append(entry)

    return "\n---\n".join(parts)
