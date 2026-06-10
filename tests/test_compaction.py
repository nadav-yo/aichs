from unittest.mock import patch

import pytest

from services.compaction import (
    _estimate_tokens,
    _forced_cut_point,
    _find_cut_point,
    can_compact,
    compact,
    compact_with_result,
    compaction_threshold,
    keep_recent_tokens,
    reserve_tokens,
    should_compact,
    summary_max_tokens,
)
from storage.settings import COMPACTION_SUMMARY_GUIDANCE_KEY, SettingsStore


def _msgs(n: int, size: int = 5000) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * size} for i in range(n)]


def test_reserve_and_keep_scale_for_small_window():
    window = 20_488
    assert reserve_tokens(window) == 4_097
    assert keep_recent_tokens(window) == 8_195
    assert compaction_threshold(window) == 16_391


def test_reserve_caps_at_default_for_large_window():
    assert reserve_tokens(180_000) == 16_384
    assert keep_recent_tokens(180_000) == 20_000


def test_settings_override_reserve_and_keep(isolate_aichs_home):
    from config import SETTINGS_PATH

    SETTINGS_PATH.write_text(
        '{"compaction": {"reserve_tokens": 8192, "keep_recent_tokens": 6000}}',
        encoding="utf-8",
    )
    assert reserve_tokens(20_488) == 8_192
    assert keep_recent_tokens(20_488) == 6_000
    assert compaction_threshold(20_488) == 12_296


def test_settings_accepts_legacy_camel_case_keys(isolate_aichs_home):
    from config import SETTINGS_PATH

    SETTINGS_PATH.write_text(
        '{"compaction": {"reserveTokens": 4096, "keepRecentTokens": 3000}}',
        encoding="utf-8",
    )
    assert reserve_tokens(20_488) == 4_096
    assert keep_recent_tokens(20_488) == 3_000


def test_summary_max_tokens_uses_reserve_fraction():
    window = 20_488
    assert summary_max_tokens(window) == int(0.8 * reserve_tokens(window))


def test_estimate_tokens():
    assert _estimate_tokens([{"role": "user", "content": "abcd"}]) >= 1


def test_find_cut_point_small_history():
    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    assert _find_cut_point(messages, "claude-sonnet-4-6") == 0


def test_forced_cut_point_compacts_before_context_pressure():
    messages = [
        {"role": "user", "content": "goal"},
        {"role": "assistant", "content": "plan"},
        {"role": "user", "content": "next"},
        {"role": "assistant", "content": "answer"},
    ]
    assert _find_cut_point(messages, "claude-sonnet-4-6") == 0
    assert _find_cut_point(messages, "claude-sonnet-4-6", force=True) == 2
    assert _forced_cut_point(messages) == 2


def test_forced_cut_point_can_compact_completed_restored_chat():
    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    assert _find_cut_point(messages, "claude-sonnet-4-6") == 0
    assert _find_cut_point(messages, "claude-sonnet-4-6", force=True) == 2
    assert _forced_cut_point(messages) == 2


def test_forced_cut_point_keeps_incomplete_tail():
    messages = [
        {"role": "user", "content": "goal"},
        {"role": "assistant", "content": "plan"},
        {"role": "user", "content": "new task"},
    ]
    assert _find_cut_point(messages, "claude-sonnet-4-6", force=True) == 2


def test_find_cut_point_large_history():
    messages = _msgs(30, size=8000)
    cut = _find_cut_point(messages, "claude-sonnet-4-6")
    assert 0 < cut < len(messages)


def test_find_cut_point_small_window_fallback(monkeypatch):
    monkeypatch.setattr("services.compaction.context_window_tokens", lambda _m: 20_488)
    messages = _msgs(8, size=2500)
    cut = _find_cut_point(messages, "local-qwen")
    assert 0 < cut < len(messages)


def test_can_compact():
    model = "claude-sonnet-4-6"
    assert not can_compact([{"role": "user", "content": "short"}], model)
    assert can_compact(_msgs(4, size=10), model, force=True)
    assert can_compact(_msgs(25, size=6000), model)


