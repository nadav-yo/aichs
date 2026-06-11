import json
import os

from config import SETTINGS_PATH


FILE_EDITOR_AUTO_SAVE_KEY = "file_editor_auto_save"
FILE_EDITOR_TAB_SPACES_KEY = "file_editor_tab_spaces"
DEFAULT_FILE_EDITOR_TAB_SPACES = 4
MIN_FILE_EDITOR_TAB_SPACES = 1
MAX_FILE_EDITOR_TAB_SPACES = 12
FILE_REVIEW_PROMPT_TEMPLATE_KEY = "file_review_prompt_template"
DEFAULT_FILE_REVIEW_PROMPT_TEMPLATE = "Please review {mention}."
DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY = "diagnostic_fix_prompt_template"
DEFAULT_DIAGNOSTIC_FIX_PROMPT_TEMPLATE = "Please fix this diagnostic in {mention}."
GIT_FIX_PROMPT_TEMPLATE_KEY = "git_fix_prompt_template"
DEFAULT_GIT_FIX_PROMPT_TEMPLATE = "Help me diagnose this git {action} failure."
AUTO_TITLE_PROMPT_INSTRUCTIONS_KEY = "auto_title_prompt_instructions"
DEFAULT_AUTO_TITLE_PROMPT_INSTRUCTIONS = """\
Write a short conversation title (5-7 words). No quotes, no punctuation at the end.
Capture the main topic from the first user message. Reply with the title only."""
DEFAULT_AUTO_TITLE_PROMPT = f"""\
{DEFAULT_AUTO_TITLE_PROMPT_INSTRUCTIONS}

First user message:
{{user}}"""
COMPACT_RESUME_PROMPT_KEY = "compact_resume_prompt"
DEFAULT_COMPACT_RESUME_PROMPT = "Continue the active task from the compacted context."
COMPACTION_SUMMARY_GUIDANCE_KEY = "compaction_summary_guidance"
ARCHIVIST_PROMPT_KEY = "archivist_prompt"
COMMIT_MESSAGE_PROMPT_ADDITION_KEY = "commit_message_prompt_addition"
DEFAULT_ARCHIVIST_PROMPT = (
    "Act as Archivist for this turn. Focus on saved chat memory, durable decisions, "
    "open threads, and context worth carrying forward. Use read_project_chat for exact "
    "dropped chat references and search_project_chats for fuzzy memory lookup. Keep the "
    "answer concise and cite conversation titles or ids when useful."
)
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


def file_editor_tab_spaces(data: dict | None) -> int:
    data = data if isinstance(data, dict) else {}
    try:
        spaces = int(data.get(FILE_EDITOR_TAB_SPACES_KEY, DEFAULT_FILE_EDITOR_TAB_SPACES))
    except (TypeError, ValueError):
        spaces = DEFAULT_FILE_EDITOR_TAB_SPACES
    return max(MIN_FILE_EDITOR_TAB_SPACES, min(MAX_FILE_EDITOR_TAB_SPACES, spaces))


def file_review_prompt_template(data: dict | None) -> str:
    return _text_setting(
        data,
        FILE_REVIEW_PROMPT_TEMPLATE_KEY,
        DEFAULT_FILE_REVIEW_PROMPT_TEMPLATE,
    )


def diagnostic_fix_prompt_template(data: dict | None) -> str:
    return _text_setting(
        data,
        DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY,
        DEFAULT_DIAGNOSTIC_FIX_PROMPT_TEMPLATE,
    )


def git_fix_prompt_template(data: dict | None) -> str:
    return _text_setting(
        data,
        GIT_FIX_PROMPT_TEMPLATE_KEY,
        DEFAULT_GIT_FIX_PROMPT_TEMPLATE,
    )


def auto_title_prompt_instructions(data: dict | None) -> str:
    return _text_setting(
        data,
        AUTO_TITLE_PROMPT_INSTRUCTIONS_KEY,
        DEFAULT_AUTO_TITLE_PROMPT_INSTRUCTIONS,
    )


def compact_resume_prompt(data: dict | None) -> str:
    return _text_setting(
        data,
        COMPACT_RESUME_PROMPT_KEY,
        DEFAULT_COMPACT_RESUME_PROMPT,
    )


def compaction_summary_guidance(data: dict | None) -> str:
    return _text_setting(data, COMPACTION_SUMMARY_GUIDANCE_KEY, "")


def archivist_prompt(data: dict | None) -> str:
    return _text_setting(data, ARCHIVIST_PROMPT_KEY, DEFAULT_ARCHIVIST_PROMPT)


def _text_setting(data: dict | None, key: str, default: str) -> str:
    data = data if isinstance(data, dict) else {}
    value = str(data.get(key, "")).strip()
    return value or default


class SettingsStore:
    _ensured_dirs: set[str] = set()
    _cache_path: str = ""
    _cache_mtime_ns: int | None = None
    _cache_size: int | None = None
    _cache_text: str | None = None
    _cache_data: dict | None = None

    def __init__(self):
        parent = str(SETTINGS_PATH.parent)
        if parent not in self._ensured_dirs:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._ensured_dirs.add(parent)

    def load(self) -> dict:
        try:
            stat = SETTINGS_PATH.stat()
        except OSError:
            self._remember_cache(None, None, None, None)
            return {}
        path = str(SETTINGS_PATH)
        try:
            text = SETTINGS_PATH.read_text()
        except Exception:
            self._remember_cache(stat.st_mtime_ns, stat.st_size, None, {})
            return {}
        if (
            self._cache_data is not None
            and self._cache_path == path
            and self._cache_mtime_ns == stat.st_mtime_ns
            and self._cache_size == stat.st_size
            and self._cache_text == text
        ):
            return dict(self._cache_data)
        try:
            data = json.loads(text)
        except Exception:
            self._remember_cache(stat.st_mtime_ns, stat.st_size, text, {})
            return {}
        data = data if isinstance(data, dict) else {}
        self._remember_cache(stat.st_mtime_ns, stat.st_size, text, data)
        return dict(data)

    def save(self, data: dict) -> None:
        text = json.dumps(data, indent=2)
        SETTINGS_PATH.write_text(text)
        try:
            stat = SETTINGS_PATH.stat()
        except OSError:
            self._remember_cache(None, None, None, None)
            return
        self._remember_cache(stat.st_mtime_ns, stat.st_size, text, data if isinstance(data, dict) else {})

    def update(self, partial: dict) -> dict:
        data = self.load()
        data.update(partial)
        self.save(data)
        return data

    @classmethod
    def _remember_cache(
        cls,
        mtime_ns: int | None,
        size: int | None,
        text: str | None,
        data: dict | None,
    ) -> None:
        cls._cache_path = str(SETTINGS_PATH)
        cls._cache_mtime_ns = mtime_ns
        cls._cache_size = size
        cls._cache_text = text
        cls._cache_data = dict(data) if isinstance(data, dict) else None

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
