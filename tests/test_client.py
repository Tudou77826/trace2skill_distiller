"""Tests for llm.client — LLMClient.close, JSON repair."""

from __future__ import annotations

from trace2skill_distiller.llm.client import LLMClient, _repair_truncated_json


class TestLLMClientClose:
    def test_close_delegates_to_provider(self):
        """LLMClient.close() should call provider.close() if it exists."""
        class FakeProvider:
            def __init__(self):
                self.closed = False
            def complete(self, messages, **kw):
                return None
            def close(self):
                self.closed = True

        client = LLMClient(FakeProvider())
        client.close()
        assert client._provider.closed is True


class TestRepairTruncatedJson:
    def test_valid_json_returns_none(self):
        assert _repair_truncated_json('{"a": 1}') is None

    def test_truncated_object_repaired(self):
        result = _repair_truncated_json('{"a": 1, "b":')
        assert isinstance(result, dict)
        assert result.get("a") == 1
