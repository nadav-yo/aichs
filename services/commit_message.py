import re

from PyQt6.QtCore import QThread, pyqtSignal

from services.git_status import run_git
from services.model_registry import get_model_config, resolve_api_key
from services.model_requests import apply_generation_params
from storage.settings import (
    COMMIT_MESSAGE_PROMPT_ADDITION_KEY as COMMIT_MESSAGE_PROMPT_ADDITION_KEY,
)

MAX_STAGED_DIFF_CHARS = 24_000
COMMIT_MESSAGE_CHUNK_CHARS = 16_000
MAX_COMPACTED_CONTEXT_CHARS = 20_000
COMMIT_MESSAGE_MAX_TOKENS = 4096
COMMIT_MESSAGE_SUMMARY_MAX_TOKENS = 512
COMMIT_MESSAGE_RETRY_MAX_TOKENS = 8192
_CHATML_CONTROL_TOKEN_RE = re.compile(
    r"<\|im_end\|>|<\|im_start\|>(?:system|user|assistant|tool)?"
)
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)

BASE_PROMPT = """\
Generate one concise Git commit message for the staged changes only.
Use the staged file list, staged stats, and staged diff below.

Output format:
Line 1: commit summary
Optional blank line followed by commit body

Rules:
- Reply with the commit message only.
- Do not wrap the answer in quotes.
- Do not use markdown fences.
- Do not include alternatives or explanations.
"""


CHUNK_SUMMARY_PROMPT = """\
Summarize this staged Git diff chunk for a later commit-message generator.
Focus on changed behavior, user-visible effects, and important files.
Do not write a commit message yet.
Reply with a compact plain-text summary only.
"""


def staged_commit_parts(repo_path: str) -> tuple[str, str, str]:
    names = run_git(["git", "diff", "--cached", "--name-status"], repo_path, timeout=10)
    stat = run_git(["git", "diff", "--cached", "--stat"], repo_path, timeout=10)
    diff = run_git(
        ["git", "diff", "--cached", "--unified=3", "--no-ext-diff"],
        repo_path,
        timeout=20,
    )
    return names, stat, diff


def staged_commit_context(repo_path: str, max_diff_chars: int = MAX_STAGED_DIFF_CHARS) -> str:
    names, stat, diff = staged_commit_parts(repo_path)
    return _staged_context_from_parts(names, stat, diff, max_diff_chars)


def _staged_context_from_parts(
    names: str,
    stat: str,
    diff: str,
    max_diff_chars: int = MAX_STAGED_DIFF_CHARS,
) -> str:
    if not names and not stat and not diff:
        return ""
    if len(diff) > max_diff_chars:
        diff = f"{diff[:max_diff_chars].rstrip()}\n\n[diff truncated]"
    return (
        "STAGED FILES:\n"
        f"{names or '(none)'}\n\n"
        "STAGED STATS:\n"
        f"{stat or '(none)'}\n\n"
        "STAGED DIFF:\n"
        f"{diff or '(no textual diff)'}"
    )


def build_commit_message_prompt(context: str, guidance: str = "") -> str:
    parts = [BASE_PROMPT.strip()]
    guidance = str(guidance or "").strip()
    if guidance:
        parts.append(f"User commit message guidance:\n{guidance}")
    parts.append(context.strip())
    return "\n\n---\n\n".join(parts)


def generate_commit_message(model: str, repo_path: str, guidance: str = "") -> str:
    model = str(model or "").strip()
    if not model:
        raise ValueError("No model selected.")
    names, stat, diff = staged_commit_parts(repo_path)
    if not names and not stat and not diff:
        raise ValueError("No staged changes to summarize.")

    cfg = get_model_config(model)
    kwargs = _client_kwargs(cfg)

    context = _generation_context(model, cfg, kwargs, names, stat, diff)
    prompt = build_commit_message_prompt(context, guidance)
    raw, resp = _call_model_text(
        model,
        cfg,
        kwargs,
        prompt,
        _final_max_tokens(cfg),
    )
    message = clean_commit_message(raw)
    if not message:
        finish_reason = _finish_reason(resp)
        if finish_reason == "length":
            retry_message = _retry_commit_message(
                model,
                cfg,
                kwargs,
                names,
                stat,
                diff,
                guidance,
            )
            if retry_message:
                return retry_message
            raise ValueError(
                "The model ran out of output tokens before returning a commit message."
            )
        raise ValueError("The model returned no visible commit message text.")
    return message


