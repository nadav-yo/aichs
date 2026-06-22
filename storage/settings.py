import json
import os

from config import SETTINGS_PATH


FILE_EDITOR_AUTO_SAVE_KEY = "file_editor_auto_save"
FILE_EDITOR_TAB_SPACES_KEY = "file_editor_tab_spaces"
DEFAULT_FILE_EDITOR_TAB_SPACES = 4
MIN_FILE_EDITOR_TAB_SPACES = 1
MAX_FILE_EDITOR_TAB_SPACES = 12
FILE_REVIEW_PROMPT_TEMPLATE_KEY = "file_review_prompt_template"
DEFAULT_FILE_REVIEW_PROMPT_TEMPLATE = "Review {mention} for bugs, regressions, and missing tests."
DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY = "diagnostic_fix_prompt_template"
DEFAULT_DIAGNOSTIC_FIX_PROMPT_TEMPLATE = (
    "Run {command}, then fix every issue reported by {tool} in {file}."
)
GIT_FIX_PROMPT_TEMPLATE_KEY = "git_fix_prompt_template"
DEFAULT_GIT_FIX_PROMPT_TEMPLATE = "Diagnose this git {action} failure and suggest a fix."
AUTO_TITLE_PROMPT_INSTRUCTIONS_KEY = "auto_title_prompt_instructions"
DEFAULT_AUTO_TITLE_PROMPT_INSTRUCTIONS = """\
Write a sidebar chat label (2-6 words). Name the task or topic, not the user's wording.
Short noun phrase; verb + object when possible. No files, paths, @mentions, or quotes.
No questions. Avoid "Help with" or "Question about". Reply with the title only.

Examples:
- "fix dropdown padding when open?" → Fix dropdown padding
- "review @services/auto_title.py" → Auto title prompt
- "header differs on Files vs Chats" → Header tab consistency"""
DEFAULT_AUTO_TITLE_PROMPT = f"""\
{DEFAULT_AUTO_TITLE_PROMPT_INSTRUCTIONS}

First user message:
{{user}}"""
COMPACT_RESUME_PROMPT_KEY = "compact_resume_prompt"
DEFAULT_COMPACT_RESUME_PROMPT = "Continue from the compacted summary. Pick up the next step."
COMPACTION_SUMMARY_GUIDANCE_KEY = "compaction_summary_guidance"
ARCHIVIST_PROMPT_KEY = "archivist_prompt"
COMMIT_MESSAGE_PROMPT_ADDITION_KEY = "commit_message_prompt_addition"
DEFAULT_ARCHIVIST_PROMPT = (
    "Act as Archivist. Recall durable decisions, open threads, and context worth keeping. "
    "Use read_project_chat for dropped references and search_project_chats to search memory. "
    "Be concise; cite chat titles or ids when useful."
)
GRAPH_AGENT_PROMPT_KEY = "graph_agent_prompt"
GRAPH_GENERATION_STRATEGY_KEY = "graph_generation_strategy"
DEFAULT_GRAPH_GENERATION_STRATEGY = "parallelism"
_VALID_GRAPH_GENERATION_STRATEGIES = frozenset({"parallelism", "atomicity"})
CANVAS_RUN_MODE_KEY = "canvas_run_mode"
DEFAULT_CANVAS_RUN_MODE = "sequential"
_VALID_CANVAS_RUN_MODES = frozenset({"sequential", "parallel"})
CANVAS_PARALLEL_LIMIT_KEY = "canvas_parallel_limit"
DEFAULT_CANVAS_PARALLEL_LIMIT = 2
MIN_CANVAS_PARALLEL_LIMIT = 1
MAX_CANVAS_PARALLEL_LIMIT = 6
CANVAS_ACTION_AUTO_APPROVE_KEY = "canvas_action_auto_approve"
DEFAULT_CANVAS_ACTION_AUTO_APPROVE = "never"
_VALID_CANVAS_ACTION_AUTO_APPROVE = frozenset({"never", "coder", "all"})
DEFAULT_GRAPH_AGENT_PROMPT = """\
You are the Intent Graph agent.
Your job is to help the user shape mega-feature work as an acyclic graph, not as a transcript, checklist, or one-chat prompt.
Use the current graph as the source of truth. Query it before suggesting changes.
When planning or generating steps, design how agents should research, implement, review, and verify the goal. Do not perform that research, implementation, review, or verification yourself.
Use the graph when the feature benefits from decomposition: multiple responsibilities, unknowns, decision points, context boundaries, file scopes, review paths, or acceptance evidence.
Use web_fetch only for graph-planning research: external product/domain context, public docs, examples, or constraints that help shape the graph. Do not use fetched content as implementation proof or claim implementation work is done.
If the goal can be summarized as one straightforward chat prompt, do not inflate it into Analyze -> Implement -> Verify. Keep it minimal or ask the user what larger breakdown they want.
Prefer small graph edits: add a goal, add work, attach files as scope, assign crew, add evidence, add a decision, connect valid components, autoformat.
Keep generated plans compact: usually 3-5 new nodes total, and fewer is better when the graph already has useful structure. Do not overcomplicate the graph to show off every possible component.
Use a straight flow when the work is naturally sequential. Branch only when it clarifies real parallel work, alternatives, dependencies, review paths, or separate acceptance evidence.
Break down by responsibility, not by generic phases. Prefer nodes like product decision, architecture decision, UX flow, API surface, state/persistence, integration, validation, review, or rollout when those responsibilities are actually distinct.
Reuse and connect existing nodes before adding new ones. Do not create duplicate actions, duplicate file/context nodes, or one box per sentence.
Only create a node when it has a distinct responsibility, input, or output in the workflow.
Ask concise questions about design details: user-facing behavior, product intent, UX priorities, acceptance criteria, constraints, risk tolerance, or business tradeoffs. Ask one focused question per turn, but use multiple question turns when each answer can change the graph shape.
Do not ask the user to choose implementation details such as engines, frameworks, libraries, file paths, or technical approaches during Generate Steps. Represent those as architecture/research/decision work for the crew unless the user explicitly made the technical choice the goal.
Never create a directed cycle. If the user asks for a cyclic relationship, explain the cycle and suggest a non-cyclic alternative.
Keep the graph high-level: do not add boxes just to mirror every sentence."""
TRASH_RETENTION_DAYS_KEY = "trash_retention_days"
DEFAULT_TRASH_RETENTION_DAYS = 14
GIT_PANEL_MODE_KEY = "git_panel_mode"
GIT_PANEL_LISTS_SPLIT_KEY = "git_panel_lists_split"
GIT_PANEL_BODY_EXPANDED_KEY = "git_panel_body_expanded"
DEFAULT_GIT_PANEL_LISTS_SPLIT = [120, 220]
RESUME_SESSION_KEY = "resume_session"
DEFAULT_RESUME_SESSION = "always"
_VALID_RESUME_SESSION = frozenset({"always", "ask", "never"})

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


