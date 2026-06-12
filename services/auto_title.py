import re

from PyQt6.QtCore import QThread, pyqtSignal

from services.content import content_preview
from services.model_registry import get_model_config, resolve_api_key
from services.model_requests import apply_generation_params
from storage.settings import (
    DEFAULT_AUTO_TITLE_PROMPT,
    SettingsStore,
    auto_title_prompt_instructions,
)

_TITLE_MODELS: dict[str, str] = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai-compatible": "gpt-5.4-nano",
}
_BUILTIN_TITLE_PROVIDERS = {"claude", "openai"}
_STOPWORDS = {
    "about", "after", "again", "also", "and", "are", "can", "could", "for",
    "from", "have", "how", "into", "just", "let", "maybe", "need", "please",
    "should", "that", "the", "this", "what", "when", "where", "with", "would",
    "we", "you",
}
_BAD_TITLE_PATTERNS = (
    "awaiting task",
    "ready to proceed",
    "provide task",
    "task instructions",
    "help with",
    "question about",
)
_PATH_TOKEN_RE = re.compile(
    r"\.(py|ts|tsx|js|jsx|md|json|yaml|yml|toml|rs|go|java|cpp|c|h|cs|rb)$",
    re.I,
)
_LEADING_FILLER_RE = re.compile(
    r"^(?:can you|could you|please|how do i|help me|i need to|i want to)\s+",
    re.I,
)

TITLE_PROMPT = DEFAULT_AUTO_TITLE_PROMPT


def generate_title(model: str, user_text: str) -> str:
    cfg = get_model_config(model)
    title_model = _title_model_for(model, cfg)
    if not title_model:
        raise ValueError(f"No title model for api type: {cfg.api!r}")

    kwargs: dict = {"api_key": resolve_api_key(cfg.api_key_spec)}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url

    prompt = _build_title_prompt(user_text)
    if cfg.api == "anthropic":
        client = _anthropic_client(**kwargs)
        request = {
            "model": title_model,
            "max_tokens": 32,
            "messages": [{"role": "user", "content": prompt}],
        }
        apply_generation_params(request, cfg, include_extra_body=False)
        resp = client.messages.create(**request)
        raw = resp.content[0].text
    else:
        client = _openai_client(**kwargs)
        request = {
            "model": title_model,
            "max_tokens": 32,
            "messages": [{"role": "user", "content": prompt}],
        }
        apply_generation_params(request, cfg)
        resp = client.chat.completions.create(**request)
        raw = resp.choices[0].message.content or ""

    title = clean_title(raw)
    if not _is_usable_title(title):
        return fallback_title(user_text)
    return title


def clean_title(raw: str) -> str:
    t = raw.strip().strip("\"'").split("\n")[0].strip()
    t = re.sub(r"^(title:\s*)+", "", t, flags=re.I).strip()
    t = re.sub(r"[?.!;:]+$", "", t).strip()
    t = re.sub(r"\s+", " ", t)
    t = _strip_leading_filler(t)
    t = _strip_path_tokens(t)
    t = _trim_title_words(t, max_words=6)
    if len(t) > 45:
        t = t[:42].rstrip() + "…"
    return t or "Untitled"


def _strip_leading_filler(text: str) -> str:
    current = text.strip()
    while current:
        stripped = _LEADING_FILLER_RE.sub("", current).strip()
        if stripped == current:
            break
        current = stripped
    return current


def _strip_path_tokens(text: str) -> str:
    kept = []
    for word in text.split():
        if "/" in word or "\\" in word or _PATH_TOKEN_RE.search(word):
            continue
        kept.append(word)
    return " ".join(kept) if kept else text


def _trim_title_words(text: str, *, max_words: int = 6) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])


def _build_title_prompt(user_text: str) -> str:
    instructions = auto_title_prompt_instructions(SettingsStore().load())
    user_preview = content_preview(user_text)[:100]
    return f"{instructions}\n\nFirst user message:\n{user_preview}".strip()


def _anthropic_client(**kwargs):
    import anthropic

    return anthropic.Anthropic(**kwargs)


def _openai_client(**kwargs):
    from openai import OpenAI

    return OpenAI(**kwargs)


def fallback_title(user_text: str) -> str:
    text = content_preview(user_text)
    text = re.sub(r"@\w+", " ", text)
    text = re.sub(r"[`*_~#>\[\](){}]", " ", text)
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9.+#/-]*", text)
    picked = [
        word.strip("-_/")
        for word in words
        if len(word.strip("-_/")) >= 2
        and word.casefold() not in _STOPWORDS
        and not _PATH_TOKEN_RE.search(word.strip("-_/"))
        and "/" not in word
        and "\\" not in word
    ]
    picked = picked[:6] or [
        word for word in words[:6]
        if not _PATH_TOKEN_RE.search(word) and "/" not in word and "\\" not in word
    ]
    if not picked:
        return "Untitled"
    return clean_title(" ".join(_title_word(word) for word in picked))


def _title_model_for(model: str, cfg) -> str | None:
    if cfg.api not in _TITLE_MODELS:
        return None
    if cfg.provider_id in _BUILTIN_TITLE_PROVIDERS:
        return _TITLE_MODELS.get(cfg.api)
    return model


def _is_usable_title(title: str) -> bool:
    if not title or title.casefold() == "untitled":
        return False
    return not _looks_like_bad_title(title)


def _looks_like_bad_title(title: str) -> bool:
    lowered = title.casefold()
    return any(pattern in lowered for pattern in _BAD_TITLE_PATTERNS)


def _title_word(word: str) -> str:
    if any(ch.isdigit() for ch in word):
        return word.upper()
    if any(ch.isupper() for ch in word[1:]):
        return word
    return word[:1].upper() + word[1:].lower()


class TitleThread(QThread):
    done  = pyqtSignal(str, str)   # conv_id, title
    error = pyqtSignal(str)

    def __init__(self, conv_id: str, model: str, user_text: str):
        super().__init__()
        self.conv_id        = conv_id
        self.model          = model
        self.user_text      = user_text

    def run(self):
        try:
            title = generate_title(self.model, self.user_text)
        except Exception:
            title = fallback_title(self.user_text)
        self.done.emit(self.conv_id, title)