def _client_kwargs(cfg) -> dict:
    kwargs: dict = {"api_key": resolve_api_key(cfg.api_key_spec)}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return kwargs


def _final_max_tokens(cfg) -> int | None:
    if cfg.api == "anthropic":
        return COMMIT_MESSAGE_MAX_TOKENS
    return None


def _call_model_text(model: str, cfg, kwargs: dict, prompt: str, max_tokens: int | None):
    if cfg.api == "anthropic":
        client = _anthropic_client(**kwargs)
        request = {
            "model": model,
            "max_tokens": max_tokens or COMMIT_MESSAGE_MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }
        apply_generation_params(request, cfg, include_extra_body=False)
        resp = client.messages.create(**request)
        raw = resp.content[0].text
    else:
        client = _openai_client(**kwargs)
        request = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        apply_generation_params(request, cfg)
        resp = client.chat.completions.create(**request)
        raw = _openai_message_text(resp)
    return raw, resp


def _anthropic_client(**kwargs):
    import anthropic

    return anthropic.Anthropic(**kwargs)


def _openai_client(**kwargs):
    from openai import OpenAI

    return OpenAI(**kwargs)


def _openai_message_text(resp) -> str:
    try:
        message = resp.choices[0].message
    except Exception:
        return ""
    content = getattr(message, "content", "") or ""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                parts.append(str(getattr(block, "text", "") or ""))
        return "".join(parts)
    return str(content)


def _retry_commit_message(
    model: str,
    cfg,
    kwargs: dict,
    names: str,
    stat: str,
    diff: str,
    guidance: str,
) -> str:
    context = _staged_context_from_parts(names, stat, diff, max_diff_chars=4_000)
    prompt = build_commit_message_prompt(context, guidance)
    prompt = (
        "The previous attempt produced no visible commit message before its output "
        "limit. Reply with only one concise commit summary line. Do not include "
        "reasoning, analysis, alternatives, markdown, or a body.\n\n"
        f"{prompt}"
    )
    raw, _resp = _call_model_text(
        model,
        cfg,
        kwargs,
        prompt,
        COMMIT_MESSAGE_RETRY_MAX_TOKENS,
    )
    return clean_commit_message(raw)


def _generation_context(model: str, cfg, kwargs: dict, names: str, stat: str, diff: str) -> str:
    if len(diff) <= MAX_STAGED_DIFF_CHARS:
        return _staged_context_from_parts(names, stat, diff)
    compacted = _summarize_diff_iteratively(model, cfg, kwargs, names, stat, diff)
    return (
        "STAGED FILES:\n"
        f"{names or '(none)'}\n\n"
        "STAGED STATS:\n"
        f"{stat or '(none)'}\n\n"
        "COMPACTED STAGED DIFF SUMMARY:\n"
        f"{compacted or '(no textual diff summary)'}\n\n"
        "[large staged diff compacted in chunks]"
    )


