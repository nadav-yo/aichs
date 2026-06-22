import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from PyQt6.QtCore import QThread, pyqtSignal

from config import SYSTEM_PROMPT
from services.model_registry import context_window_tokens, get_model_config, resolve_api_key
from services.model_requests import apply_generation_params
from services.content import content_length, content_preview
from services.continuation import (
    continuation_prompt,
    parse_continuation_ledger,
    render_continuation_ledger,
)

# API defaults when no provider/model contextWindow is configured (see model_registry).
CONTEXT_WINDOWS = {"anthropic": 180_000, "openai-compatible": 100_000}

# Pi-style defaults (see earendil-works/pi compaction settings).
DEFAULT_RESERVE_TOKENS = 16_384
DEFAULT_KEEP_RECENT_TOKENS = 20_000
MIN_RESERVE_TOKENS = 2_048
MIN_KEEP_RECENT_TOKENS = 2_048
RESERVE_TOKENS = DEFAULT_RESERVE_TOKENS  # legacy import name

SUMMARY_PROMPT = """\
Summarize this earlier coding-agent conversation so the next call can continue the task.

Keep: user goal, decisions, key files/commands/tests, changes made, failures, next step.
Drop: chatter, duplicate tool output, raw file contents.
Use concise bullets grouped by topic."""


@dataclass(frozen=True)
class CompactionResult:
    messages: list[dict]
    summary: str
    cut_index: int
    status: str
    proof: dict
    artifact: dict | None = None


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


def _prompt_settings() -> dict:
    from storage.settings import SettingsStore

    return SettingsStore().load()


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


def can_compact(messages: list[dict], model: str, *, force: bool = False) -> bool:
    return _find_cut_point(messages, model, force=force) > 0


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
    result = _safe_cut_after_turn(messages, cut)
    if result <= 0 or result >= len(messages):
        return 0
    return result


def _safe_cut_after_turn(messages: list[dict], assistant_index: int) -> int:
    if assistant_index < 0 or assistant_index >= len(messages):
        return 0
    cut = assistant_index + 1
    if _has_openai_tool_calls(messages[assistant_index]):
        while cut < len(messages) and messages[cut].get("role") == "tool":
            cut += 1
    return cut


def _has_openai_tool_calls(message: dict) -> bool:
    return message.get("role") == "assistant" and bool(message.get("tool_calls"))


def _cut_at_recent_budget(messages: list[dict], keep_recent: int) -> int:
    accumulated = 0
    for i in range(len(messages) - 1, -1, -1):
        accumulated += content_length(messages[i].get("content", "")) // 4
        if accumulated >= keep_recent:
            return _snap_cut_index(messages, i + 1)
    return 0


