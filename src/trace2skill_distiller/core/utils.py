"""Shared utility functions."""

import re

# CJK Unified Ideographs range — each character is typically 1-2 tokens
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")
_CHARS_PER_TOKEN = 4  # English / code / mixed
_CJK_CHARS_PER_TOKEN = 1.5  # CJK characters


def estimate_tokens(text: str) -> int:
    """Rough token count estimation.

    Accounts for CJK text where each character is 1-2 tokens
    (vs 4 chars/token for English/code).
    """
    if not text:
        return 0
    cjk_count = len(_CJK_RE.findall(text))
    non_cjk_count = len(text) - cjk_count
    return int(non_cjk_count / _CHARS_PER_TOKEN + cjk_count / _CJK_CHARS_PER_TOKEN) + 1


def truncate_to_token_budget(text: str, budget: int) -> str:
    """Truncate text to fit within a token budget."""
    estimated = estimate_tokens(text)
    if estimated <= budget:
        return text
    # Work backwards: find a safe truncation point
    # Use conservative chars-per-token (smaller value = more tokens per char)
    max_chars = int(budget * _CJK_CHARS_PER_TOKEN)
    if len(text) > max_chars:
        return text[:max_chars] + "\n...[truncated to fit token budget]"
    return text
