from unittest.mock import patch

import pytest

from services.compaction import (
    _estimate_tokens,
    _find_cut_point,
    can_compact,
    compact,
    should_compact,
)


def _msgs(n: int, size: int = 5000) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * size} for i in range(n)]


def test_estimate_tokens():
    assert _estimate_tokens([{"role": "user", "content": "abcd"}]) >= 1


def test_find_cut_point_small_history():
    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    assert _find_cut_point(messages) == 0


def test_find_cut_point_large_history():
    messages = _msgs(30, size=8000)
    cut = _find_cut_point(messages)
    assert 0 < cut < len(messages)


def test_can_compact():
    assert not can_compact([{"role": "user", "content": "short"}])
    assert can_compact(_msgs(25, size=6000))


def test_should_compact(monkeypatch):
    monkeypatch.setattr("ui.theme.compaction_threshold_pct", lambda: 90)
    monkeypatch.setattr("services.compaction._estimate_tokens", lambda _m: 200_000)
    assert should_compact("claude-sonnet-4-6", [{"role": "user", "content": "x"}])


def test_compact_replaces_prefix(monkeypatch):
    messages = _msgs(25, size=6000)
    cut = _find_cut_point(messages)
    assert cut > 0
    with patch("services.compaction._call_model", return_value="Summary text."):
        out = compact("claude-sonnet-4-6", messages)
    assert out[0]["content"].startswith("[Conversation summary]")
    assert len(out) < len(messages)
