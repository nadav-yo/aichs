from types import SimpleNamespace

from services.usage import merge_usage, normalize_usage, usage_summary, usage_tooltip


def test_normalize_anthropic_usage():
    usage = SimpleNamespace(
        input_tokens=10,
        cache_read_input_tokens=30,
        cache_creation_input_tokens=5,
        output_tokens=7,
    )
    data = normalize_usage("anthropic", usage)
    assert data["input_tokens"] == 45
    assert data["cached_input_tokens"] == 30
    assert data["cache_creation_input_tokens"] == 5
    assert data["output_tokens"] == 7


def test_normalize_openai_cached_tokens():
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "prompt_tokens_details": {"cached_tokens": 64},
    }
    data = normalize_usage("openai-compatible", usage)
    assert data["input_tokens"] == 100
    assert data["cached_input_tokens"] == 64
    assert data["uncached_input_tokens"] == 36
    assert data["output_tokens"] == 20


def test_merge_and_summarize_usage():
    merged = merge_usage(
        {"provider": "anthropic", "input_tokens": 10, "cached_input_tokens": 5},
        {"provider": "anthropic", "input_tokens": 20, "output_tokens": 3},
    )
    assert merged["input_tokens"] == 30
    assert merged["cached_input_tokens"] == 5
    assert usage_summary(merged) == "↓30 · ↑3"
    assert usage_tooltip(merged) == "Input tokens: 30\nCache hit: 5\nOutput tokens: 3"
