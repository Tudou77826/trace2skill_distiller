"""OpenAI-compatible provider — httpx-based for maximum compatibility."""

from __future__ import annotations

from typing import Any

import httpx

from ..types import LLMConfig, LLMResponse, LLMUsageStats, ContextOverflowError
from ...core.utils import estimate_tokens


class OpenAICompatibleProvider:
    """OpenAI-compatible chat completion provider using raw httpx.

    Supports:
    - Custom base_url
    - SSL toggle
    - HTTP/SOCKS proxy
    - Custom User-Agent
    - Custom extra headers
    - Timeout configuration
    """

    def __init__(self, config: LLMConfig):
        self._config = config
        self._model = config.model
        self._base_url = config.base_url.rstrip("/")
        self._max_tokens = config.max_tokens
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.call_count = 0

        proxy_arg: str | None = config.proxy or None

        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "User-Agent": config.user_agent,
        }
        headers.update(config.extra_headers)

        self._client = httpx.Client(
            headers=headers,
            timeout=httpx.Timeout(config.timeout, connect=config.connect_timeout),
            verify=config.verify_ssl,
            proxy=proxy_arg,
        )

    @property
    def model_name(self) -> str:
        return self._model

    def complete(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        """Send a chat completion request and return structured response."""
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens or self._max_tokens,
        }

        resp = self._client.post(
            f"{self._base_url}/chat/completions",
            json=payload,
        )

        if resp.status_code == 400:
            body = resp.text
            if "context_length" in body or "max_tokens" in body:
                prompt_text = " ".join(m.get("content", "") for m in messages)
                raise ContextOverflowError(
                    f"Input exceeds model context window. "
                    f"Estimated input tokens: {estimate_tokens(prompt_text)}"
                )

        resp.raise_for_status()
        data = resp.json()

        # Track usage
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.call_count += 1

        finish_reason = data.get("choices", [{}])[0].get("finish_reason", "")

        # Extract content — some models put text in reasoning_content
        choice = data["choices"][0]["message"]
        content = choice.get("content") or choice.get("reasoning_content") or ""

        return LLMResponse(
            content=content,
            finish_reason=finish_reason,
            usage=LLMUsageStats(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
            raw=data,
        )

    def reset_stats(self) -> dict:
        """Reset and return accumulated usage stats."""
        stats = {
            "calls": self.call_count,
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
        }
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.call_count = 0
        return stats
