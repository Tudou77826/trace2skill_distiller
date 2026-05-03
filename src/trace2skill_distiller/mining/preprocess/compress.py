"""Level 0: Code-only smart compression and noise filtering.

No LLM calls — pure mechanical preprocessing.
"""

from __future__ import annotations

import re
import time as _time

from ..types import (
    Session,
    CleanedSession,
    CleanedMessage,
    UserAnchor,
    ToolCall,
)

# ── Constants ──

NOISE_TOOL_TYPES = {"todowrite", "profile_update", "profile_query"}

BLOCK_SOFT_LIMIT_CHARS = 80_000


# ── Per-tool-type smart compression ──

def _compress_bash(inp: dict, output) -> str:
    cmd = inp.get("command", "")[:200]
    out_str = str(output) if output else ""
    if not out_str:
        return f"$ {cmd}"
    lines = out_str.split("\n")
    is_error = any(
        kw in out_str.lower()[:500]
        for kw in ["error", "failed", "not found", "denied", "fatal"]
    )
    if len(lines) <= 6:
        compressed = out_str[:300]
    else:
        head = "\n".join(lines[:3])
        tail = "\n".join(lines[-2:])
        mid_count = len(lines) - 5
        compressed = f"{head}\n  ... ({mid_count} more lines) ...\n{tail}"
    compressed = compressed[:300]
    status_mark = "ERR" if is_error else "OK"
    return f"$ {cmd}  [{status_mark}]\n{compressed}"


def _compress_read(inp: dict, output) -> str:
    path = inp.get("filePath", "")
    out_str = str(output) if output else ""
    if not out_str:
        return f"Read({path}) → empty/no file"
    lines = out_str.split("\n")
    line_count = len([l for l in lines if l.strip()])
    first_content = ""
    for line in lines[:5]:
        stripped = line.strip()
        if stripped and not stripped.startswith("<"):
            first_content = stripped[:80]
            break
    return f"Read({path}) → {line_count} lines. Starts with: {first_content}"


def _compress_write(inp: dict, output) -> str:
    path = inp.get("filePath", "")
    content = inp.get("content", "")
    lines = content.split("\n")
    line_count = len(lines)
    if line_count <= 5:
        skeleton = content[:150]
    else:
        head = "\n".join(lines[:2])
        tail = "\n".join(lines[-2:])
        skeleton = f"{head}\n  ... ({line_count - 4} lines omitted) ...\n{tail}"
    return f"Write({path}, {line_count} lines)\n{skeleton}"


def _compress_edit(inp: dict, output) -> str:
    path = inp.get("filePath", "")
    old = inp.get("oldString", "")
    new = inp.get("newString", "")
    old_first = old.strip().split("\n")[0][:60] if old.strip() else "(empty)"
    new_first = new.strip().split("\n")[0][:60] if new.strip() else "(empty)"
    old_lines = len(old.strip().split("\n"))
    new_lines = len(new.strip().split("\n"))
    return f"Edit({path}) -{old_lines}/+{new_lines} lines\n  - {old_first}\n  + {new_first}"


def _compress_glob(inp: dict, output) -> str:
    pattern = inp.get("pattern", "")
    out_str = str(output) if output else ""
    if not out_str.strip():
        return f"Glob({pattern}) → 0 matches"
    paths = out_str.strip().split("\n")
    count = len(paths)
    first = "\n  ".join(paths[:5])
    if count > 5:
        first += f"\n  ... and {count - 5} more"
    return f"Glob({pattern}) → {count} matches:\n  {first}"


def _compress_grep(inp: dict, output) -> str:
    pattern = inp.get("pattern", "")
    out_str = str(output) if output else ""
    if not out_str.strip():
        return f"Grep({pattern}) → 0 matches"
    lines = out_str.strip().split("\n")
    count = len(lines)
    first = "\n  ".join(lines[:3])
    if count > 3:
        first += f"\n  ... and {count - 3} more"
    return f"Grep({pattern}) → {count} matches:\n  {first}"


def _compress_task(inp: dict, output) -> str:
    agent = inp.get("subagent_type", "?")
    desc = inp.get("description", "")[:100]
    out_str = str(output) if output else ""
    if not out_str:
        return f"Agent({agent}): {desc}"
    compressed = out_str[:200]
    if len(out_str) > 200:
        compressed += "..."
    return f"Agent({agent}): {desc}\n  → {compressed}"


def _compress_default(tool: str, inp: dict, output) -> str:
    out_str = str(output)[:200] if output else ""
    inp_str = str(inp)[:100]
    return f"{tool}({inp_str}) → {out_str}"


