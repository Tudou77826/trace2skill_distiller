"""Level 1 & 2: Quick-LLM semantic processing.

Level 1: Intent boundary detection + per-block structured extraction.
Level 2: Cross-block aggregation into session-level narrative.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..llm import LLMClient, estimate_tokens, truncate_to_token_budget, ContextOverflowError
from ..models import (
    IntentBlock,
    TrajectorySummary,
    PhaseSummary,
    ProblemRecord,
    DecisionRecord,
)
from .preprocess import CleanedSession, format_anchors_for_llm, format_block_for_llm

# Token budgets for different LLM call stages
# Assumes ~128K context models; reserve output tokens + prompt overhead
INPUT_TOKEN_BUDGET = 60000  # max input tokens for the user message
PROMPT_OVERHEAD = 500       # system prompt + template tokens


# ── Level 1: Intent boundary detection ──

BOUNDARY_SYSTEM = """你是一个开发会话分析器。你的任务是识别用户意图的边界。
同一目标下的追问、确认、微调属于同一个意图块。
只有当用户切换到不同的目标或主题时，才需要分割。"""

BOUNDARY_PROMPT = """分析以下开发会话中的用户输入序列，识别意图边界。

用户输入列表（按时间排列）：
{anchors}

请将它们分组为独立的意图块（intent blocks）。
同一目标下的追问（如"继续"、"改一下X"）属于同一块。

严格输出以下 JSON 格式：
{{
  "blocks": [
    {{
      "block_id": 1,
      "message_range": [start_index, end_index],
      "intent": "一句话描述这个意图块的目标"
    }}
  ]
}}

注意：
- message_range 使用消息在列表中的索引（从0开始）
- 相邻的块可以连续，不应有间隙
- 通常 1-3 个用户消息构成一个意图块"""


def detect_intent_boundaries(
    cleaned: CleanedSession, llm: LLMClient
) -> list[IntentBlock]:
    """Level 1a: Use quick-LLM to detect intent boundaries from user anchors."""
    anchors_text = format_anchors_for_llm(cleaned)

    # Short sessions: single block, skip LLM call
    if len(cleaned.user_anchors) <= 2:
        end = cleaned.user_anchors[-1].index if cleaned.user_anchors else 0
        return [
            IntentBlock(
                block_id=1,
                message_range=(0, end),
                intent=cleaned.user_anchors[0].text[:100] if cleaned.user_anchors else "unknown",
            )
        ]

    # Truncate anchors if too long
    budget = INPUT_TOKEN_BUDGET - PROMPT_OVERHEAD - estimate_tokens(BOUNDARY_SYSTEM)
    anchors_text = truncate_to_token_budget(anchors_text, budget)

    result = llm.chat_json_with_retry(
        BOUNDARY_SYSTEM,
        BOUNDARY_PROMPT.format(anchors=anchors_text),
        temperature=0.2,
        max_tokens=4096,
        json_retries=1,
    )

    blocks = []
    raw_blocks = result.get("blocks", [])
    if not raw_blocks or result.get("_parse_error"):
        # Fallback: single block
        end = cleaned.user_anchors[-1].index if cleaned.user_anchors else 0
        return [
            IntentBlock(
                block_id=1,
                message_range=(0, end),
                intent="entire session",
            )
        ]

    for b in raw_blocks:
        rng = b.get("message_range", [0, 0])
        blocks.append(
            IntentBlock(
                block_id=b.get("block_id", len(blocks) + 1),
                message_range=(rng[0], rng[1]),
                intent=b.get("intent", ""),
            )
        )

    return blocks


# ── Level 1b: Per-block structured extraction ──

BLOCK_EXTRACT_SYSTEM = """你是一个开发轨迹分析器。分析以下开发会话片段，提取结构化信息。
只输出 JSON，不要其他内容。"""

BLOCK_EXTRACT_PROMPT = """分析以下开发会话片段：

{block_content}

上下文：用户意图是「{intent}」

请提取以下结构化信息，严格输出 JSON：
{{
  "what_happened": "这一段做了什么（1-2句话）",
  "tools_used": ["使用的工具列表"],
  "code_changes": [
    {{"file": "文件路径", "operation": "create|modify|delete", "summary": "改了什么"}}
  ],
  "problems_found": [
    {{"problem": "遇到什么问题", "how_resolved": "如何解决", "is_resolved": true/false}}
  ],
  "key_decisions": [
    {{"decision": "做了什么决策", "rationale": "为什么"}}
  ],
  "outcome": "success|partial|failure"
}}"""


def extract_block_summary(
    cleaned: CleanedSession, block: IntentBlock, llm: LLMClient
) -> dict[str, Any]:
    """Level 1b: Extract structured summary for a single intent block."""
    start, end = block.message_range
    content = format_block_for_llm(cleaned, start, end)

    # Truncate to token budget
    budget = INPUT_TOKEN_BUDGET - PROMPT_OVERHEAD - estimate_tokens(BLOCK_EXTRACT_SYSTEM)
    content = truncate_to_token_budget(content, budget)

    result = llm.chat_json_with_retry(
        BLOCK_EXTRACT_SYSTEM,
        BLOCK_EXTRACT_PROMPT.format(block_content=content, intent=block.intent),
        temperature=0.2,
        max_tokens=2048,
        json_retries=1,
    )

    result["block_id"] = block.block_id
    result["intent"] = block.intent
    result["message_range"] = list(block.message_range)
    return result


# ── Level 2: Session-level aggregation ──

AGGREGATE_SYSTEM = """你是一个高级软件工程师，擅长从开发过程中提炼关键信息。
请将多个开发片段的分析结果整合为一份结构化的会话摘要。"""

AGGREGATE_PROMPT = """以下是一个开发会话各阶段的分析结果：

