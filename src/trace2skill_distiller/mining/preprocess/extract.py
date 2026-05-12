"""Level 1 & 2: Quick-LLM semantic processing."""

from __future__ import annotations

import json
import re
from typing import Any

from ...llm import LLMClient
from ...core.utils import estimate_tokens, truncate_to_token_budget
from ..types import (
    IntentBlock,
    TrajectorySummary,
    PhaseSummary,
    ProblemRecord,
    DecisionRecord,
    CleanedSession,
)
from .compress import format_anchors_for_llm, format_block_for_llm

INPUT_TOKEN_BUDGET = 60000
PROMPT_OVERHEAD = 500

# Segment processing constants
ANCHOR_SEGMENT_SIZE = 15  # Max user-turns per segment
SEGMENT_OVERLAP = 3      # Overlap between adjacent segments


# ── Level 1: Intent boundary detection ──

BOUNDARY_SYSTEM = """你是一个开发会话分析器。你的任务是识别用户意图的边界。
同一目标下的追问、确认、微调属于同一个意图块。
只有当用户切换到不同的目标或主题时，才需要分割。"""

BOUNDARY_PROMPT = """分析以下开发会话中的用户输入序列，识别意图边界。

User messages (chronological):
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
- message_range 使用消息在列表中的全局索引（从0开始，与输入中的索引一致）
- 相邻的块可以连续，不应有间隙
- 根据实际意图变化来决定 blocks 数量，不要预设固定数量
- 当用户消息超过20个时，至少应该分成6个以上的块"""


def detect_intent_boundaries(
    cleaned: CleanedSession, llm: LLMClient
) -> list[IntentBlock]:
    """Detect intent boundaries, with segmented processing for long sessions."""
    anchors = cleaned.user_anchors
    n_anchors = len(anchors)

    if n_anchors <= 2:
        # Very short session: single block
        end = anchors[-1].index if anchors else 0
        return [
            IntentBlock(
                block_id=1,
                message_range=(0, end),
                intent=anchors[0].text[:100] if anchors else "unknown",
            )
        ]

    if n_anchors <= ANCHOR_SEGMENT_SIZE:
        # Short session: single LLM call
        return _detect_boundaries_single(cleaned, llm)

    # Long session: segmented processing
    return _detect_boundaries_segmented(cleaned, llm)


def _detect_boundaries_single(
    cleaned: CleanedSession, llm: LLMClient
) -> list[IntentBlock]:
    """Single LLM call for short sessions."""
    anchors_text = format_anchors_for_llm(cleaned)

    budget = INPUT_TOKEN_BUDGET - PROMPT_OVERHEAD - estimate_tokens(BOUNDARY_SYSTEM)
    anchors_text = truncate_to_token_budget(anchors_text, budget)

    result = llm.chat_json_with_retry(
        BOUNDARY_SYSTEM,
        BOUNDARY_PROMPT.format(anchors=anchors_text),
        temperature=0.2,
        max_tokens=4096,
        json_retries=1,
    )

    return _parse_boundary_result(result, cleaned)


def _detect_boundaries_segmented(
    cleaned: CleanedSession, llm: LLMClient
) -> list[IntentBlock]:
    """Segmented processing for long sessions (>15 user-turns)."""
    anchors = cleaned.user_anchors
    n_anchors = len(anchors)

    # Split into segments with overlap
    segments = []
    start = 0
    while start < n_anchors:
        end = min(start + ANCHOR_SEGMENT_SIZE, n_anchors)
        segments.append((start, end))
        start = end - SEGMENT_OVERLAP  # Overlap for boundary continuity

    # Process each segment independently
    all_blocks: list[IntentBlock] = []
    for seg_start, seg_end in segments:
        # Format segment anchors with global indices
        segment_text = _format_segment_anchors(anchors, seg_start, seg_end)

        result = llm.chat_json_with_retry(
            BOUNDARY_SYSTEM,
            BOUNDARY_PROMPT.format(anchors=segment_text),
            temperature=0.2,
            max_tokens=4096,
            json_retries=1,
        )

        seg_blocks = _parse_segment_boundary_result(result, seg_start)
        all_blocks.extend(seg_blocks)

    # Merge overlapping blocks and deduplicate
    merged_blocks = _merge_segment_blocks(all_blocks, n_anchors)

    # Merge similar adjacent intents
    final_blocks = _merge_similar_intents(merged_blocks)

    return final_blocks


