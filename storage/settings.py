import json
import os

from config import SETTINGS_PATH


FILE_EDITOR_AUTO_SAVE_KEY = "file_editor_auto_save"
TRASH_RETENTION_DAYS_KEY = "trash_retention_days"
DEFAULT_TRASH_RETENTION_DAYS = 14

_LEGACY_PROVIDER_KEYS = {
    "claude": "anthropic_api_key",
    "openai": "openai_api_key",
}


def trash_retention_days(data: dict | None) -> int:
    data = data if isinstance(data, dict) else {}
    try:
        days = int(data.get(TRASH_RETENTION_DAYS_KEY, DEFAULT_TRASH_RETENTION_DAYS))
    except (TypeError, ValueError):
        days = DEFAULT_TRASH_RETENTION_DAYS
    return max(1, min(3650, days))


class SettingsStore:
    def __init__(self):
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict:
        if not SETTINGS_PATH.exists():
            return {}
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except Exception:
            return {}

    def save(self, data: dict) -> None:
        SETTINGS_PATH.write_text(json.dumps(data, indent=2))

    def update(self, partial: dict) -> dict:
        data = self.load()
        data.update(partial)
        self.save(data)
        return data

    def _saved_provider_key(self, data: dict, provider: str) -> str:
        provider_keys = data.get("provider_api_keys", {})
        if provider in provider_keys:
            return str(provider_keys.get(provider, "")).strip()
        legacy_key = _LEGACY_PROVIDER_KEYS.get(provider)
        if legacy_key:
            return str(data.get(legacy_key, "")).strip()
        return ""

    def _apply_provider_keys(self, data: dict, *, overwrite: bool) -> None:
        from services.model_registry import MODELS, api_key_env_var, get_provider_config

        for provider in MODELS:
            cfg = get_provider_config(provider)
            if not cfg:
                continue
            env_var = api_key_env_var(cfg.api_key_spec)
            if not env_var:
                continue
            key = self._saved_provider_key(data, provider)
            if key:
                if overwrite or not os.environ.get(env_var):
                    os.environ[env_var] = key
            elif overwrite and provider in _LEGACY_PROVIDER_KEYS:
                os.environ.pop(env_var, None)

    def apply(self) -> None:
        """Load settings at startup; only set env vars not already defined externally."""
        data = self.load()
        self._apply_provider_keys(data, overwrite=False)

    def apply_saved(self, data: dict) -> None:
        """Apply keys after the user saves in the settings dialog."""
        self._apply_provider_keys(data, overwrite=True)
