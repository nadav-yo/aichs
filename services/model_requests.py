"""Helpers for applying model configuration to provider request payloads."""
from __future__ import annotations


def _float_in_range(value, minimum: float, maximum: float) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if minimum <= parsed <= maximum else None


def _top_k_value(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= -1 else None


def apply_generation_params(
    request: dict,
    cfg,
    *,
    include_extra_body: bool = True,
) -> dict:
    """Attach optional generation controls from a model config to a request."""
    temperature = _float_in_range(getattr(cfg, "temperature", None), 0.0, 2.0)
    if temperature is not None:
        request["temperature"] = temperature

    if not include_extra_body:
        return request

    extra_body = {}
    top_k = _top_k_value(getattr(cfg, "top_k", None))
    min_p = _float_in_range(getattr(cfg, "min_p", None), 0.0, 1.0)
    if top_k is not None:
        extra_body["top_k"] = top_k
    if min_p is not None:
        extra_body["min_p"] = min_p
    if not extra_body:
        return request

    existing = request.get("extra_body")
    if isinstance(existing, dict):
        extra_body = {**existing, **extra_body}
    request["extra_body"] = extra_body
    return request