def graph_agent_prompt(data: dict | None) -> str:
    return _text_setting(data, GRAPH_AGENT_PROMPT_KEY, DEFAULT_GRAPH_AGENT_PROMPT)


def graph_generation_strategy(data: dict | None) -> str:
    data = data if isinstance(data, dict) else {}
    value = str(data.get(GRAPH_GENERATION_STRATEGY_KEY, DEFAULT_GRAPH_GENERATION_STRATEGY) or "").strip().lower()
    return value if value in _VALID_GRAPH_GENERATION_STRATEGIES else DEFAULT_GRAPH_GENERATION_STRATEGY


def canvas_run_mode(data: dict | None) -> str:
    data = data if isinstance(data, dict) else {}
    value = str(data.get(CANVAS_RUN_MODE_KEY, DEFAULT_CANVAS_RUN_MODE) or "").strip().lower()
    return value if value in _VALID_CANVAS_RUN_MODES else DEFAULT_CANVAS_RUN_MODE


def canvas_parallel_limit(data: dict | None) -> int:
    data = data if isinstance(data, dict) else {}
    try:
        value = int(data.get(CANVAS_PARALLEL_LIMIT_KEY, DEFAULT_CANVAS_PARALLEL_LIMIT))
    except (TypeError, ValueError):
        value = DEFAULT_CANVAS_PARALLEL_LIMIT
    return max(MIN_CANVAS_PARALLEL_LIMIT, min(MAX_CANVAS_PARALLEL_LIMIT, value))


def canvas_action_auto_approve(data: dict | None) -> str:
    data = data if isinstance(data, dict) else {}
    value = str(data.get(CANVAS_ACTION_AUTO_APPROVE_KEY, DEFAULT_CANVAS_ACTION_AUTO_APPROVE) or "").strip().lower()
    return value if value in _VALID_CANVAS_ACTION_AUTO_APPROVE else DEFAULT_CANVAS_ACTION_AUTO_APPROVE


def git_panel_mode(data: dict | None) -> str:
    data = data if isinstance(data, dict) else {}
    mode = str(data.get(GIT_PANEL_MODE_KEY, "changes") or "changes").strip().lower()
    if mode == "sync":
        return "changes"
    if mode not in {"changes", "history"}:
        return "changes"
    return mode


def git_panel_lists_split(data: dict | None) -> list[int]:
    data = data if isinstance(data, dict) else {}
    raw = data.get(GIT_PANEL_LISTS_SPLIT_KEY, DEFAULT_GIT_PANEL_LISTS_SPLIT)
    if not isinstance(raw, list) or len(raw) != 2:
        return list(DEFAULT_GIT_PANEL_LISTS_SPLIT)
    try:
        return [max(40, int(raw[0])), max(40, int(raw[1]))]
    except (TypeError, ValueError):
        return list(DEFAULT_GIT_PANEL_LISTS_SPLIT)


def git_panel_body_expanded(data: dict | None) -> bool:
    data = data if isinstance(data, dict) else {}
    return bool(data.get(GIT_PANEL_BODY_EXPANDED_KEY, False))


def resume_session(data: dict | None) -> str:
    data = data if isinstance(data, dict) else {}
    mode = str(data.get(RESUME_SESSION_KEY, DEFAULT_RESUME_SESSION) or DEFAULT_RESUME_SESSION).strip().lower()
    if mode not in _VALID_RESUME_SESSION:
        return DEFAULT_RESUME_SESSION
    return mode


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

    def apply(self) -> None:
        """Load settings at startup; only set env vars not already defined externally."""
        data = self.load()
        self._apply_provider_keys(data, overwrite=False)

    def apply_saved(self, data: dict) -> None:
        """Apply keys after the user saves in the settings dialog."""
        self._apply_provider_keys(data, overwrite=True)
