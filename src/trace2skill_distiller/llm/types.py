"""LLM data types."""

from __future__ import annotations

from pydantic import BaseModel, Field

# Import LLMConfig from core for convenience
from ..core.config import LLMConfig


class LLMUsageStats(BaseModel):
    """Accumulated LLM usage statistics."""
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0


class LLMResponse(BaseModel):
    """Raw response from an LLM provider."""
    content: str
    finish_reason: str = ""
    usage: LLMUsageStats = Field(default_factory=LLMUsageStats)
    raw: dict = Field(default_factory=dict)


class ContextOverflowError(Exception):
    """Raised when input exceeds the model's context window."""
    pass