def _format_segment_anchors(
    anchors: list, seg_start: int, seg_end: int
) -> str:
    """Format segment anchors with global indices."""
    lines = []
    for i in range(seg_start, seg_end):
        anchor = anchors[i]
        # Use global index (i) instead of segment-local index
        lines.append(f"[{i}] {anchor.text[:200]}")
    return "\n".join(lines)


def _parse_boundary_result(
    result: dict, cleaned: CleanedSession
) -> list[IntentBlock]:
    """Parse LLM output for single-segment boundary detection."""
    raw_blocks = result.get("blocks", [])
    if not raw_blocks or result.get("_parse_error"):
        end = cleaned.user_anchors[-1].index if cleaned.user_anchors else 0
        return [
            IntentBlock(
                block_id=1,
                message_range=(0, end),
                intent="entire session",
            )
        ]

    blocks = []
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


def _parse_segment_boundary_result(
    result: dict, seg_start: int
) -> list[IntentBlock]:
    """Parse LLM output for segment boundary detection."""
    raw_blocks = result.get("blocks", [])
    if not raw_blocks or result.get("_parse_error"):
        return []

    blocks = []
    for b in raw_blocks:
        rng = b.get("message_range", [0, 0])
        # Indices are already global (from _format_segment_anchors)
        blocks.append(
            IntentBlock(
                block_id=b.get("block_id", len(blocks) + 1),
                message_range=(rng[0], rng[1]),
                intent=b.get("intent", ""),
            )
        )
    return blocks


def _merge_segment_blocks(
    blocks: list[IntentBlock], total_anchors: int
) -> list[IntentBlock]:
    """Merge overlapping blocks from different segments, deduplicate."""
    if not blocks:
        return []

    # Sort by start index
    sorted_blocks = sorted(blocks, key=lambda b: b.message_range[0])

    # Merge overlapping ranges (tolerance of 3 for minor boundary differences)
    merged: list[IntentBlock] = []
    for block in sorted_blocks:
        start, end = block.message_range

        # Check if this block overlaps with the last merged block
        if merged and start <= merged[-1].message_range[1] + 3:
            # Extend the last block if this one goes further
            last = merged[-1]
            if end > last.message_range[1]:
                merged[-1] = IntentBlock(
                    block_id=last.block_id,
                    message_range=(last.message_range[0], end),
                    intent=last.intent,  # Keep first intent
                )
        else:
            # No overlap, add as new block
            block_id = len(merged) + 1
            merged.append(
                IntentBlock(
                    block_id=block_id,
                    message_range=(start, end),
                    intent=block.intent,
                )
            )

    # Ensure full coverage with no gaps
    if merged:
        # Fill gaps between blocks
        filled: list[IntentBlock] = []
        prev_end = 0
        for block in merged:
            if block.message_range[0] > prev_end + 1:
                # Gap detected, fill it
                filled.append(
                    IntentBlock(
                        block_id=len(filled) + 1,
                        message_range=(prev_end, block.message_range[0]),
                        intent="(过渡段)",
                    )
                )
            filled.append(
                IntentBlock(
                    block_id=len(filled) + 1,
                    message_range=block.message_range,
                    intent=block.intent,
                )
            )
            prev_end = block.message_range[1]

        # Ensure last block covers to the end
        if filled and filled[-1].message_range[1] < total_anchors - 1:
            filled[-1] = IntentBlock(
                block_id=filled[-1].block_id,
                message_range=(filled[-1].message_range[0], total_anchors - 1),
                intent=filled[-1].intent,
            )

        merged = filled

    return merged