{block_summaries}

项目：{project}
会话标题：{title}

请整合为一份完整的会话级摘要。严格输出以下 JSON：
{{
  "session_type": "feature_development|debugging|exploration|refactoring|config|other",
  "intent": "用户整体想做什么（一句话）",
  "what_happened": [
    {{"phase": "阶段名", "summary": "做了什么"}}
  ],
  "problems_encountered": [
    {{"problem": "问题描述", "how_resolved": "解决方式", "lessons": "教训"}}
  ],
  "key_decisions": [
    {{"decision": "决策内容", "rationale": "原因", "outcome": "结果"}}
  ],
  "lessons_learned": ["从整个过程中学到的经验"],
  "success_indicators": ["表明成功的信号"],
  "failure_indicators": ["表明遇到问题的信号"],
  "overall_outcome": "success|partial|failure"
}}"""


def aggregate_session_summary(
    cleaned: CleanedSession,
    block_summaries: list[dict[str, Any]],
    llm: LLMClient,
) -> TrajectorySummary:
    """Level 2: Aggregate block summaries into session-level TrajectorySummary."""
    summaries_text = json.dumps(block_summaries, ensure_ascii=False, indent=2)

    # Truncate to token budget
    budget = INPUT_TOKEN_BUDGET - PROMPT_OVERHEAD - estimate_tokens(AGGREGATE_SYSTEM)
    summaries_text = truncate_to_token_budget(summaries_text, budget)

    result = llm.chat_json_with_retry(
        AGGREGATE_SYSTEM,
        AGGREGATE_PROMPT.format(
            block_summaries=summaries_text,
            project=cleaned.project,
            title=cleaned.title,
        ),
        temperature=0.2,
        max_tokens=3000,
        json_retries=1,
    )

    # Build TrajectorySummary from result
    phases = [
        PhaseSummary(phase=p.get("phase", ""), summary=p.get("summary", ""))
        for p in result.get("what_happened", [])
    ]
    problems = [
        ProblemRecord(
            problem=p.get("problem", ""),
            how_resolved=p.get("how_resolved", ""),
            lessons=p.get("lessons", ""),
        )
        for p in result.get("problems_encountered", [])
    ]
    decisions = [
        DecisionRecord(
            decision=d.get("decision", ""),
            rationale=d.get("rationale", ""),
            outcome=d.get("outcome", ""),
        )
        for d in result.get("key_decisions", [])
    ]

    # Compute label from multi-signal fusion
    label, score = _compute_label(cleaned, result)

    return TrajectorySummary(
        session_id=cleaned.session_id,
        session_type=result.get("session_type", ""),
        project=cleaned.project,
        intent=result.get("intent", ""),
        what_happened=phases,
        problems_encountered=problems,
        key_decisions=decisions,
        lessons_learned=result.get("lessons_learned", []),
        label=label,
        label_score=score,
    )


def _compute_label(
    cleaned: CleanedSession, llm_result: dict
) -> tuple[str, float]:
    """Multi-signal fusion for trajectory labeling."""
    signals: list[tuple[str, float]] = []

    # Signal: patches present
    if cleaned.has_patches:
        signals.append(("has_patch", 1.0))

    # Signal: exploration with substantial tool usage (no patch but actively gathered info)
    if not cleaned.has_patches and cleaned.tool_count >= 5:
        signals.append(("exploration_rich", 0.8))

    # Signal: last assistant finished cleanly
    if cleaned.last_finish == "stop":
        signals.append(("clean_stop", 0.7))

    # Signal: errors present (softened — errors in exploration are normal)
    if cleaned.has_errors and not cleaned.has_patches:
        signals.append(("has_error_exploration", -0.3))
    elif cleaned.has_errors:
        signals.append(("has_error", -0.8))

    # Signal: LLM-assessed outcome
    outcome = llm_result.get("overall_outcome", "")
    if outcome == "success":
        signals.append(("llm_success", 0.5))
    elif outcome == "failure":
        signals.append(("llm_failure", -0.3))

    # Signal: lessons learned (indicates learning value)
    lessons = llm_result.get("lessons_learned", [])
    if lessons:
        signals.append(("has_lessons", 0.3))

    # Signal: key decisions recorded (valuable insights extracted)
    decisions = llm_result.get("key_decisions", [])
    if decisions:
        signals.append(("has_decisions", 0.4))

    score = sum(w for _, w in signals)

    if score >= 0.7:
        return "success", score
    elif score >= 0.2:
        return "partial", score
    else:
        return "failure", score
