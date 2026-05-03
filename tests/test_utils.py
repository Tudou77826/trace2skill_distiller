"""Tests for core.utils — estimate_tokens with CJK support."""

from __future__ import annotations

from trace2skill_distiller.core.utils import estimate_tokens, truncate_to_token_budget


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_english(self):
        text = "Hello world this is English text"
        tokens = estimate_tokens(text)
        assert tokens > 0
        assert tokens <= len(text)

    def test_cjk_costs_more_per_char(self):
        """CJK chars should be estimated as more tokens per character than English."""
        eng = estimate_tokens("Hello world this is English")
        cjk = estimate_tokens("你好世界这是中文测试")
        # CJK: 10 chars, English: 27 chars
        # CJK tokens per char should be higher
        assert cjk / 10 > eng / 27

    def test_mixed_content(self):
        tokens = estimate_tokens("Use the 你好 function for 测试")
        assert tokens > 0

    def test_truncate_under_budget(self):
        text = "short"
        assert truncate_to_token_budget(text, 1000) == text

    def test_truncate_over_budget(self):
        text = "A" * 50000
        result = truncate_to_token_budget(text, 100)
        assert len(result) < len(text)
        assert "truncated" in result

    def test_truncate_cjk(self):
        text = "你好" * 5000  # 10000 CJK chars
        result = truncate_to_token_budget(text, 100)
        assert len(result) < len(text)
