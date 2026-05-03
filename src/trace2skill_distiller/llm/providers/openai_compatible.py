"""OpenAI-compatible provider — httpx-based for maximum compatibility."""

from __future__ import annotations

import httpx

from ..transport import ProxyBypassTransport
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

        if not config.api_key:
            raise ValueError(
                "API key is empty. Set TRACE2SKILL_API_KEY env var "
                "or configure api_key in config.yaml"
            )

        proxy_arg: str | None = config.proxy or None

        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "User-Agent": config.user_agent,
        }
        headers.update(config.extra_headers)

        timeout = httpx.Timeout(config.timeout, connect=config.connect_timeout)

        if config.proxy and config.proxy_bypass:
            transport = ProxyBypassTransport(
                proxy=config.proxy,
                bypass_patterns=config.proxy_bypass,
                verify=config.verify_ssl,
            )
            self._client = httpx.Client(
                headers=headers, timeout=timeout, transport=transport,
            )
        elif config.proxy:
            self._client = httpx.Client(
                headers=headers, timeout=timeout,
                verify=config.verify_ssl, proxy=proxy_arg,
            )
        else:
            self._client = httpx.Client(
                headers=headers, timeout=timeout, verify=config.verify_ssl,
            )

    @property
    def model_name(self) -> str:
        return self._model

    def close(self) -> None:
        """Close the underlying httpx client and release connections."""
        self._client.close()

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

        choices = data.get("choices", [])
        if not choices:
            raise ValueError(f"Empty choices in LLM response: {data}")

        choice = choices[0]
        finish_reason = choice.get("finish_reason", "")

        # Extract content — some models put text in reasoning_content
        message = choice.get("message", {})
        content = message.get("content") or message.get("reasoning_content") or ""

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
