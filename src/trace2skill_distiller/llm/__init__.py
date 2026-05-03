"""LLM module: provider protocol + high-level client."""

from .types import LLMConfig, LLMResponse, LLMUsageStats, ContextOverflowError
from .base import LLMProvider
from .client import LLMClient

# Backward-compatible re-exports
from ..core.config import LLMConfig as ModelConfig
from ..core.utils import estimate_tokens, truncate_to_token_budget

__all__ = [
    "LLMConfig",
    "LLMResponse",
    "LLMUsageStats",
    "ContextOverflowError",
    "LLMProvider",
    "LLMClient",
    "ModelConfig",
    "estimate_tokens",
    "truncate_to_token_budget",
]
