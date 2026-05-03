"""Shared utility functions."""

# Conservative token estimation: ~4 chars per token for English/mixed content
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Rough token count estimation. Conservative (overestimates for CJK)."""
    return len(text) // CHARS_PER_TOKEN + 1


def truncate_to_token_budget(text: str, budget: int) -> str:
    """Truncate text to fit within a token budget."""
    estimated = estimate_tokens(text)
    if estimated <= budget:
        return text
    max_chars = budget * CHARS_PER_TOKEN
    if len(text) > max_chars:
        return text[:max_chars] + "\n...[truncated to fit token budget]"
    return text