def _summarize_diff_iteratively(
    model: str,
    cfg,
    kwargs: dict,
    names: str,
    stat: str,
    diff: str,
) -> str:
    summaries = _summarize_chunks(
        model,
        cfg,
        kwargs,
        _chunk_text(diff, COMMIT_MESSAGE_CHUNK_CHARS),
        lambda chunk, index, total: _build_chunk_summary_prompt(
            names,
            stat,
            chunk,
            index,
            total,
        ),
        "Chunk",
    )
    combined = "\n\n".join(summaries).strip()

    while len(combined) > MAX_COMPACTED_CONTEXT_CHARS and len(summaries) > 1:
        previous_length = len(combined)
        summaries = _summarize_chunks(
            model,
            cfg,
            kwargs,
            _chunk_text(combined, COMMIT_MESSAGE_CHUNK_CHARS),
            _build_summary_compaction_prompt,
            "Summary chunk",
        )
        combined = "\n\n".join(summaries).strip()
        if len(combined) >= previous_length:
            break

    if len(combined) > MAX_COMPACTED_CONTEXT_CHARS:
        combined = (
            f"{combined[:MAX_COMPACTED_CONTEXT_CHARS].rstrip()}\n\n"
            "[compacted summary truncated]"
        )
    return combined


def _summarize_chunks(
    model: str,
    cfg,
    kwargs: dict,
    chunks: list[str],
    prompt_builder,
    label: str,
) -> list[str]:
    summaries: list[str] = []
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        prompt = prompt_builder(chunk, index, total)
        raw, resp = _call_model_text(
            model,
            cfg,
            kwargs,
            prompt,
            COMMIT_MESSAGE_SUMMARY_MAX_TOKENS,
        )
        summary = clean_commit_message(raw)
        if not summary:
            summary = _empty_summary_note(resp)
        summaries.append(f"{label} {index}/{total}:\n{summary}")
    return summaries


def _build_chunk_summary_prompt(
    names: str,
    stat: str,
    chunk: str,
    index: int,
    total: int,
) -> str:
    return (
        f"{CHUNK_SUMMARY_PROMPT.strip()}\n\n"
        f"Chunk {index} of {total}\n\n"
        "STAGED FILES:\n"
        f"{names or '(none)'}\n\n"
        "STAGED STATS:\n"
        f"{stat or '(none)'}\n\n"
        "STAGED DIFF CHUNK:\n"
        f"{chunk}"
    )


def _build_summary_compaction_prompt(chunk: str, index: int, total: int) -> str:
    return (
        f"{CHUNK_SUMMARY_PROMPT.strip()}\n\n"
        f"Summary chunk {index} of {total}\n\n"
        "DIFF SUMMARIES TO COMPACT:\n"
        f"{chunk}"
    )


def _chunk_text(text: str, chunk_chars: int) -> list[str]:
    text = str(text or "")
    if not text:
        return [""]
    return [text[index : index + chunk_chars] for index in range(0, len(text), chunk_chars)]


def _empty_summary_note(resp) -> str:
    if _finish_reason(resp) == "length":
        return "[chunk summary ran out of output tokens]"
    return "[chunk summary returned no visible text]"


def clean_commit_message(raw: str) -> str:
    text = _CHATML_CONTROL_TOKEN_RE.sub("", str(raw or "")).strip()
    text = _THINK_BLOCK_RE.sub("", text).strip()
    text = re.sub(r"^```(?:[A-Za-z0-9_-]+)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    text = text.strip("\"'")
    text = re.sub(r"^(commit message|message|summary)\s*:\s*", "", text, flags=re.I).strip()
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def split_commit_message(message: str) -> tuple[str, str]:
    text = clean_commit_message(message)
    if not text:
        return "", ""
    lines = text.splitlines()
    summary = lines[0].strip()
    body = "\n".join(lines[1:]).strip()
    return summary, body


def _finish_reason(resp) -> str:
    try:
        return str(resp.choices[0].finish_reason or "")
    except Exception:
        pass
    try:
        return str(resp.stop_reason or "")
    except Exception:
        return ""


class CommitMessageThread(QThread):
    done = pyqtSignal(str, str)
    error = pyqtSignal(str)

    def __init__(self, model: str, repo_path: str, guidance: str = ""):
        super().__init__()
        self.model = model
        self.repo_path = repo_path
        self.guidance = guidance

    def run(self):
        try:
            message = generate_commit_message(self.model, self.repo_path, self.guidance)
            summary, body = split_commit_message(message)
            if not summary:
                raise ValueError("The model returned an empty commit message.")
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.done.emit(summary, body)
