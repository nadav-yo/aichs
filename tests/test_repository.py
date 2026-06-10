import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from storage.repository import (
    ConversationStore,
    _available_path,
    _load_json,
    list_workspaces,
    _parse_datetime,
    register_workspace,
    workspace_conversations_dir,
    workspace_id,
    _message_text,
    _summary,
)


def _sample_conv(conv_id: str = "20260101_120000", **overrides) -> dict:
    data = {
        "id": conv_id,
        "title": "First chat",
        "title_auto": True,
        "created_at": "2026-01-01T12:00:00",
        "updated_at": "2026-01-02T10:00:00",
        "model": "claude-test",
        "pinned": False,
        "messages": [{"role": "user", "content": "Hello world"}],
    }
    data.update(overrides)
    return data


class TestConversationStore:
    def test_list_all_skips_invalid_json(self, store, conv_dir):
        (conv_dir / "bad.json").write_text("{not json", encoding="utf-8")
        store.save("good", _sample_conv("good"))
        assert len(store.list_all()) == 1

    def test_save_load_roundtrip(self, store, conv_dir):
        data = _sample_conv()
        path = store.save(data["id"], data)
        assert path == conv_dir / f"{data['id']}.json"
        loaded = store.load(str(path))
        assert loaded["title"] == "First chat"
        assert len(loaded["messages"]) == 1

    def test_list_all_pins_first(self, store):
        store.save("older", _sample_conv("older", pinned=False, updated_at="2026-01-01"))
        store.save("pinned", _sample_conv("pinned", pinned=True, updated_at="2025-12-01"))
        listed = store.list_all()
        assert [s["id"] for _, s in listed] == ["pinned", "older"]

    def test_delete(self, store):
        path = store.save("x", _sample_conv("x"))
        store.delete(str(path))
        assert store.list_all() == []
        trashed = store.list_trash()
        assert len(trashed) == 1
        assert trashed[0][1]["id"] == "x"
        assert trashed[0][1]["deleted_at"]

    def test_delete_missing_path_is_noop(self, store, tmp_path):
        store.delete(str(tmp_path / "missing.json"))
        assert store.list_trash() == []

    def test_restore_deleted_conversation(self, store):
        path = store.save("x", _sample_conv("x"))
        store.delete(str(path))
        trash_path = store.list_trash()[0][0]

        restored = store.restore(str(trash_path))

        assert restored.exists()
        assert store.load(str(restored))["title"] == "First chat"
        assert "deleted_at" not in store.load(str(restored))
        assert store.list_trash() == []
        assert [summary["id"] for _, summary in store.list_all()] == ["x"]

    def test_prune_trash_removes_expired_conversations(self, store):
        path = store.save("old", _sample_conv("old"))
        store.delete(str(path))
        trash_path = store.list_trash()[0][0]
        data = json.loads(trash_path.read_text(encoding="utf-8"))
        data["deleted_at"] = (datetime.now() - timedelta(days=15)).isoformat()
        trash_path.write_text(json.dumps(data), encoding="utf-8")

        assert store.prune_trash(retention_days=14) == 1
        assert store.list_trash() == []

    def test_rename(self, store):
        path = store.save("x", _sample_conv("x"))
        store.rename(str(path), "  Renamed  ")
        loaded = store.load(str(path))
        assert loaded["title"] == "Renamed"
        assert loaded["title_auto"] is False

    def test_rename_empty_title_becomes_untitled(self, store):
        path = store.save("x", _sample_conv("x"))
        store.rename(str(path), "   ")
        assert store.load(str(path))["title"] == "Untitled"

    def test_save_removes_duplicate_file_for_same_id(self, store, conv_dir):
        legacy = conv_dir / "legacy.json"
        legacy.write_text(
            json.dumps(_sample_conv("same-id", title="Ghost")),
            encoding="utf-8",
        )
        store.save("same-id", _sample_conv("same-id", title="Canonical"))
        assert not legacy.exists()
        assert (conv_dir / "same-id.json").exists()
        assert store.load(str(conv_dir / "same-id.json"))["title"] == "Canonical"

    def test_prune_leaked_test_conversation_fixture(self, conv_dir):
        ghost = conv_dir / "c1.json"
        ghost.write_text(
            json.dumps(
                {
                    "id": "c1",
                    "title": "First",
                    "messages": [],
                    "updated_at": "2026-01-01T12:00:00",
                }
            ),
            encoding="utf-8",
        )
        ConversationStore()
        assert not ghost.exists()

    def test_list_all_dedupes_by_conversation_id(self, store, conv_dir):
        (conv_dir / "ghost.json").write_text(
            json.dumps(_sample_conv("dup", title="Ghost", updated_at="2026-01-01")),
            encoding="utf-8",
        )
        store.save("dup", _sample_conv("dup", title="Live", updated_at="2026-02-01"))
        listed = store.list_all()
        assert len(listed) == 1
        assert listed[0][1]["title"] == "Live"

    def test_toggle_pin(self, store):
        path = store.save("x", _sample_conv("x", pinned=False))
        assert store.toggle_pin(str(path)) is True
        assert store.load(str(path))["pinned"] is True
        assert store.toggle_pin(str(path)) is False

    def test_make_title_truncates(self):
        long = "x" * 60
        title = ConversationStore.make_title(long)
        assert title.endswith("…")
        assert len(title) == 51

    def test_matches_search_title_and_body(self, store):
        path = store.save("x", _sample_conv("x", messages=[
            {"role": "user", "content": "find the needle"},
            {"role": "user", "content": "hidden runtime", "synthetic": "extension"},
        ]))
        _, summary = store.list_all()[0]
        assert store.matches_search(path, summary, "needle")
        assert store.matches_search(path, summary, "First")
        assert not store.matches_search(path, summary, "runtime")
        assert not store.matches_search(path, summary, "missing")

    def test_matches_search_returns_false_when_body_cannot_load(self, store, tmp_path):
        missing = tmp_path / "missing.json"
        assert not store.matches_search(missing, {"title": "No match"}, "needle")

    def test_workspace_store_uses_workspace_id_directory(self, tmp_path):
        workspace = tmp_path / "repo"
        workspace.mkdir()
        store = ConversationStore(str(workspace))

        path = store.save("x", _sample_conv("x"))

        assert path == workspace_conversations_dir(workspace) / "x.json"
        assert path.exists()
        assert path.parts[-3:] == (workspace_id(workspace), "conversations", "x.json")
        data = store.load(str(path))
        assert data["workspace_id"] == workspace_id(workspace)
        assert Path(data["cwd"]) == workspace.resolve()

    def test_workspace_store_writes_registry(self, tmp_path, isolate_aichs_home):
        from storage import repository

        workspace = tmp_path / "repo"
        workspace.mkdir()
        store = ConversationStore(str(workspace))
        store.save("x", _sample_conv("x"))

        data = json.loads(repository.WORKSPACES_PATH.read_text(encoding="utf-8"))
        wid = workspace_id(workspace)
        assert data["version"] == 1
        assert data["workspaces"][wid]["path"] == str(workspace.resolve())

    def test_list_workspaces_sorts_by_recent_and_marks_missing(self, tmp_path):
        older = tmp_path / "older"
        newer = tmp_path / "newer"
        missing = tmp_path / "missing"
        older.mkdir()
        newer.mkdir()

        register_workspace(older)
        register_workspace(missing)
        register_workspace(newer)

        rows = list_workspaces()

        assert [Path(row["path"]).name for row in rows] == ["newer", "missing", "older"]
        assert rows[0]["exists"] is True
        assert rows[1]["exists"] is False
        assert list_workspaces(limit=2) == rows[:2]

    def test_list_workspaces_ignores_invalid_registry_shapes(self, isolate_aichs_home):
        from storage import repository

        repository.WORKSPACES_PATH.parent.mkdir(parents=True, exist_ok=True)
        repository.WORKSPACES_PATH.write_text("[]", encoding="utf-8")
        assert list_workspaces() == []

        repository.WORKSPACES_PATH.write_text(
            json.dumps({"workspaces": {"bad": None, "empty": {"path": ""}}}),
            encoding="utf-8",
        )
        assert list_workspaces() == []

    def test_workspace_store_lists_only_its_workspace(self, tmp_path):
        one = tmp_path / "one"
        two = tmp_path / "two"
        one.mkdir()
        two.mkdir()

        ConversationStore(str(one)).save("one-chat", _sample_conv("one-chat"))
        ConversationStore(str(two)).save("two-chat", _sample_conv("two-chat"))

        listed = ConversationStore(str(one)).list_all()

        assert [summary["id"] for _, summary in listed] == ["one-chat"]

    def test_workspace_store_ignores_global_conversations(self, tmp_path, conv_dir):
        workspace = tmp_path / "repo"
        other = tmp_path / "other"
        workspace.mkdir()
        other.mkdir()
        (conv_dir / "current.json").write_text(
            json.dumps(_sample_conv("current", cwd=str(workspace.resolve()))),
            encoding="utf-8",
        )
        (conv_dir / "other.json").write_text(
            json.dumps(_sample_conv("other", cwd=str(other.resolve()))),
            encoding="utf-8",
        )
        (conv_dir / "unscoped.json").write_text(
            json.dumps(_sample_conv("unscoped")),
            encoding="utf-8",
        )

        listed = ConversationStore(str(workspace)).list_all()

        assert listed == []

    def test_load_by_id_finds_workspace_chat(self, tmp_path):
        workspace = tmp_path / "repo"
        workspace.mkdir()
        store = ConversationStore(str(workspace))
        store.save("x", _sample_conv("x", title="Workspace chat"))

        assert store.load_by_id("x")["title"] == "Workspace chat"

    def test_load_by_id_raises_for_missing_id(self, store):
        with pytest.raises(FileNotFoundError):
            store.load_by_id("missing")


class TestHelpers:
    def test_message_text_blocks(self):
        content = [
            {"type": "text", "text": "hello"},
            {"type": "image"},
            {"type": "tool_result", "content": [{"type": "text", "text": "nested"}]},
        ]
        assert "hello" in _message_text(content)
        assert "[image]" in _message_text(content)
        assert "nested" in _message_text(content)
        assert _message_text(None) == ""
        assert _message_text(123) == "123"

    def test_summary_message_count(self):
        assert _summary(_sample_conv())["message_count"] == 1
        summary = _summary({**_sample_conv(), "cwd": "/repo", "messages": [{}, {}, {}]})
        assert summary["message_count"] == 3
        assert summary["cwd"] == "/repo"

    def test_file_and_datetime_helpers(self, tmp_path):
        path = tmp_path / "chat.json"
        path.write_text("{}", encoding="utf-8")

        assert _available_path(path).name == "chat-1.json"
        assert _load_json(tmp_path / "missing.json") is None
        assert _parse_datetime("not a date") is None
