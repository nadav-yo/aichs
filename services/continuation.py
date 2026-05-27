from __future__ import annotations

import json
from dataclasses import dataclass


CONTINUATION_VERSION = "aicc-continuation/v1"
REQUIRED_FIELDS = (
    "version",
    "task",
    "done_when",
    "forbid",
    "established",
    "learned",
    "open",
    "next",
)


@dataclass(frozen=True)
class LedgerValidation:
    ok: bool
    errors: list[str]
    ledger: dict | None = None


def parse_continuation_ledger(text: str) -> LedgerValidation:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = _extract_json_object(text)
        if data is None:
            return LedgerValidation(False, ["response did not contain a JSON object"])
    return validate_continuation_ledger(data)


def validate_continuation_ledger(data) -> LedgerValidation:
    errors: list[str] = []
    if not isinstance(data, dict):
        return LedgerValidation(False, ["ledger must be a JSON object"])

    for field in REQUIRED_FIELDS:
        if field not in data:
            errors.append(f"missing field: {field}")

    if data.get("version") != CONTINUATION_VERSION:
        errors.append(f"version must be {CONTINUATION_VERSION}")

    for field in ("task", "done_when"):
        if field in data and not isinstance(data.get(field), str):
            errors.append(f"{field} must be a string")

    for field in ("forbid", "established", "learned", "open", "next"):
        if field in data and not isinstance(data.get(field), list):
            errors.append(f"{field} must be a list")

    return LedgerValidation(not errors, errors, data if not errors else None)


def render_continuation_ledger(ledger: dict) -> str:
    validation = validate_continuation_ledger(ledger)
    if not validation.ok:
        raise ValueError("; ".join(validation.errors))

    parts = [
        "[Continuation ledger]",
        f"Task: {ledger['task']}",
        f"Done when: {ledger['done_when']}",
    ]
    for field, title in (
        ("forbid", "Forbid"),
        ("established", "Established"),
        ("learned", "Learned"),
        ("open", "Open"),
        ("next", "Next"),
    ):
        items = ledger.get(field) or []
        if not items:
            continue
        parts.append(f"{title}:")
        for item in items:
            parts.append(f"- {_render_item(item)}")
    return "\n".join(parts)


def continuation_prompt(transcript: str) -> str:
    return (
        "Convert the earlier portion of this coding-agent conversation into one "
        f"strict JSON object with version {CONTINUATION_VERSION}. Include exactly "
        "these top-level fields: version, task, done_when, forbid, established, "
        "learned, open, next. Use strings for task and done_when. Use arrays for "
        "the other fields. Preserve only durable facts needed to resume safely; "
        "do not include raw transcript.\n\n---\n"
        f"{transcript}"
    )


def _extract_json_object(text: str) -> dict | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _render_item(item) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        compact = json.dumps(item, ensure_ascii=False, sort_keys=True)
        return compact
    return str(item)