def test_should_compact_uses_full_context_tokens(monkeypatch):
    monkeypatch.setattr("services.compaction.context_window_tokens", lambda _m: 20_488)
    messages = [{"role": "user", "content": "x" * 400}]
    assert not should_compact("local-qwen", messages, context_tokens=16_000)
    assert should_compact("local-qwen", messages, context_tokens=17_000)


def test_should_compact_messages_only_over_threshold(monkeypatch):
    monkeypatch.setattr("services.compaction.context_window_tokens", lambda _m: 20_488)
    huge = [{"role": "user", "content": "x" * (compaction_threshold(20_488) * 4 + 400)}]
    assert should_compact("local-qwen", huge)


def test_compact_replaces_prefix(monkeypatch):
    messages = _msgs(25, size=6000)
    cut = _find_cut_point(messages, "claude-sonnet-4-6")
    assert cut > 0
    with patch("services.compaction._call_model", return_value="Summary text.") as call:
        out = compact("claude-sonnet-4-6", messages)
    assert call.call_args.args[2] == summary_max_tokens(180_000)
    assert "coding-agent conversation" in call.call_args.args[1]
    assert "Files, symbols, commands, tests" in call.call_args.args[1]
    assert out[0]["content"].startswith("[Conversation summary]")
    assert len(out) < len(messages)


def test_compact_appends_configured_summary_guidance():
    SettingsStore().save({
        COMPACTION_SUMMARY_GUIDANCE_KEY: "Preserve exact test commands.",
    })
    messages = _msgs(4, size=20)
    with patch("services.compaction._call_model", return_value="Guided summary.") as call:
        compact("claude-sonnet-4-6", messages, force=True)

    prompt = call.call_args.args[1]
    assert "Preserve only durable" in prompt
    assert "Additional user guidance:" in prompt
    assert "Preserve exact test commands." in prompt


def test_compact_with_result_includes_proof():
    messages = _msgs(4, size=20)
    with patch("services.compaction._call_model", return_value="Summary text."):
        result = compact_with_result(
            "claude-sonnet-4-6",
            messages,
            force=True,
            source="test-extension",
        )
    assert result.status == "compacted"
    assert result.cut_index == 2
    assert result.proof["version"] == "aicc-compaction/v1"
    assert result.proof["source"] == "test-extension"
    assert result.proof["summary_input_sha256"]


def test_compact_with_result_validates_ledger():
    messages = _msgs(4, size=20)
    ledger = {
        "version": "aicc-continuation/v1",
        "task": "Keep working",
        "done_when": "Done",
        "forbid": [],
        "established": [{"claim": "A"}],
        "learned": [],
        "open": [],
        "next": ["Continue"],
    }
    import json

    with patch("services.compaction._call_model", return_value=json.dumps(ledger)):
        result = compact_with_result("claude-sonnet-4-6", messages, force=True, ledger=True)
    assert result.artifact == ledger
    assert "[Continuation ledger]" in result.summary


def test_compact_with_result_rejects_invalid_ledger():
    messages = _msgs(4, size=20)
    with patch("services.compaction._call_model", return_value='{"version": "wrong"}'):
        with pytest.raises(ValueError, match="invalid continuation ledger"):
            compact_with_result("claude-sonnet-4-6", messages, force=True, ledger=True)


def test_compact_force_replaces_small_prefix():
    messages = _msgs(4, size=20)
    with patch("services.compaction._call_model", return_value="Manual summary."):
        out = compact("claude-sonnet-4-6", messages, force=True)
    assert out[0]["content"].startswith("[Conversation summary]")
    assert out[2:] == messages[2:]


def test_compact_force_replaces_completed_restored_chat():
    messages = _msgs(2, size=20)
    with patch("services.compaction._call_model", return_value="Restored summary."):
        out = compact("claude-sonnet-4-6", messages, force=True)
    assert out == [
        {"role": "user", "content": "[Conversation summary]\nRestored summary."},
        {"role": "assistant", "content": "Thank you for the context — I'm fully caught up."},
    ]
