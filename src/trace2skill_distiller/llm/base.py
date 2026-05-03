"""LLM Provider protocol — the interface all providers must implement."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import LLMResponse


@runtime_checkable
class LLMProvider(Protocol):
    """Bottom-level LLM communication protocol.

    Only responsible for: building requests, sending them, returning raw responses.
    NOT responsible for: retries, JSON parsing, token management.
    """

    def complete(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        """Send a chat completion request and return raw response."""
        ...

    @property
    def model_name(self) -> str:
        """Return the model identifier."""
        ...

    def reset_stats(self) -> dict:
        """Reset and return accumulated usage stats."""
        ...
