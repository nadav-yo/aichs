import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from storage.repository import (
    ConversationStore,
    _CONVERSATION_INDEX_NAME,
    _available_path,
    _load_json,
    _write_json_atomic,
    list_workspaces,
    _parse_datetime,
    register_workspace,
    remove_workspace,
    workspace_conversations_dir,
    workspace_id,
    _message_text,
    _search_messages,
    _search_text,
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

    def test_purge_removes_trashed_conversation(self, store):
        path = store.save("x", _sample_conv("x"))
        store.delete(str(path))
        trash_path = store.list_trash()[0][0]

        assert store.purge(str(trash_path)) == "x"
        assert store.list_trash() == []
        assert store.list_all() == []

    def test_purge_ignores_non_trash_paths(self, store, conv_dir):
        path = store.save("x", _sample_conv("x"))

        assert store.purge(str(path)) == ""
        assert path.exists()
        assert store.list_all()

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

    def test_list_all_reuses_summary_index_without_loading_bodies(self, store, conv_dir, monkeypatch):
        store.save("one", _sample_conv("one", title="One", updated_at="2026-01-01"))
        store.save("two", _sample_conv("two", title="Two", updated_at="2026-01-02"))
        assert [summary["id"] for _, summary in store.list_all()] == ["two", "one"]

        original_load_json = _load_json
        body_loads = []

        def spy_load_json(path):
            if Path(path).suffix == ".json":
                body_loads.append(Path(path).name)
                raise AssertionError("conversation body should not be loaded from a warm index")
            return original_load_json(path)

        monkeypatch.setattr("storage.repository._load_json", spy_load_json)

        listed = store.list_all()

        assert [summary["title"] for _, summary in listed] == ["Two", "One"]
        assert body_loads == []

    def test_list_all_refreshes_changed_index_entry(self, store, conv_dir):
        path = store.save("one", _sample_conv("one", title="Old"))
        assert store.list_all()[0][1]["title"] == "Old"
        path.write_text(json.dumps(_sample_conv("one", title="New title")), encoding="utf-8")

        listed = store.list_all()

        assert listed[0][1]["title"] == "New title"

    def test_list_all_rebuilds_index_entry_missing_search_text(self, store, conv_dir):
        store.save(
            "one",
            _sample_conv("one", messages=[{"role": "user", "content": "indexed needle"}]),
        )
        index_path = conv_dir / _CONVERSATION_INDEX_NAME
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index["rows"]["one.json"]["summary"].pop("search_text")
        index["rows"]["one.json"]["summary"].pop("search_messages")
        index_path.write_text(json.dumps(index), encoding="utf-8")

        _, summary = store.list_all()[0]

        assert "indexed needle" in summary["search_text"]
        assert summary["search_messages"] == [{"role": "user", "text": "indexed needle"}]
        rebuilt = json.loads(index_path.read_text(encoding="utf-8"))
        assert "search_text" in rebuilt["rows"]["one.json"]["summary"]
        assert "search_messages" in rebuilt["rows"]["one.json"]["summary"]

    def test_load_by_id_uses_summary_index_to_avoid_scan(self, store, monkeypatch):
        store.save("one", _sample_conv("one", title="One"))
        store.save("two", _sample_conv("two", title="Two"))
        assert store.list_all()

        original_load_json = _load_json
        body_loads = []

        def spy_load_json(path):
            if Path(path).name != _CONVERSATION_INDEX_NAME:
                body_loads.append(Path(path).name)
                raise AssertionError("load_by_id should use the index before loading the target")
            return original_load_json(path)

        monkeypatch.setattr("storage.repository._load_json", spy_load_json)

        assert store.load_by_id("two")["title"] == "Two"
        assert body_loads == []

    def test_path_for_id_uses_summary_index_without_loading_bodies(self, store, monkeypatch):
        store.save("one", _sample_conv("one", title="One"))
        store.save("two", _sample_conv("two", title="Two"))
        assert store.list_all()

        original_load_json = _load_json
        body_loads = []

        def spy_load_json(path):
            if Path(path).name != _CONVERSATION_INDEX_NAME:
                body_loads.append(Path(path).name)
                raise AssertionError("path_for_id should use the summary index only")
            return original_load_json(path)

        monkeypatch.setattr("storage.repository._load_json", spy_load_json)

        assert store.path_for_id("two").name == "two.json"
        assert body_loads == []

    def test_invalid_summary_index_is_rebuilt(self, store, conv_dir):
        (conv_dir / _CONVERSATION_INDEX_NAME).write_text("[]", encoding="utf-8")
        (conv_dir / "one.json").write_text(json.dumps(_sample_conv("one")), encoding="utf-8")

        listed = store.list_all()

        assert [summary["id"] for _, summary in listed] == ["one"]
        index = json.loads((conv_dir / _CONVERSATION_INDEX_NAME).read_text(encoding="utf-8"))
        assert "one.json" in index["rows"]

    def test_save_recovers_from_invalid_index_rows(self, store, conv_dir):
        (conv_dir / _CONVERSATION_INDEX_NAME).write_text(
            json.dumps({"version": 1, "rows": []}),
            encoding="utf-8",
        )

        store.save("one", _sample_conv("one"))

        index = json.loads((conv_dir / _CONVERSATION_INDEX_NAME).read_text(encoding="utf-8"))
        assert "one.json" in index["rows"]

    def test_list_all_recovers_from_invalid_index_rows(self, store, conv_dir):
        (conv_dir / _CONVERSATION_INDEX_NAME).write_text(
            json.dumps({"version": 1, "rows": []}),
            encoding="utf-8",
        )
        (conv_dir / "one.json").write_text(json.dumps(_sample_conv("one")), encoding="utf-8")

        assert [summary["id"] for _, summary in store.list_all()] == ["one"]

    def test_index_records_skips_file_that_disappears_during_stat(self, store, conv_dir, monkeypatch):
        (conv_dir / "kept.json").write_text(json.dumps(_sample_conv("kept")), encoding="utf-8")
        (conv_dir / "gone.json").write_text(json.dumps(_sample_conv("gone")), encoding="utf-8")
        original_stat = Path.stat

        def flaky_stat(path, *args, **kwargs):
            if Path(path).name == "gone.json":
                raise OSError("gone")
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", flaky_stat)

        assert [summary["id"] for _, summary in store.list_all()] == ["kept"]

    def test_upsert_index_path_ignores_missing_file(self, store, conv_dir):
        store._upsert_index_path(conv_dir / "missing.json", {"id": "missing"})

        assert not (conv_dir / _CONVERSATION_INDEX_NAME).exists()

    def test_remove_index_path_missing_entry_is_noop(self, store, conv_dir):
        store.save("one", _sample_conv("one"))
        before = (conv_dir / _CONVERSATION_INDEX_NAME).read_text(encoding="utf-8")

        store._remove_index_path(conv_dir / "missing.json")

        assert (conv_dir / _CONVERSATION_INDEX_NAME).read_text(encoding="utf-8") == before

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

    def test_new_id_is_unique_for_parallel_creation(self):
        ids = {ConversationStore.new_id() for _ in range(20)}
        assert len(ids) == 20
        assert all(id_.startswith(datetime.now().strftime("%Y%m%d_")) for id_ in ids)

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

    def test_matches_search_uses_summary_without_loading_body(self, store, monkeypatch):
        path = store.save("x", _sample_conv("x", messages=[
            {"role": "user", "content": "find the indexed needle"},
        ]))
        _, summary = store.list_all()[0]

        def fail_load(_path):
            raise AssertionError("search should use indexed summary text")

        monkeypatch.setattr(store, "load", fail_load)

        assert store.matches_search(path, summary, "indexed needle")

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

    def test_workspace_registry_follows_patched_config_when_module_path_is_stale(self, tmp_path, monkeypatch):
        from storage import repository

        stale_registry = tmp_path / "stale_home" / ".aichs" / "workspaces.json"
        isolated_registry = tmp_path / "isolated_home" / ".aichs" / "workspaces.json"
        monkeypatch.setattr(repository, "_IMPORTED_WORKSPACES_PATH", stale_registry)
        monkeypatch.setattr(repository, "WORKSPACES_PATH", stale_registry)
        monkeypatch.setattr("config.WORKSPACES_PATH", isolated_registry)
        workspace = tmp_path / "repo"
        workspace.mkdir()

        register_workspace(workspace)

        assert isolated_registry.exists()
        assert not stale_registry.exists()

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

    def test_remove_workspace_deletes_recent_entry_by_path(self, tmp_path):
        older = tmp_path / "older"
        newer = tmp_path / "newer"
        older.mkdir()
        newer.mkdir()
        register_workspace(older)
        register_workspace(newer)

        assert remove_workspace(older) is True

        rows = list_workspaces()
        assert [Path(row["path"]).name for row in rows] == ["newer"]
        assert remove_workspace(older) is False

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

    def test_search_text_includes_visible_messages_only(self):
        data = _sample_conv(messages=[
            {"role": "user", "content": "visible body"},
            {"role": "assistant", "content": "hidden metadata", "synthetic": "extension"},
            {"role": "tool", "content": "tool chatter"},
        ])

        search_text = _search_text(data)

        assert "visible body" in search_text
        assert "hidden metadata" not in search_text
        assert "tool chatter" not in search_text

    def test_search_messages_include_role_and_visible_text_only(self):
        data = _sample_conv(messages=[
            {"role": "user", "content": "visible body"},
            {"role": "assistant", "content": [{"type": "text", "text": "answer body"}]},
            {"role": "tool", "content": "tool chatter"},
        ])

        assert _search_messages(data) == [
            {"role": "user", "text": "visible body"},
            {"role": "assistant", "text": "answer body"},
        ]

    def test_summary_message_count(self):
        assert _summary(_sample_conv())["message_count"] == 1
        summary = _summary({**_sample_conv(), "cwd": "/repo", "messages": [{}, {}, {}]})
        assert summary["message_count"] == 3
        assert summary["cwd"] == "/repo"
        assert summary["search_text"] == ""
        assert summary["search_messages"] == []

    def test_file_and_datetime_helpers(self, tmp_path):
        path = tmp_path / "chat.json"
        path.write_text("{}", encoding="utf-8")

        assert _available_path(path).name == "chat-1.json"
        assert _load_json(tmp_path / "missing.json") is None
        assert _parse_datetime("not a date") is None

    def test_write_json_atomic_removes_temp_file_on_replace_error(self, tmp_path, monkeypatch):
        target = tmp_path / "data.json"

        def fail_replace(_src, _dst):
            raise OSError("replace failed")

        monkeypatch.setattr("storage.repository.os.replace", fail_replace)

        with pytest.raises(OSError):
            _write_json_atomic(target, {"ok": True})

        assert not target.exists()
        assert list(tmp_path.glob("*.tmp")) == []
