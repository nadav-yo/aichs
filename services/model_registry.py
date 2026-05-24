"""Model registry — loads built-in providers and merges ~/.aicc/models.json on top.

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
import subprocess
import warnings
from dataclasses import dataclass
from pathlib import Path

_MODELS_PATH = Path.home() / ".aicc" / "models.json"

_BUILTIN: dict = {
    "claude": {
        "api": "anthropic",
        "api_key_spec": "ANTHROPIC_API_KEY",
        "models": [
            {"id": "claude-opus-4-7"},
            {"id": "claude-sonnet-4-6"},
            {"id": "claude-haiku-4-5-20251001"},
        ],
    },
    "openai": {
        "api": "openai-compatible",
        "api_key_spec": "OPENAI_API_KEY",
        "models": [
            {"id": "gpt-4.1"},
            {"id": "gpt-4.1-mini"},
            {"id": "gpt-4o"},
            {"id": "gpt-4o-mini"},
            {"id": "o3"},
            {"id": "o4-mini"},
        ],
    },
}

_VALID_APIS = {"anthropic", "openai-compatible"}


@dataclass
class ModelConfig:
    provider_id:  str
    api:          str         # "anthropic" | "openai-compatible"
    base_url:     str | None  # None = SDK default
    api_key_spec: str         # env var name, "!command", or literal key
    display_name: str


@dataclass
class ProviderConfig:
    provider_id:  str
    api:          str
    base_url:     str | None
    api_key_spec: str
    model_ids:    list[str]


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
                spec[1:], shell=True, capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                warnings.warn(f"aicc: API key command failed ({spec[1:]!r}): {result.stderr.strip()}")
                return ""
            return result.stdout.strip()
        except Exception as exc:
            warnings.warn(f"aicc: API key command error: {exc}")
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
        warnings.warn(f"aicc: could not parse ~/.aicc/models.json: {exc}")
        return {}


def load_user_providers() -> dict:
    """Return provider definitions from ~/.aicc/models.json."""
    return copy.deepcopy(_load_user_providers())


def save_user_providers(providers: dict) -> None:
    """Persist provider definitions to ~/.aicc/models.json."""
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
        user_models  = ucfg.get("models", [])

        if api not in _VALID_APIS:
            warnings.warn(f"aicc: unknown api type {api!r} for provider {name!r} — skipping")
            continue

        if name in merged:
            existing = merged[name]
            if api_key_spec:
                existing["api_key_spec"] = api_key_spec
            if base_url is not None:
                existing["base_url"] = base_url
            if api != existing.get("api"):
                existing["api"] = api
            # append new model ids only
            existing_ids = {m["id"] for m in existing.get("models", [])}
            for m in user_models:
                if m["id"] not in existing_ids:
                    existing.setdefault("models", []).append(m)
        else:
            merged[name] = {
                "api":          api,
                "api_key_spec": api_key_spec,
                "base_url":     base_url,
                "models":       user_models,
            }
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

        ids: list[str] = []
        for m in pcfg.get("models", []):
            mid = m["id"]
            if mid in model_provider:
                warnings.warn(
                    f"aicc: model {mid!r} already registered under "
                    f"{model_provider[mid]!r}, overriding with {provider_id!r}"
                )
            ids.append(mid)
            model_provider[mid] = provider_id
            model_config[mid] = ModelConfig(
                provider_id=provider_id,
                api=api,
                base_url=base_url,
                api_key_spec=api_key_spec,
                display_name=m.get("name", mid),
            )
        models[provider_id] = ids
        provider_config[provider_id] = ProviderConfig(
            provider_id=provider_id,
            api=api,
            base_url=base_url,
            api_key_spec=api_key_spec,
            model_ids=ids,
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


def get_model_config(model_id: str) -> ModelConfig:
    return _MODEL_CONFIG.get(model_id, _FALLBACK)


def get_provider_config(provider_id: str) -> ProviderConfig | None:
    return PROVIDERS.get(provider_id)


def reload() -> None:
    """Reload built-ins plus ~/.aicc/models.json, preserving public dict objects."""
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