def _merge_similar_intents(blocks: list[IntentBlock]) -> list[IntentBlock]:
    """Merge adjacent blocks with similar intents."""
    if len(blocks) <= 1:
        return blocks

    merged: list[IntentBlock] = []
    for block in blocks:
        if not merged:
            merged.append(block)
            continue

        last = merged[-1]
        if _intents_are_similar(last.intent, block.intent):
            # Merge into one block
            merged[-1] = IntentBlock(
                block_id=last.block_id,
                message_range=(last.message_range[0], block.message_range[1]),
                intent=last.intent,  # Keep first intent
            )
        else:
            merged.append(
                IntentBlock(
                    block_id=len(merged) + 1,
                    message_range=block.message_range,
                    intent=block.intent,
                )
            )

    return merged


def _intents_are_similar(intent1: str, intent2: str) -> bool:
    """Check if two intents are similar based on keyword overlap."""
    # Extract keywords (Chinese + English words)
    words1 = set(re.findall(r"[a-zA-Z]+|[\u4e00-\u9fff]+", intent1.lower()))
    words2 = set(re.findall(r"[a-zA-Z]+|[\u4e00-\u9fff]+", intent2.lower()))

    # Filter out common filler words
    filler = {"的", "和", "与", "在", "是", "有", "这", "那", "the", "a", "an", "is", "are"}
    words1 = words1 - filler
    words2 = words2 - filler

    if not words1 or not words2:
        return False

    # 50% keyword overlap threshold
    overlap = len(words1 & words2)
    max_len = max(len(words1), len(words2))
    return overlap / max_len >= 0.5


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
  "discoveries": [
    "探索过程中发现的具体事实：文件路径、数据格式、架构关系、配置细节、API用法等客观认知"
  ],
  "outcome": "success|partial|failure"
}}"""


def extract_block_summary(
    cleaned: CleanedSession, block: IntentBlock, llm: LLMClient
) -> dict[str, Any]:
    start, end = block.message_range
    content = format_block_for_llm(cleaned, start, end)
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
  "discoveries": ["保留各阶段发现的具体事实：文件路径、数据格式、架构关系、配置细节、API用法等。去重合并，保留最具体准确的描述"],
  "success_indicators": ["表明成功的信号"],
  "failure_indicators": ["表明遇到问题的信号"],
  "overall_outcome": "success|partial|failure"
}}"""


def aggregate_session_summary(
    cleaned: CleanedSession,
    block_summaries: list[dict[str, Any]],
    llm: LLMClient,
) -> TrajectorySummary:
    summaries_text = json.dumps(block_summaries, ensure_ascii=False, indent=2)
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
        discoveries=result.get("discoveries", []),
        label=label,
        label_score=score,
    )


def _compute_label(
    cleaned: CleanedSession, llm_result: dict
) -> tuple[str, float]:
    signals: list[tuple[str, float]] = []

    if cleaned.has_patches:
        signals.append(("has_patch", 1.0))

    if not cleaned.has_patches and cleaned.tool_count >= 5:
        signals.append(("exploration_rich", 0.8))

    if cleaned.last_finish == "stop":
        signals.append(("clean_stop", 0.7))

    if cleaned.has_errors and not cleaned.has_patches:
        signals.append(("has_error_exploration", -0.3))
    elif cleaned.has_errors:
        signals.append(("has_error", -0.8))

    outcome = llm_result.get("overall_outcome", "")
    if outcome == "success":
        signals.append(("llm_success", 0.5))
    elif outcome == "failure":
        signals.append(("llm_failure", -0.3))

    lessons = llm_result.get("lessons_learned", [])
    if lessons:
        signals.append(("has_lessons", 0.3))

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
