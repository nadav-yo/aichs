from __future__ import annotations


def normalize_usage(provider: str, usage) -> dict:
    if usage is None:
        return {}
    if provider == "anthropic":
        return _normalize_anthropic(usage)
    return _normalize_openai(usage)


def merge_usage(*items: dict) -> dict:
    total: dict[str, int | str] = {}
    provider = ""
    for item in items:
        if not isinstance(item, dict):
            continue
        provider = provider or str(item.get("provider") or "")
        for key, value in item.items():
            if key == "provider":
                continue
            if isinstance(value, int):
                total[key] = int(total.get(key, 0)) + value
    if provider and total:
        total["provider"] = provider
    return total


def usage_summary(usage: dict | None) -> str:
    if not isinstance(usage, dict) or not usage:
        return ""
    input_tokens = _int(usage.get("input_tokens"))
    output = _int(usage.get("output_tokens"))
    parts = []
    if input_tokens:
        parts.append(f"↓{input_tokens:,}")
    if output:
        parts.append(f"↑{output:,}")
    return " · ".join(parts)


def usage_tooltip(usage: dict | None) -> str:
    if not isinstance(usage, dict) or not usage:
        return ""
    input_tokens = _int(usage.get("input_tokens"))
    cached = _int(usage.get("cached_input_tokens"))
    cache_write = _int(usage.get("cache_creation_input_tokens"))
    output = _int(usage.get("output_tokens"))
    lines = []
    if input_tokens:
        lines.append(f"Input tokens: {input_tokens:,}")
    if cached:
        lines.append(f"Cache hit: {cached:,}")
    if cache_write:
        lines.append(f"Cache write: {cache_write:,}")
    if output:
        lines.append(f"Output tokens: {output:,}")
    return "\n".join(lines)


def _normalize_anthropic(usage) -> dict:
    input_tokens = _field(usage, "input_tokens")
    cache_read = _field(usage, "cache_read_input_tokens")
    cache_creation = _field(usage, "cache_creation_input_tokens")
    output_tokens = _field(usage, "output_tokens")
    total_input = input_tokens + cache_read + cache_creation
    return _compact({
        "provider": "anthropic",
        "input_tokens": total_input,
        "uncached_input_tokens": input_tokens,
        "cached_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_creation,
        "output_tokens": output_tokens,
    })


def _normalize_openai(usage) -> dict:
    input_tokens = _field(usage, "prompt_tokens")
    output_tokens = _field(usage, "completion_tokens")
    total_tokens = _field(usage, "total_tokens")
    details = _field_obj(usage, "prompt_tokens_details")
    cached = _field(details, "cached_tokens") if details is not None else 0
    return _compact({
        "provider": "openai-compatible",
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "uncached_input_tokens": max(0, input_tokens - cached) if input_tokens else 0,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    })


def _compact(data: dict) -> dict:
    out = {}
    for key, value in data.items():
        if key == "provider":
            out[key] = value
        elif isinstance(value, int) and value > 0:
            out[key] = value
    return out if any(key != "provider" for key in out) else {}


def _field(obj, name: str) -> int:
    if obj is None:
        return 0
    value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    return _int(value)


def _field_obj(obj, name: str):
    if obj is None:
        return None
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _int(value) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0
