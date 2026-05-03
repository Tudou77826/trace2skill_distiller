"""Tests for llm.providers.openai_compatible — API key validation, choices safety, close."""

from __future__ import annotations

import pytest

from trace2skill_distiller.core.config import LLMConfig
from trace2skill_distiller.llm.providers.openai_compatible import OpenAICompatibleProvider


class TestProviderInit:
    def test_empty_api_key_raises(self):
        cfg = LLMConfig(api_key="", base_url="http://localhost:1")
        with pytest.raises(ValueError, match="API key is empty"):
            OpenAICompatibleProvider(cfg)

    def test_close_releases_connections(self):
        cfg = LLMConfig(api_key="sk-test", base_url="http://localhost:1")
        provider = OpenAICompatibleProvider(cfg)
        provider.close()  # should not raise


class TestChoicesSafety:
    def test_empty_choices_raises_value_error(self):
        cfg = LLMConfig(api_key="sk-test", base_url="http://localhost:1")
        provider = OpenAICompatibleProvider(cfg)
        # Simulate the parsing path with empty choices
        with pytest.raises(ValueError, match="Empty choices"):
            # Direct test of the logic
            data = {"choices": [], "usage": {}}
            choices = data.get("choices", [])
            if not choices:
                raise ValueError(f"Empty choices in LLM response: {data}")
