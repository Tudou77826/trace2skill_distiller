"""Level 0: Code-only noise filtering and coarse chunking.

No LLM calls here — pure mechanical preprocessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import Session, Message

# Tool types that are purely mechanical / management
NOISE_TOOL_TYPES = {"todowrite", "profile_update", "profile_query"}

# Max characters to keep from a tool output
MAX_TOOL_OUTPUT_CHARS = 500
# Max characters to keep from a file content in write/edit
MAX_CONTENT_PREVIEW = 300


@dataclass
class CleanedSession:
    """Session after noise removal, ready for LLM processing."""

    session_id: str
    project: str
    title: str
    message_count: int
    tool_count: int
    # Anchors: all user messages with index and surrounding context
    user_anchors: list[UserAnchor] = field(default_factory=list)
    # Cleaned message data (noise removed)
    cleaned_messages: list[CleanedMessage] = field(default_factory=list)
    # Metadata for filtering
    has_patches: bool = False
    has_errors: bool = False
    last_finish: str = ""
    total_tokens: int = 0


@dataclass
class UserAnchor:
    """A user message as an anchor point for intent boundary detection."""

    index: int  # position in original messages list
    text: str
    prev_assistant_summary: str = ""  # preceding assistant's text (truncated)
    timestamp: int = 0


@dataclass
class CleanedMessage:
    """A message with noise removed."""

    role: str
    index: int
    text_parts: list[str] = field(default_factory=list)
    reasoning_parts: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    patches: list[dict] = field(default_factory=list)
    subtasks: list[dict] = field(default_factory=list)
    error: dict | None = None
    finish: str = ""
    model: str = ""


@dataclass
class ToolCall:
    """Simplified tool call — only the meaningful parts."""

    tool: str
    input_summary: str  # human-readable summary of input
    output_summary: str  # truncated output
    status: str = "completed"

    @staticmethod
    def from_raw(raw: dict) -> "ToolCall":
        state = raw.get("state", {})
        inp = state.get("input", {})
        out = state.get("output", "")
        tool_name = raw.get("tool", "unknown")

        input_summary = _summarize_tool_input(tool_name, inp)
        output_str = str(out) if out else ""
        if len(output_str) > MAX_TOOL_OUTPUT_CHARS:
            output_str = output_str[:MAX_TOOL_OUTPUT_CHARS] + "...[truncated]"

        return ToolCall(
            tool=tool_name,
            input_summary=input_summary,
            output_summary=output_str,
            status=state.get("status", "completed"),
        )


def _summarize_tool_input(tool: str, inp: dict) -> str:
    """Create a compact human-readable summary of tool input."""
    if tool == "bash":
        return inp.get("command", "")[:200]
    elif tool == "read":
        path = inp.get("filePath", "")
        offset = inp.get("offset", "")
        limit = inp.get("limit", "")
        extra = f" (lines {offset}-{offset+limit})" if offset else ""
        return f"Read: {path}{extra}"
    elif tool == "write":
        path = inp.get("filePath", "")
        content = inp.get("content", "")
        preview = content[:MAX_CONTENT_PREVIEW]
        if len(content) > MAX_CONTENT_PREVIEW:
            preview += "...[truncated]"
        return f"Write: {path}\n{preview}"
    elif tool == "edit":
        path = inp.get("filePath", "")
        old = inp.get("oldString", "")[:100]
        new = inp.get("newString", "")[:100]
        return f"Edit: {path}\n  - {old}\n  + {new}"
    elif tool == "glob":
        return f"Glob: {inp.get('pattern', '')}"
    elif tool == "grep":
        return f"Grep: {inp.get('pattern', '')} in {inp.get('include', '*')}"
    elif tool == "task":
        return f"Agent({inp.get('subagent_type', '?')}): {inp.get('description', '')[:100]}"
    else:
        return str(inp)[:200]


def preprocess(session: Session) -> CleanedSession:
    """Level 0: clean noise, extract anchors, build lightweight representation."""

    cleaned = CleanedSession(
        session_id=session.session_id,
        project=session.project_name,
        title=session.info.title,
        message_count=len(session.messages),
        tool_count=session.tool_count,
        has_patches=session.has_patches,
        has_errors=session.has_errors,
        last_finish=session.last_assistant_finish,
        total_tokens=session.total_tokens,
    )

    prev_assistant_text = ""

    for idx, msg in enumerate(session.messages):
        # Build user anchors
        if msg.role == "user":
            text = " ".join(msg.text_parts) if msg.text_parts else ""
            anchor = UserAnchor(
                index=idx,
                text=text[:500],
                prev_assistant_summary=prev_assistant_text[:200],
                timestamp=msg.info.time.get("created", 0),
            )
            cleaned.user_anchors.append(anchor)

        # Build cleaned message
        cleaned_msg = CleanedMessage(
            role=msg.role,
            index=idx,
            error=msg.info.error,
            finish=msg.info.finish,
            model=msg.info.modelID,
        )

        for p in msg.parts:
            ptype = p.get("type", "")

            if ptype == "text":
                cleaned_msg.text_parts.append(p.get("text", ""))

            elif ptype == "reasoning":
                cleaned_msg.reasoning_parts.append(p.get("text", ""))

            elif ptype == "tool":
                tool_name = p.get("tool", "")
                if tool_name not in NOISE_TOOL_TYPES:
                    cleaned_msg.tool_calls.append(ToolCall.from_raw(p))

            elif ptype == "patch":
                cleaned_msg.patches.append(p)

            elif ptype == "subtask":
                cleaned_msg.subtasks.append(p)

        # Track last assistant text for next user anchor
        if msg.role == "assistant" and cleaned_msg.text_parts:
            prev_assistant_text = " ".join(cleaned_msg.text_parts)[:200]

        cleaned.cleaned_messages.append(cleaned_msg)

    return cleaned


def should_process(cleaned: CleanedSession, min_messages: int = 5, min_tools: int = 3) -> bool:
    """Quick filter: is this session worth analyzing?"""
    if cleaned.message_count < min_messages:
        return False
    if cleaned.tool_count < min_tools:
        return False
    if not cleaned.user_anchors:
        return False
    return True


def format_anchors_for_llm(cleaned: CleanedSession) -> str:
    """Format user anchors for intent boundary detection prompt."""
    lines = []
    for a in cleaned.user_anchors:
        time_str = ""
        if a.timestamp:
            import time as t
            time_str = t.strftime("%H:%M", t.localtime(a.timestamp / 1000))
        ctx = f"  (after: {a.prev_assistant_summary})" if a.prev_assistant_summary else ""
        lines.append(f"#{a.index} [{time_str}] {a.text}{ctx}")
    return "\n".join(lines)


def format_block_for_llm(
    cleaned: CleanedSession, start_idx: int, end_idx: int
) -> str:
    """Format a message range into a readable block for LLM extraction."""
    parts = []
    for msg in cleaned.cleaned_messages[start_idx : end_idx + 1]:
        role_tag = "USER" if msg.role == "user" else "ASSISTANT"

        for text in msg.text_parts:
            parts.append(f"[{role_tag}] {text[:800]}")

        for r in msg.reasoning_parts:
            parts.append(f"[REASONING] {r[:400]}")

        for tc in msg.tool_calls:
            status_mark = "OK" if tc.status == "completed" else "ERR"
            parts.append(
                f"[TOOL:{tc.tool}({status_mark})] {tc.input_summary}\n"
                f"  -> {tc.output_summary[:300]}"
            )

        for patch in msg.patches:
            parts.append(f"[PATCH] {patch.get('hash', '')[:8]} {patch.get('files', [])}")

        if msg.error:
            parts.append(f"[ERROR] {msg.error}")

    return "\n".join(parts)
