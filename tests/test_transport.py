"""Tests for llm.transport — ProxyBypassTransport routing and regex validation."""

from __future__ import annotations

import pytest

from trace2skill_distiller.llm.transport import ProxyBypassTransport


class TestProxyBypassTransport:
    def test_compiles_valid_patterns(self):
        t = ProxyBypassTransport("http://p:1", "localhost,127\\.0\\.0\\.1", verify=False)
        assert len(t._bypass) == 2
        assert t._bypass[0].pattern == "localhost"
        assert t._bypass[1].pattern == "127\\.0\\.0\\.1"

    def test_empty_pattern_gives_no_bypass(self):
        t = ProxyBypassTransport("http://p:1", "", verify=False)
        assert len(t._bypass) == 0

    def test_whitespace_only_skipped(self):
        t = ProxyBypassTransport("http://p:1", "localhost, , ,", verify=False)
        assert len(t._bypass) == 1

    def test_invalid_regex_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid regex"):
            ProxyBypassTransport("http://p:1", "[invalid", verify=False)

    def test_no_async_method(self):
        """BaseTransport is sync — no handle_async_request should exist."""
        assert not hasattr(ProxyBypassTransport, "handle_async_request")

    def test_close_cleans_up(self):
        t = ProxyBypassTransport("http://p:1", "localhost", verify=False)
        t.close()  # should not raise
