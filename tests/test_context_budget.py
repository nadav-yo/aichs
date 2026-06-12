import pytest

from services.compaction import compaction_threshold, reserve_tokens
from services.context_budget import ContextBudget, ContextSegment, format_bytes


@pytest.mark.parametrize(
    "n,expected",
    [
        (500, "500 B"),
        (2048, "2.0 KB"),
        (3 * 1024 * 1024, "3.0 MB"),
    ],
)
def test_format_bytes(n, expected):
    assert format_bytes(n) == expected


class TestContextSegment:
    def test_empty_text_zero_tokens(self):
        seg = ContextSegment("label", "")
        assert seg.token_count == 0
        assert seg.byte_count == 0

    def test_non_empty_token_estimate(self):
        seg = ContextSegment("label", "abcd")  # 4 bytes -> max(1, 1) = 1? 4//4 = 1
        assert seg.byte_count == 4
        assert seg.token_count == 1

    def test_utf8_byte_count(self):
        seg = ContextSegment("label", "é")  # 2 bytes in utf-8
        assert seg.byte_count == 2


class TestContextBudget:
    def _budget(self, segments, window_tokens=10_000):
        return ContextBudget(
            segments=segments,
            window_tokens=window_tokens,
            reserve_tokens=reserve_tokens(window_tokens),
        )

    def test_used_tokens_and_bytes(self):
        budget = self._budget([
            ContextSegment("a", "aaaa"),
            ContextSegment("b", "bbbbbbbb"),
        ])
        assert budget.used_tokens == 3  # 1 + 2
        assert budget.used_bytes == 12

    def test_pct_capped_at_100(self):
        budget = self._budget([ContextSegment("x", "x" * 400)], window_tokens=10)
        assert budget.pct == 100.0

    def test_pct_zero_window(self):
        budget = self._budget([ContextSegment("x", "hello")], window_tokens=0)
        assert budget.pct == 0.0

    def test_compaction_limit_tokens(self):
        window = 100_000
        budget = self._budget([], window_tokens=window)
        assert budget.compaction_limit_tokens == compaction_threshold(window)

    def test_compaction_limit_is_window_minus_reserve(self):
        window = 32_768
        budget = self._budget([], window_tokens=window)
        assert budget.compaction_limit_tokens == window - budget.reserve_tokens
        assert budget.compaction_limit_tokens < window
