import json

import pytest

from services.project_memory import (
    ProjectMemoryStore,
    parse_save_memory_args,
    read_project_memory,
    save_project_memory,
)
from storage.settings import SettingsStore


def test_project_memory_defaults_to_local_workspace_file(workspace):
    result = save_project_memory(
        str(workspace),
        topic="Compaction",
        text="Decision memory is core project memory.",
        source="user",
    )

    path = workspace / ".aichs" / "memory" / "project-memory.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert result == "Saved decision memory under 'compaction'."
    assert data["version"] == 1
    assert data["records"][0]["topic"] == "compaction"
    assert data["records"][0]["text"] == "Decision memory is core project memory."
    assert "global-memory" not in str(path)


def test_project_memory_reads_matches(workspace):
    save_project_memory(str(workspace), topic="auth", text="Use JWT for API authentication.")
    save_project_memory(str(workspace), topic="compaction", text="Keep compaction separate from memory.")

    out = read_project_memory(str(workspace), "auth jwt")

    assert "Project memory matches" in out
    assert "auth: Use JWT" in out
    assert "compaction" not in out


def test_project_memory_global_scope_uses_user_home(workspace):
    SettingsStore().save({"project_memory": {"scope": "global"}})

    save_project_memory(str(workspace), topic="style", text="Prefer concise answers.")

    local = workspace / ".aichs" / "memory" / "project-memory.json"
    global_path = ProjectMemoryStore(str(workspace)).path
    assert not local.exists()
    assert global_path.name == "global-memory.json"
    assert "Prefer concise answers" in global_path.read_text(encoding="utf-8")


def test_project_memory_disabled_scope(workspace):
    SettingsStore().save({"project_memory_scope": "disabled"})

    assert read_project_memory(str(workspace)) == "Project memory is disabled."
    try:
        save_project_memory(str(workspace), topic="x", text="y")
    except ValueError as exc:
        assert str(exc) == "project memory is disabled"
    else:
        raise AssertionError("save should fail when project memory is disabled")


def test_parse_save_memory_args_accepts_topic_colon_and_space():
    assert parse_save_memory_args("compaction: Keep memory core.") == ("compaction", "Keep memory core.")
    assert parse_save_memory_args("compaction Keep memory core.") == ("compaction", "Keep memory core.")
    assert parse_save_memory_args("") is None
    assert parse_save_memory_args("topic") is None



def test_project_memory_rejects_invalid_inputs(workspace):
    store = ProjectMemoryStore(str(workspace))
    cases = [
        ({"topic": "!!!", "text": "valid"}, "topic is required"),
        ({"topic": "topic", "text": "   "}, "memory text is required"),
        ({"topic": "a" * 81, "text": "valid"}, "topic must be 80 characters or fewer"),
        ({"topic": "topic", "text": "valid", "kind": "k" * 33}, "kind must be 32 characters or fewer"),
        ({"topic": "topic", "text": "x" * 1001}, "memory text must be 1000 characters or fewer"),
    ]

    for inputs, message in cases:
        with pytest.raises(ValueError, match=message):
            store.add(**inputs)


def test_project_memory_disabled_store_lists_empty(workspace):
    SettingsStore().save({"project_memory_scope": "disabled"})

    assert ProjectMemoryStore(str(workspace)).list() == []


def test_project_memory_handles_malformed_storage(workspace):
    store = ProjectMemoryStore(str(workspace))
    path = store.path
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text("{not json", encoding="utf-8")
    assert read_project_memory(str(workspace)) == "(no project memory saved)"

    path.write_text("[]", encoding="utf-8")
    assert store.list() == []

    path.write_text(json.dumps({"records": "not a list"}), encoding="utf-8")
    assert store.list() == []


def test_project_memory_filters_archived_and_invalid_records(workspace):
    store = ProjectMemoryStore(str(workspace))
    path = store.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "version": 1,
            "records": [
                "not a record",
                {"id": "missing_text", "topic": "bad", "text": ""},
                {"id": "archived", "topic": "old", "text": "Use old plan.", "archived": True},
                {"id": "active", "topic": "current", "text": "Use current plan."},
            ],
        }),
        encoding="utf-8",
    )

    active = store.list()
    all_records = store.list(include_archived=True)

    assert [record.id for record in active] == ["active"]
    assert [record.id for record in all_records] == ["archived", "active"]
    assert all_records[1].updated_at == all_records[1].created_at


def test_project_memory_read_limit_and_empty_messages(workspace):
    assert read_project_memory(str(workspace)) == "(no project memory saved)"
    assert read_project_memory(str(workspace), "missing") == "(no project memory matches 'missing')"

    save_project_memory(str(workspace), topic="alpha", text="First memory.")
    save_project_memory(str(workspace), topic="beta", text="Second memory.")

    limited = read_project_memory(str(workspace), limit=0)
    invalid_limit = read_project_memory(str(workspace), limit="bad")

    assert limited.count("- [decision]") == 1
    assert invalid_limit.count("- [decision]") == 2


def test_project_memory_search_handles_symbol_queries(workspace):
    save_project_memory(str(workspace), topic="symbols", text="Keep the !!! marker.")

    assert "symbols: Keep the !!! marker." in read_project_memory(str(workspace), "!!!")