def _find_cut_point(messages: list[dict], model: str, *, force: bool = False) -> int:
    """Return the index of the first message to keep verbatim.

    Walks backward until keep_recent_tokens is reached, then snaps to an assistant
    turn boundary (Pi-style). If forced, summarizes an older completed turn even
    when the thread still fits in context. If the thread is shorter than that
    budget but still large enough to summarize, keeps roughly half at a turn
    boundary.
    """
    if len(messages) < 3:
        if force:
            return _forced_cut_point(messages)
        return 0

    window = context_window_tokens(model)
    keep = keep_recent_tokens(window)
    cut = _cut_at_recent_budget(messages, keep)
    if cut > 0:
        return cut

    if force:
        return _forced_cut_point(messages)

    total = _estimate_tokens(messages)
    if total <= keep // 2:
        return 0
    fallback_keep = max(MIN_KEEP_RECENT_TOKENS, total // 2)
    return _cut_at_recent_budget(messages, fallback_keep)


def _forced_cut_point(messages: list[dict]) -> int:
    """Cut an older completed assistant turn for manual compaction.

    Manual /compact is often used before a large task, while the thread still
    fits comfortably. In that case we summarize roughly the older half, but only
    at a completed assistant boundary. If the conversation ends on an assistant
    turn, it may summarize the whole completed history so a restored old chat can
    be compacted before the next task.
    """
    total = max(1, _estimate_tokens(messages))
    target = max(1, total // 2)
    accumulated = 0
    fallback = 0
    for i, msg in enumerate(messages):
        accumulated += content_length(msg.get("content", "")) // 4
        if msg.get("role") != "assistant":
            continue
        cut = _safe_cut_after_turn(messages, i)
        if cut >= len(messages) and messages[-1].get("role") != "assistant":
            continue
        fallback = cut
        if accumulated >= target:
            return cut
    return fallback


def _call_model(model: str, prompt: str, max_tokens: int) -> str:
    cfg    = get_model_config(model)
    kwargs: dict = {"api_key": resolve_api_key(cfg.api_key_spec)}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url

    if cfg.api == "anthropic":
        client = _anthropic_client(**kwargs)
        request = {
            "model": model,
            "max_tokens": max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }
        apply_generation_params(request, cfg, include_extra_body=False)
        resp = client.messages.create(**request)
        return resp.content[0].text
    else:
        client = _openai_client(**kwargs)
        request = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
        }
        apply_generation_params(request, cfg)
        resp = client.chat.completions.create(**request)
        return resp.choices[0].message.content


def _anthropic_client(**kwargs):
    import anthropic

    return anthropic.Anthropic(**kwargs)


def _openai_client(**kwargs):
    from openai import OpenAI

    return OpenAI(**kwargs)


def _summary_prompt(transcript: str) -> str:
    from storage.settings import compaction_summary_guidance

    parts = [SUMMARY_PROMPT]
    guidance = compaction_summary_guidance(_prompt_settings())
    if guidance:
        parts.extend(["Additional user guidance:", guidance])
    parts.extend(["---", transcript])
    return "\n\n".join(parts)


def compact_with_result(
    model: str,
    messages: list[dict],
    *,
    force: bool = False,
    source: str = "core",
    ledger: bool = False,
) -> CompactionResult:
    """Return compacted history plus provenance and optional continuation artifact."""
    cut = _find_cut_point(messages, model, force=force)
    if cut <= 0:
        return CompactionResult(
            messages=messages,
            summary="",
            cut_index=0,
            status="unchanged",
            proof=_compaction_proof(model, messages, 0, source, "unchanged"),
            artifact=None,
        )

    to_summarize = messages[:cut]
    to_keep      = messages[cut:]

    transcript = "\n\n".join(
        f"{m['role'].upper()}: {content_preview(m['content'])}" for m in to_summarize
    )
    window = context_window_tokens(model)
    prompt = continuation_prompt(transcript) if ledger else _summary_prompt(transcript)
    summary = _call_model(
        model,
        prompt,
        summary_max_tokens(window),
    )
    artifact = None
    rendered_summary = summary
    if ledger:
        validation = parse_continuation_ledger(summary)
        if not validation.ok or validation.ledger is None:
            raise ValueError(f"invalid continuation ledger: {'; '.join(validation.errors)}")
        artifact = validation.ledger
        rendered_summary = render_continuation_ledger(validation.ledger)

    compacted = [
        {"role": "user",      "content": f"[Conversation summary]\n{rendered_summary}"},
        {"role": "assistant", "content": "Thank you for the context — I'm fully caught up."},
        *to_keep,
    ]
    return CompactionResult(
        messages=compacted,
        summary=rendered_summary,
        cut_index=cut,
        status="compacted",
        proof=_compaction_proof(model, messages, cut, source, "compacted"),
        artifact=artifact,
    )


def compact(model: str, messages: list[dict], *, force: bool = False) -> list[dict]:
    """Return a compacted history: summary synthetic turn + recent verbatim messages."""
    return compact_with_result(model, messages, force=force).messages


def _compaction_proof(
    model: str,
    messages: list[dict],
    cut_index: int,
    source: str,
    status: str,
) -> dict:
    payload = json.dumps(messages[:cut_index], sort_keys=True, ensure_ascii=False, default=str)
    return {
        "version": "aicc-compaction/v1",
        "source": source,
        "status": status,
        "model": model,
        "cut_index": cut_index,
        "message_count": len(messages),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary_input_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


class CompactionThread(QThread):
    done  = pyqtSignal(list)   # emits the compacted history
    error = pyqtSignal(str)

    def __init__(self, model: str, history: list, *, force: bool = False):
        super().__init__()
        self.model   = model
        self.history = list(history)
        self.force   = force

    def run(self):
        try:
            self.done.emit(compact(self.model, self.history, force=self.force))
        except Exception as exc:
            self.error.emit(str(exc))
