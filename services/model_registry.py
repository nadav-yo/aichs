"""Model registry - loads built-in providers and merges ~/.aichs/models.json on top.

Public API
----------
MODELS        : dict[str, list[str]]   provider_id -> [model_id, ...]
MODEL_PROVIDER: dict[str, str]         model_id    -> provider_id
PROVIDERS     : dict[str, ProviderConfig]
get_model_config(model_id) -> ModelConfig
get_provider_config(provider_id) -> ProviderConfig | None
load_user_providers() -> dict
save_user_providers(providers) -> None
reload() -> None
api_key_env_var(spec) -> str | None
resolve_api_key(spec)      -> str
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import warnings
from dataclasses import dataclass
from pathlib import Path

from services.subprocess_utils import no_window_creationflags, no_window_startupinfo

_MODELS_PATH = Path.home() / ".aichs" / "models.json"
_MODEL_ID_CONTEXT_SUFFIX = re.compile(r"\s@\s*\d+\s*$")

_BUILTIN: dict = {
    "claude": {
        "api": "anthropic",
        "api_key_spec": "ANTHROPIC_API_KEY",
        "models": [
            {"id": "claude-opus-4-7", "name": "Claude Opus 4.7"},
            {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6"},
            {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5"},
        ],
    },
    "openai": {
        "api": "openai-compatible",
        "api_key_spec": "OPENAI_API_KEY",
        "models": [
            {"id": "gpt-5.5", "name": "GPT-5.5"},
            {"id": "gpt-5.4-mini", "name": "GPT-5.4 Mini"},
            {"id": "gpt-5.4-nano", "name": "GPT-5.4 Nano"},
            {"id": "gpt-5.1", "name": "GPT-5.1"},
        ],
    },
}

_VALID_APIS = {"anthropic", "openai-compatible"}
_BUILTIN_PROVIDER_IDS = frozenset({"claude", "openai"})
_ANTHROPIC_CONTEXT: dict[str, int] = {}


def api_default_context_window(api: str) -> int:
    """Default context size (tokens) when no provider/model override is set."""
    if api == "anthropic":
        return 180_000
    return 100_000


def _parse_context_window(value) -> int | None:
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _parse_float_range(value, minimum: float, maximum: float) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if minimum <= parsed <= maximum else None


def _parse_temperature(value) -> float | None:
    return _parse_float_range(value, 0.0, 2.0)


def _parse_top_k(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= -1 else None


def _parse_min_p(value) -> float | None:
    return _parse_float_range(value, 0.0, 1.0)


def _coalesce_config_value(primary, fallback):
    return primary if primary is not None else fallback


@dataclass
class ModelConfig:
    provider_id:  str
    api:          str         # "anthropic" | "openai-compatible"
    base_url:     str | None  # None = SDK default
    api_key_spec: str         # env var name, "!command", or literal key
    display_name: str
    context_window: int | None = None  # tokens; None = api default
    temperature: float | None = None
    top_k: int | None = None
    min_p: float | None = None


@dataclass
class ProviderConfig:
    provider_id:  str
    api:          str
    base_url:     str | None
    api_key_spec: str
    model_ids:    list[str]
    context_window: int | None = None
    temperature: float | None = None
    top_k: int | None = None
    min_p: float | None = None


def api_key_env_var(spec: str) -> str | None:
    """Return the env var name for an apiKey spec, or None for commands/literals."""
    if not spec or spec.startswith("!"):
        return None
    if spec == spec.upper() and " " not in spec and not any(
        spec.startswith(p) for p in ("sk-", "Bearer ", "Basic ")
    ):
        return spec
    return None


def resolve_api_key(spec: str) -> str:
    """Turn an api_key_spec into the actual key string.

    Formats:
    - ``"!command"``        — run shell command, use stdout
    - ``"ENV_VAR_NAME"``    — read from environment
    - ``"sk-literal..."``   — return as-is (literal key)
    """
    if not spec:
        return ""
    if spec.startswith("!"):
        try:
            result = subprocess.run(
                spec[1:],
                shell=True,
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=no_window_creationflags(),
                startupinfo=no_window_startupinfo(),
            )
            if result.returncode != 0:
                warnings.warn(f"aichs: API key command failed ({spec[1:]!r}): {result.stderr.strip()}")
                return ""
            return result.stdout.strip()
        except Exception as exc:
            warnings.warn(f"aichs: API key command error: {exc}")
            return ""
    env_var = api_key_env_var(spec)
    if env_var:
        return os.environ.get(env_var, "")
    return spec  # literal


# ── Merging ────────────────────────────────────────────────────────────────────

def _load_user_providers() -> dict:
    if not _MODELS_PATH.exists():
        return {}
    try:
        data = json.loads(_MODELS_PATH.read_text(errors="replace"))
        return data.get("providers", {})
    except Exception as exc:
        warnings.warn(f"aichs: could not parse ~/.aichs/models.json: {exc}")
        return {}


def load_user_providers() -> dict:
    """Return provider definitions from ~/.aichs/models.json."""
    return copy.deepcopy(_load_user_providers())


def save_user_providers(providers: dict) -> None:
    """Persist provider definitions to ~/.aichs/models.json."""
    _MODELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if providers:
        _MODELS_PATH.write_text(json.dumps({"providers": providers}, indent=2))
    elif _MODELS_PATH.exists():
        _MODELS_PATH.unlink()


def _merge(builtin: dict, user: dict) -> dict:
    merged = copy.deepcopy(builtin)
    for name, ucfg in user.items():
        # JSON uses camelCase keys; normalise to snake_case for internal use
        api          = ucfg.get("api", "openai-compatible")
        api_key_spec = ucfg.get("apiKey", ucfg.get("api_key_spec", ""))
        base_url     = ucfg.get("baseUrl", ucfg.get("base_url"))
        context_window = _parse_context_window(
            ucfg.get("contextWindow", ucfg.get("context_window")),
        )
        temperature = _parse_temperature(ucfg.get("temperature"))
        top_k = _parse_top_k(ucfg.get("topK", ucfg.get("top_k")))
        min_p = _parse_min_p(ucfg.get("minP", ucfg.get("min_p")))
        user_models  = ucfg.get("models", [])

        if api not in _VALID_APIS:
            warnings.warn(f"aichs: unknown api type {api!r} for provider {name!r} — skipping")
            continue

        if name in merged:
            existing = merged[name]
            if api_key_spec:
                existing["api_key_spec"] = api_key_spec
            if base_url is not None:
                existing["base_url"] = base_url
            if api != existing.get("api"):
                existing["api"] = api
            if context_window is not None:
                existing["context_window"] = context_window
            if temperature is not None:
                existing["temperature"] = temperature
            if top_k is not None:
                existing["top_k"] = top_k
            if min_p is not None:
                existing["min_p"] = min_p
            if user_models:
                existing_models = existing.get("models", [])
                existing_by_id = {m["id"]: m for m in existing_models if "id" in m}
                ordered = []
                seen = set()
                for m in user_models:
                    mid = m.get("id")
                    if not mid or mid in seen:
                        continue
                    item = dict(existing_by_id.get(mid, {}))
                    item.update({k: v for k, v in m.items() if v is not None})
                    ordered.append(item)
                    seen.add(mid)
                for m in existing_models:
                    mid = m.get("id")
                    if mid and mid not in seen:
                        ordered.append(m)
                existing["models"] = ordered
        else:
            entry = {
                "api":          api,
                "api_key_spec": api_key_spec,
                "base_url":     base_url,
                "models":       user_models,
            }
            if context_window is not None:
                entry["context_window"] = context_window
            if temperature is not None:
                entry["temperature"] = temperature
            if top_k is not None:
                entry["top_k"] = top_k
            if min_p is not None:
                entry["min_p"] = min_p
            merged[name] = entry
    return merged


# ── Build public dicts ─────────────────────────────────────────────────────────

def _build(providers: dict) -> tuple[dict, dict, dict, dict]:
    models: dict[str, list[str]]       = {}
    model_provider: dict[str, str]     = {}
    model_config: dict[str, ModelConfig] = {}
    provider_config: dict[str, ProviderConfig] = {}

    for provider_id, pcfg in providers.items():
        api          = pcfg.get("api", "openai-compatible")
        api_key_spec = pcfg.get("api_key_spec", "")
        base_url     = pcfg.get("base_url") or pcfg.get("baseUrl")
        provider_window = _parse_context_window(
            pcfg.get("context_window", pcfg.get("contextWindow")),
        )
        provider_temperature = _parse_temperature(pcfg.get("temperature"))
        provider_top_k = _parse_top_k(pcfg.get("top_k", pcfg.get("topK")))
        provider_min_p = _parse_min_p(pcfg.get("min_p", pcfg.get("minP")))

        ids: list[str] = []
        for m in pcfg.get("models", []):
            mid = m["id"]
            if mid in model_provider:
                warnings.warn(
                    f"aichs: model {mid!r} already registered under "
                    f"{model_provider[mid]!r}, overriding with {provider_id!r}"
                )
            ids.append(mid)
            model_provider[mid] = provider_id
            model_window = _parse_context_window(
                m.get("contextWindow", m.get("context_window")),
            ) or provider_window
            model_temperature = _coalesce_config_value(
                _parse_temperature(m.get("temperature")),
                provider_temperature,
            )
            model_top_k = _coalesce_config_value(
                _parse_top_k(m.get("topK", m.get("top_k"))),
                provider_top_k,
            )
            model_min_p = _coalesce_config_value(
                _parse_min_p(m.get("minP", m.get("min_p"))),
                provider_min_p,
            )
            model_config[mid] = ModelConfig(
                provider_id=provider_id,
                api=api,
                base_url=base_url,
                api_key_spec=api_key_spec,
                display_name=m.get("name", mid),
                context_window=model_window,
                temperature=model_temperature,
                top_k=model_top_k,
                min_p=model_min_p,
            )
        models[provider_id] = ids
        provider_config[provider_id] = ProviderConfig(
            provider_id=provider_id,
            api=api,
            base_url=base_url,
            api_key_spec=api_key_spec,
            model_ids=ids,
            context_window=provider_window,
            temperature=provider_temperature,
            top_k=provider_top_k,
            min_p=provider_min_p,
        )

    return models, model_provider, model_config, provider_config


_providers = _merge(_BUILTIN, _load_user_providers())
MODELS, MODEL_PROVIDER, _MODEL_CONFIG, PROVIDERS = _build(_providers)

_FALLBACK = ModelConfig(
    provider_id="openai",
    api="openai-compatible",
    base_url=None,
    api_key_spec="OPENAI_API_KEY",
    display_name="unknown",
)


def normalize_model_id(model_id: str) -> str:
    """Strip a trailing settings-style context suffix (``id @ 32768``) if present."""
    return _MODEL_ID_CONTEXT_SUFFIX.sub("", str(model_id or "").strip())


def get_model_config(model_id: str) -> ModelConfig:
    model_id = normalize_model_id(model_id)
    return _MODEL_CONFIG.get(model_id, _FALLBACK)


def get_provider_config(provider_id: str) -> ProviderConfig | None:
    return PROVIDERS.get(provider_id)


def custom_default_context_window() -> int:
    """Default context size for custom (non built-in) openai-compatible models."""
    return 32_768


def _fetch_anthropic_context_window(cfg: ModelConfig, model_id: str) -> int | None:
    if not resolve_api_key(cfg.api_key_spec):
        return None
    try:
        import anthropic

        kwargs: dict = {"api_key": resolve_api_key(cfg.api_key_spec)}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        info = anthropic.Anthropic(**kwargs).models.retrieve(model_id)
        if info.max_input_tokens:
            return int(info.max_input_tokens)
    except Exception:
        return None
    return None


def _refresh_anthropic_context_cache() -> None:
    _ANTHROPIC_CONTEXT.clear()
    for model_id, cfg in _MODEL_CONFIG.items():
        if cfg.api != "anthropic":
            continue
        window = _fetch_anthropic_context_window(cfg, model_id)
        if window:
            _ANTHROPIC_CONTEXT[model_id] = window


def context_window_tokens(model_id: str) -> int:
    """Context window (tokens) for a model."""
    cfg = get_model_config(model_id)
    if cfg.api == "anthropic":
        return _ANTHROPIC_CONTEXT.get(model_id, api_default_context_window("anthropic"))
    if cfg.context_window:
        return cfg.context_window
    if cfg.provider_id not in _BUILTIN_PROVIDER_IDS:
        return custom_default_context_window()
    return api_default_context_window(cfg.api)


def reload() -> None:
    """Reload built-ins plus ~/.aichs/models.json, preserving public dict objects."""
    global _providers
    _providers = _merge(_BUILTIN, _load_user_providers())
    models, model_provider, model_config, provider_config = _build(_providers)
    MODELS.clear()
    MODELS.update(models)
    MODEL_PROVIDER.clear()
    MODEL_PROVIDER.update(model_provider)
    _MODEL_CONFIG.clear()
    _MODEL_CONFIG.update(model_config)
    PROVIDERS.clear()
    PROVIDERS.update(provider_config)
    _refresh_anthropic_context_cache()


_refresh_anthropic_context_cache()
