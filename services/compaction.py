import anthropic
from openai import OpenAI
from PyQt6.QtCore import QThread, pyqtSignal

from config import SYSTEM_PROMPT
from services.model_registry import get_model_config, resolve_api_key
from services.content import content_length, content_preview
from ui.theme import compaction_threshold_pct

# Mirrors the Pi harness numbers
CONTEXT_WINDOWS  = {"anthropic": 180_000, "openai-compatible": 100_000}  # tokens, conservative
RESERVE_TOKENS   = 16_384   # headroom for the summary prompt + output
KEEP_RECENT_TOKENS = 20_000  # tokens retained verbatim after the cut point

SUMMARY_PROMPT = """\
The following is an earlier portion of a conversation between you (an empathetic AI companion) \
and a user. Summarize it concisely so you can continue the conversation with full context.

Include:
- What the user has been going through emotionally
- Key topics, concerns, and themes discussed
- Important things you've learned about the user
- Where the conversation left off and what the user seemed to need most

Write in first-person ("The user shared…", "We discussed…"). Be concise but preserve nuance."""


def _estimate_tokens(messages: list[dict]) -> int:
    return sum(content_length(m.get("content", "")) for m in messages) // 4


def can_compact(messages: list[dict]) -> bool:
    return _find_cut_point(messages) > 0


def should_compact(model: str, messages: list[dict]) -> bool:
    window = CONTEXT_WINDOWS.get(get_model_config(model).api, 100_000)
    pct = compaction_threshold_pct()
    limit = int(window * pct / 100) - RESERVE_TOKENS
    return _estimate_tokens(messages) > limit


def _find_cut_point(messages: list[dict]) -> int:
    """Return the index of the first message to keep verbatim.

    Works backward, accumulating tokens until KEEP_RECENT_TOKENS is reached,
    then snaps forward to the end of a complete assistant turn so we never
    cut mid-exchange — the same approach Pi uses.
    """
    accumulated = 0
    for i in range(len(messages) - 1, -1, -1):
        accumulated += content_length(messages[i].get("content", "")) // 4
        if accumulated >= KEEP_RECENT_TOKENS:
            # snap to the start of the next assistant turn boundary
            cut = i + 1
            while cut < len(messages) and messages[cut]["role"] != "assistant":
                cut += 1
            return min(cut + 1, len(messages))
    return 0  # everything fits in recent window, nothing to cut


def _call_model(model: str, prompt: str) -> str:
    cfg    = get_model_config(model)
    kwargs: dict = {"api_key": resolve_api_key(cfg.api_key_spec)}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url

    if cfg.api == "anthropic":
        client = anthropic.Anthropic(**kwargs)
        resp = client.messages.create(
            model=model,
            max_tokens=RESERVE_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text
    else:
        client = OpenAI(**kwargs)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=RESERVE_TOKENS,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
        )
        return resp.choices[0].message.content


def compact(model: str, messages: list[dict]) -> list[dict]:
    """Return a compacted history: summary synthetic turn + recent verbatim messages."""
    cut = _find_cut_point(messages)
    if cut <= 0:
        return messages

    to_summarize = messages[:cut]
    to_keep      = messages[cut:]

    transcript = "\n\n".join(
        f"{m['role'].upper()}: {content_preview(m['content'])}" for m in to_summarize
    )
    summary = _call_model(model, f"{SUMMARY_PROMPT}\n\n---\n{transcript}")

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
