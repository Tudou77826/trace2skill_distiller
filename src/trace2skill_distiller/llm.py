"""LLM client — raw httpx-based for maximum compatibility with proxies."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

import httpx
from pydantic import BaseModel

from .config import ModelConfig

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
    # Convert budget back to chars, leave margin
    max_chars = budget * CHARS_PER_TOKEN
    if len(text) > max_chars:
        return text[:max_chars] + "\n...[truncated to fit token budget]"
    return text


class LLMClient:
    """HTTP client for OpenAI-compatible chat completion APIs."""

    def __init__(self, config: ModelConfig):
        self.config = config
        self.model = config.model
        self.max_tokens = config.max_tokens
        self.base_url = config.base_url.rstrip("/")
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.call_count = 0
        # Build proxy dict: empty string = no proxy
        proxy_arg: str | None = config.proxy or None

        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "curl/8.0",
            },
            timeout=httpx.Timeout(120.0, connect=10.0),
            verify=config.verify_ssl,
            proxy=proxy_arg,
        )

    def chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
        retries: int = 2,
    ) -> str:
        """Single-turn chat completion, returns raw text. With retry on server errors."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }

        last_error = None
        for attempt in range(retries + 1):
            try:
                resp = self._client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                )
                resp.raise_for_status()
                break
            except (httpx.RemoteProtocolError, httpx.ReadTimeout) as e:
                last_error = e
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(
                    f"LLM call failed after {retries + 1} attempts: {e}"
                ) from e
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500 and attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                # 400 could be context_length_exceeded
                if e.response.status_code == 400:
                    body = e.response.text
                    if "context_length" in body or "max_tokens" in body:
                        raise ContextOverflowError(
                            f"Input exceeds model context window. "
                            f"Estimated input tokens: {estimate_tokens(system + user)}"
                        ) from e
                raise

        data = resp.json()

        # Track usage
        usage = data.get("usage", {})
        self.total_input_tokens += usage.get("prompt_tokens", 0)
        self.total_output_tokens += usage.get("completion_tokens", 0)
        self.call_count += 1

        # Detect truncation by checking finish_reason
        finish_reason = data.get("choices", [{}])[0].get("finish_reason", "")
        if finish_reason == "length":
            # Output was truncated — log warning but still return partial
            pass

        # Extract content — some models put text in reasoning_content
        choice = data["choices"][0]["message"]
        content = choice.get("content") or choice.get("reasoning_content") or ""
        return content

    def chat_json(
        self,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
    ) -> dict[str, Any]:
        """Chat completion expecting JSON output. Auto-extracts JSON from response.

        Returns parsed dict. If parsing fails, returns {"_parse_error": True, ...}.
        Caller should check for "_parse_error" key.
        """
        raw = self.chat(system, user, temperature, max_tokens)
        result = self._extract_json(raw)
        return result

    def chat_json_with_retry(
        self,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        json_retries: int = 1,
    ) -> dict[str, Any]:
        """chat_json with automatic retry on JSON parse failure.

        If first attempt returns broken JSON, retries with explicit instruction
        to output only valid JSON.
        """
        result = self.chat_json(system, user, temperature, max_tokens)

        if not result.get("_parse_error"):
            return result

        if json_retries <= 0:
            return result

        # Retry with stronger instruction
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
                # Try to repair truncated JSON
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


class ContextOverflowError(Exception):
    """Raised when input exceeds the model's context window."""
    pass


def _repair_truncated_json(text: str) -> dict[str, Any] | None:
    """Attempt to repair truncated JSON by closing open brackets/braces."""
    # Count unclosed brackets
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
        if ch == '"' and not escape:
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

    # If balanced, it's not a truncation issue
    if not stack:
        return None

    # Try progressively closing from the last complete value
    # Strategy: find the last , or : and truncate there, then close all brackets
    for i in range(len(text) - 1, -1, -1):
        if text[i] in (",", ":"):
            # Remove the trailing incomplete value
            repaired = text[:i]
            # Close all remaining open brackets
            for opener in reversed(stack):
                repaired += "}" if opener == "{" else "]"
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                continue

    # Last resort: just close all brackets at the end
    repaired = text.rstrip(",\"': \t\n")
    for opener in reversed(stack):
        repaired += "}" if opener == "{" else "]"
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return None