_COMPRESSORS = {
    "bash": _compress_bash,
    "read": _compress_read,
    "write": _compress_write,
    "edit": _compress_edit,
    "glob": _compress_glob,
    "grep": _compress_grep,
    "task": _compress_task,
}


def _compress_tool_call(raw: dict) -> ToolCall:
    state = raw.get("state", {})
    inp = state.get("input", {})
    out = state.get("output", "")
    tool_name = raw.get("tool", "unknown")
    compressor = _COMPRESSORS.get(tool_name, None)
    if tool_name in NOISE_TOOL_TYPES:
        summary = ""
    elif compressor:
        summary = compressor(inp, out)
    else:
        summary = _compress_default(tool_name, inp, out)
    return ToolCall(
        tool=tool_name,
        summary=summary,
        status=state.get("status", "completed"),
    )


def _compress_reasoning(text: str) -> str:
    if not text:
        return ""
    sentences = re.split(r'[。.!?！？]\s*', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return text[:150]
    if len(text) <= 200:
        return text
    last = sentences[-1]
    first = sentences[0][:80]
    return f"{first} ... → {last}"


# ── Main preprocessing ──

def preprocess(session: Session) -> CleanedSession:
    """Level 0: smart compression — no truncation, preserve semantics."""

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
        if msg.role == "user":
            text = " ".join(msg.text_parts) if msg.text_parts else ""
            anchor = UserAnchor(
                index=idx,
                text=text[:500],
                prev_assistant_summary=prev_assistant_text[:200],
                timestamp=msg.info.time.get("created", 0),
            )
            cleaned.user_anchors.append(anchor)

        cleaned_msg = CleanedMessage(
            role=msg.role,
            index=idx,
            error=msg.info.error,
            finish=msg.info.finish,
        )

        for p in msg.parts:
            ptype = p.get("type", "")
            if ptype == "text":
                cleaned_msg.text_parts.append(p.get("text", ""))
            elif ptype == "reasoning":
                conclusion = _compress_reasoning(p.get("text", ""))
                if conclusion:
                    cleaned_msg.reasoning_conclusions.append(conclusion)
            elif ptype == "tool":
                tool_name = p.get("tool", "")
                if tool_name not in NOISE_TOOL_TYPES:
                    cleaned_msg.tool_calls.append(_compress_tool_call(p))
            elif ptype == "patch":
                cleaned_msg.patches.append(p)
            elif ptype == "subtask":
                cleaned_msg.subtasks.append(p)

        if msg.role == "assistant" and cleaned_msg.text_parts:
            prev_assistant_text = " ".join(cleaned_msg.text_parts)[:200]

        cleaned.cleaned_messages.append(cleaned_msg)

    return cleaned


def should_process(
    cleaned: CleanedSession, min_messages: int = 5, min_tools: int = 3
) -> bool:
    if cleaned.message_count < min_messages:
        return False
    if cleaned.tool_count < min_tools:
        return False
    if not cleaned.user_anchors:
        return False
    return True


# ── Formatting for LLM consumption ──

def format_anchors_for_llm(cleaned: CleanedSession) -> str:
    lines = []
    for a in cleaned.user_anchors:
        ts = ""
        if a.timestamp:
            ts = _time.strftime("%H:%M", _time.localtime(a.timestamp / 1000))
        ctx = f"  (after: {a.prev_assistant_summary})" if a.prev_assistant_summary else ""
        lines.append(f"#{a.index} [{ts}] {a.text}{ctx}")
    return "\n".join(lines)


def format_block_for_llm(
    cleaned: CleanedSession, start_idx: int, end_idx: int
) -> str:
    parts = []
    for msg in cleaned.cleaned_messages[start_idx : end_idx + 1]:
        role_tag = "USER" if msg.role == "user" else "ASSISTANT"
        for text in msg.text_parts:
            parts.append(f"[{role_tag}] {text}")
        for conclusion in msg.reasoning_conclusions:
            parts.append(f"[THOUGHT] {conclusion}")
        for tc in msg.tool_calls:
            parts.append(f"[TOOL] {tc.summary}")
        for patch in msg.patches:
            parts.append(f"[PATCH] {patch.get('hash', '')[:8]} {patch.get('files', [])}")
        if msg.error:
            err_msg = msg.error.get("data", {}).get("message", str(msg.error))
            parts.append(f"[ERROR] {err_msg}")
    return "\n".join(parts)
