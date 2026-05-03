"""High-level LLM client — composes a Provider with retry, JSON extraction, and token management."""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from pydantic import BaseModel

from .base import LLMProvider
from .types import ContextOverflowError, LLMResponse
from ..core.config import LLMConfig
from ..core.utils import estimate_tokens


class LLMClient:
    """High-level LLM client wrapping any LLMProvider.

    Accepts either an LLMProvider or an LLMConfig (for backward compat).
    When given an LLMConfig, auto-creates an OpenAICompatibleProvider.

    Provides:
    - Retry with exponential backoff
    - JSON extraction and repair
    - chat / chat_json / chat_json_with_retry / chat_pydantic
    - Token usage tracking
    """

    def __init__(self, provider_or_config, max_retries: int = 2):
        if isinstance(provider_or_config, LLMConfig):
            from .providers.openai_compatible import OpenAICompatibleProvider
            self._provider = OpenAICompatibleProvider(provider_or_config)
        else:
            self._provider = provider_or_config
        self._max_retries = max_retries
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.call_count = 0

    def chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
        retries: int = 2,
    ) -> str:
        """Single-turn chat completion, returns raw text. With retry on server errors."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        last_error = None
        for attempt in range(retries + 1):
            try:
                response = self._provider.complete(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens or 4096,
                )
                break
            except ContextOverflowError:
                raise
            except Exception as e:
                last_error = e
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(
                    f"LLM call failed after {retries + 1} attempts: {e}"
                ) from e

        # Track usage
        self.total_input_tokens += response.usage.input_tokens
        self.total_output_tokens += response.usage.output_tokens
        self.call_count += 1

        return response.content

    def chat_json(
        self,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
    ) -> dict[str, Any]:
        """Chat completion expecting JSON output. Auto-extracts JSON from response."""
        raw = self.chat(system, user, temperature, max_tokens)
        return self._extract_json(raw)

    def chat_json_with_retry(
        self,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        json_retries: int = 1,
    ) -> dict[str, Any]:
        """chat_json with automatic retry on JSON parse failure."""
        result = self.chat_json(system, user, temperature, max_tokens)

        if not result.get("_parse_error"):
            return result

        if json_retries <= 0:
            return result

        retry_system = system + "\n\nIMPORTANT: Output ONLY valid JSON. No markdown, no comments, no preamble."
        retry_result = self.chat_json(retry_system, user, temperature, max_tokens)

        if not retry_result.get("_parse_error"):
            return retry_result

        return retry_result

    def chat_pydantic(
        self,
        system: str,
        user: str,
        model_cls: type[BaseModel],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
    ) -> BaseModel:
        """Chat completion expecting Pydantic-parseable JSON output."""
        data = self.chat_json(system, user, temperature, max_tokens)
        return model_cls.model_validate(data)

    def reset_stats(self) -> dict[str, int]:
        """Reset and return accumulated stats."""
        stats = {
            "calls": self.call_count,
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
        }
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.call_count = 0
        return stats

    def close(self) -> None:
        """Close the underlying provider and release connections."""
        provider = self._provider
        if hasattr(provider, "close"):
            provider.close()

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Extract JSON from LLM response, handling markdown fences, preamble,
        and truncated output."""
        # Strip markdown code fences
        if "```" in text:
            lines = text.split("\n")
            in_fence = False
            collected: list[str] = []
            for line in lines:
                if line.strip().startswith("```"):
                    in_fence = not in_fence
                    continue
                if in_fence:
                    collected.append(line)
            text = "\n".join(collected)

        text = text.strip()

        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Find first { and last }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            candidate = text[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                repaired = _repair_truncated_json(candidate)
                if repaired is not None:
                    return repaired

        # Find first [ and last ] as fallback
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            try:
                return {"items": json.loads(text[start : end + 1])}
            except json.JSONDecodeError:
                pass

        return {"raw_response": text, "_parse_error": True}


def _repair_truncated_json(text: str) -> dict[str, Any] | None:
    """Attempt to repair truncated JSON by closing open brackets/braces."""
    stack: list[str] = []
    in_string = False
    escape = False

    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            # escape is always False here: True was consumed by the branch above
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch == "}" and stack and stack[-1] == "{":
            stack.pop()
        elif ch == "]" and stack and stack[-1] == "[":
            stack.pop()

    if not stack:
        return None

    for i in range(len(text) - 1, -1, -1):
        if text[i] in (",", ":"):
            repaired = text[:i]
            for opener in reversed(stack):
                repaired += "}" if opener == "{" else "]"
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                continue

    repaired = text.rstrip(",\"': \t\n")
    for opener in reversed(stack):
        repaired += "}" if opener == "{" else "]"
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return None
