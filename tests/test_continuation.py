import json

from services.continuation import (
    CONTINUATION_VERSION,
    continuation_prompt,
    parse_continuation_ledger,
    render_continuation_ledger,
    validate_continuation_ledger,
)


def _ledger():
    return {
        "version": CONTINUATION_VERSION,
        "task": "Implement runtime extensions",
        "done_when": "Tests pass",
        "forbid": [],
        "established": [{"claim": "Hooks can return directives"}],
        "learned": [],
        "open": [],
        "next": ["Run tests"],
    }


def test_validate_continuation_ledger_accepts_shape():
    result = validate_continuation_ledger(_ledger())
    assert result.ok
    assert result.ledger["task"] == "Implement runtime extensions"


def test_parse_continuation_ledger_extracts_json_from_text():
    text = f"Here is the artifact:\n{json.dumps(_ledger())}\nDone."
    result = parse_continuation_ledger(text)
    assert result.ok


def test_validate_continuation_ledger_reports_errors():
    result = validate_continuation_ledger({"version": "wrong"})
    assert not result.ok
    assert "missing field: task" in result.errors


def test_parse_continuation_ledger_rejects_non_json():
    result = parse_continuation_ledger("no object here")
    assert not result.ok
    assert result.errors == ["response did not contain a JSON object"]


def test_parse_continuation_ledger_rejects_json_list():
    result = parse_continuation_ledger("[]")
    assert not result.ok
    assert result.errors == ["ledger must be a JSON object"]


def test_validate_continuation_ledger_type_errors():
    ledger = _ledger()
    ledger["task"] = []
    ledger["next"] = "not a list"
    result = validate_continuation_ledger(ledger)
    assert not result.ok
    assert "task must be a string" in result.errors
    assert "next must be a list" in result.errors


def test_render_continuation_ledger():
    rendered = render_continuation_ledger(_ledger())
    assert "Task: Implement runtime extensions" in rendered
    assert "Established:" in rendered


def test_render_continuation_ledger_rejects_invalid():
    try:
        render_continuation_ledger({"version": "wrong"})
    except ValueError as exc:
        assert "missing field: task" in str(exc)
    else:
        raise AssertionError("expected invalid ledger")


def test_continuation_prompt_mentions_schema():
    prompt = continuation_prompt("USER: hi")
    assert CONTINUATION_VERSION in prompt
    assert "USER: hi" in prompt
