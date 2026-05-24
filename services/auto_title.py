import re

import anthropic
from openai import OpenAI
from PyQt6.QtCore import QThread, pyqtSignal

from services.content import content_preview
from services.model_registry import get_model_config, resolve_api_key

# Fast, cheap models to use for title generation; keyed by api type.
_TITLE_MODELS: dict[str, str] = {
    "anthropic":         "claude-haiku-4-5-20251001",
    "openai-compatible": "gpt-4.1-mini",
}

TITLE_PROMPT = """\
Write a short conversation title (5–7 words). No quotes, no punctuation at the end.
Capture the main topic or emotional theme. Reply with the title only.

USER:
{user}

A:
{assistant}"""


def generate_title(model: str, user_text: str, assistant_text: str) -> str:
    cfg         = get_model_config(model)
    title_model = _TITLE_MODELS.get(cfg.api)
    if not title_model:
        raise ValueError(f"No title model for api type: {cfg.api!r}")

    # Reuse the same provider config (key, base_url) as the conversation model.
    kwargs: dict = {"api_key": resolve_api_key(cfg.api_key_spec)}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url

    prompt = TITLE_PROMPT.format(
        user=content_preview(user_text)[:1500],
        assistant=content_preview(assistant_text)[:1500],
    )

    if cfg.api == "anthropic":
        client = anthropic.Anthropic(**kwargs)
        resp   = client.messages.create(
            model=title_model,
            max_tokens=32,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text
    else:
        client = OpenAI(**kwargs)
        resp   = client.chat.completions.create(
            model=title_model,
            max_tokens=32,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.choices[0].message.content or ""

    return clean_title(raw)


def clean_title(raw: str) -> str:
    t = raw.strip().strip("\"'").split("\n")[0].strip()
    t = re.sub(r"^(title:\s*)+", "", t, flags=re.I).strip()
    t = re.sub(r"\s+", " ", t)
    if len(t) > 60:
        t = t[:57].rstrip() + "…"
    return t or "Untitled"


class TitleThread(QThread):
    done  = pyqtSignal(str, str)   # conv_id, title
    error = pyqtSignal(str)

    def __init__(self, conv_id: str, model: str, user_text: str, assistant_text: str):
        super().__init__()
        self.conv_id        = conv_id
        self.model          = model
        self.user_text      = user_text
        self.assistant_text = assistant_text

    def run(self):
        try:
            title = generate_title(self.model, self.user_text, self.assistant_text)
            self.done.emit(self.conv_id, title)
        except Exception as exc:
            self.error.emit(str(exc))
