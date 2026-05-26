import anthropic
from openai import OpenAI
from PyQt6.QtCore import QThread, pyqtSignal

from config import SYSTEM_PROMPT
from services.model_registry import context_window_tokens, get_model_config, resolve_api_key
from services.content import content_length, content_preview

# API defaults when no provider/model contextWindow is configured (see model_registry).
CONTEXT_WINDOWS = {"anthropic": 180_000, "openai-compatible": 100_000}

# Pi-style defaults (see earendil-works/pi compaction settings).
DEFAULT_RESERVE_TOKENS = 16_384
DEFAULT_KEEP_RECENT_TOKENS = 20_000
MIN_RESERVE_TOKENS = 2_048
MIN_KEEP_RECENT_TOKENS = 2_048
RESERVE_TOKENS = DEFAULT_RESERVE_TOKENS  # legacy import name

SUMMARY_PROMPT = """\
The following is an earlier portion of a conversation between you (an empathetic AI companion) \
and a user. Summarize it concisely so you can continue the conversation with full context.

Include:
- What the user has been going through emotionally
- Key topics, concerns, and themes discussed
- Important things you've learned about the user
- Where the conversation left off and what the user seemed to need most

Write in first-person ("The user shared…", "We discussed…"). Be concise but preserve nuance."""


def parse_compaction_token(value) -> int | None:
    """Parse a settings/UI token count; None means use automatic scaling."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", "")
        if not value:
            return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _compaction_settings() -> dict:
    from storage.settings import SettingsStore

    raw = SettingsStore().load().get("compaction")
    return raw if isinstance(raw, dict) else {}


def configured_reserve_tokens() -> int | None:
    settings = _compaction_settings()
    return parse_compaction_token(
        settings.get("reserve_tokens", settings.get("reserveTokens")),
    )


def configured_keep_recent_tokens() -> int | None:
    settings = _compaction_settings()
    return parse_compaction_token(
        settings.get("keep_recent_tokens", settings.get("keepRecentTokens")),
    )


def _clamp_token_setting(value: int, window: int) -> int:
    if window > 0:
        return max(MIN_RESERVE_TOKENS, min(value, window))
    return max(MIN_RESERVE_TOKENS, value)


def _scaled_reserve_tokens(window: int) -> int:
    if window <= 0:
        return MIN_RESERVE_TOKENS
    return min(DEFAULT_RESERVE_TOKENS, max(MIN_RESERVE_TOKENS, window // 5))


def _scaled_keep_recent_tokens(window: int, reserve: int) -> int:
    usable = max(MIN_KEEP_RECENT_TOKENS, window - reserve)
    return min(DEFAULT_KEEP_RECENT_TOKENS, max(MIN_KEEP_RECENT_TOKENS, usable // 2))


def reserve_tokens(window: int) -> int:
    """Tokens held back from the context window for the next model response."""
    override = configured_reserve_tokens()
    if override is not None:
        return _clamp_token_setting(override, window)
    return _scaled_reserve_tokens(window)


def keep_recent_tokens(window: int) -> int:
    """Recent history to keep verbatim when compacting (scales down on small windows)."""
    override = configured_keep_recent_tokens()
    if override is not None:
        return _clamp_token_setting(override, window)
    reserve = reserve_tokens(window)
    if window <= 0:
        return MIN_KEEP_RECENT_TOKENS
    return _scaled_keep_recent_tokens(window, reserve)


def compaction_threshold(window: int) -> int:
    """Auto-compact when estimated context exceeds this (Pi: window - reserve)."""
    return max(0, window - reserve_tokens(window))


def summary_max_tokens(window: int) -> int:
    """max_tokens for the compaction summary call (Pi uses ~0.8 * reserve)."""
    reserve = reserve_tokens(window)
    return max(512, min(int(0.8 * reserve), reserve, max(window // 4, 512)))


def _estimate_tokens(messages: list[dict]) -> int:
    return sum(content_length(m.get("content", "")) for m in messages) // 4


def can_compact(messages: list[dict], model: str) -> bool:
    return _find_cut_point(messages, model) > 0


def should_compact(
    model: str,
    messages: list[dict],
    *,
    context_tokens: int | None = None,
) -> bool:
    window = context_window_tokens(model)
    used = context_tokens if context_tokens is not None else _estimate_tokens(messages)
    return used > compaction_threshold(window)


def _snap_cut_index(messages: list[dict], cut: int) -> int:
    while cut < len(messages) and messages[cut]["role"] != "assistant":
        cut += 1
    result = min(cut + 1, len(messages))
    if result <= 0 or result >= len(messages):
        return 0
    return result


def _cut_at_recent_budget(messages: list[dict], keep_recent: int) -> int:
    accumulated = 0
    for i in range(len(messages) - 1, -1, -1):
        accumulated += content_length(messages[i].get("content", "")) // 4
        if accumulated >= keep_recent:
            return _snap_cut_index(messages, i + 1)
    return 0


def _find_cut_point(messages: list[dict], model: str) -> int:
    """Return the index of the first message to keep verbatim.

    Walks backward until keep_recent_tokens is reached, then snaps to an assistant
    turn boundary (Pi-style). If the thread is shorter than that budget but still
    large enough to summarize, keeps roughly half at a turn boundary.
    """
    if len(messages) < 3:
        return 0

    window = context_window_tokens(model)
    keep = keep_recent_tokens(window)
    cut = _cut_at_recent_budget(messages, keep)
    if cut > 0:
        return cut

    total = _estimate_tokens(messages)
    if total <= keep // 2:
        return 0
    fallback_keep = max(MIN_KEEP_RECENT_TOKENS, total // 2)
    return _cut_at_recent_budget(messages, fallback_keep)


def _call_model(model: str, prompt: str, max_tokens: int) -> str:
    cfg    = get_model_config(model)
    kwargs: dict = {"api_key": resolve_api_key(cfg.api_key_spec)}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url

    if cfg.api == "anthropic":
        client = anthropic.Anthropic(**kwargs)
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text
    else:
        client = OpenAI(**kwargs)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
        )
        return resp.choices[0].message.content


def compact(model: str, messages: list[dict]) -> list[dict]:
    """Return a compacted history: summary synthetic turn + recent verbatim messages."""
    cut = _find_cut_point(messages, model)
    if cut <= 0:
        return messages

    to_summarize = messages[:cut]
    to_keep      = messages[cut:]

    transcript = "\n\n".join(
        f"{m['role'].upper()}: {content_preview(m['content'])}" for m in to_summarize
    )
    window = context_window_tokens(model)
    summary = _call_model(
        model,
        f"{SUMMARY_PROMPT}\n\n---\n{transcript}",
        summary_max_tokens(window),
    )

    return [
        {"role": "user",      "content": f"[Conversation summary]\n{summary}"},
        {"role": "assistant", "content": "Thank you for the context — I'm fully caught up."},
        *to_keep,
    ]


class CompactionThread(QThread):
    done  = pyqtSignal(list)   # emits the compacted history
    error = pyqtSignal(str)

    def __init__(self, model: str, history: list):
        super().__init__()
        self.model   = model
        self.history = list(history)

    def run(self):
        try:
            self.done.emit(compact(self.model, self.history))
        except Exception as exc:
            self.error.emit(str(exc))
