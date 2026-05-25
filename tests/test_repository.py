import json

import pytest

from storage.repository import ConversationStore, _message_text, _summary


@pytest.fixture
def conv_dir(monkeypatch, tmp_path):
    path = tmp_path / "conversations"
    path.mkdir()
    monkeypatch.setattr("storage.repository.CONV_DIR", path)
    return path


@pytest.fixture
def store(conv_dir):
    return ConversationStore()


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
        ]))
        _, summary = store.list_all()[0]
        assert store.matches_search(path, summary, "needle")
        assert store.matches_search(path, summary, "First")
        assert not store.matches_search(path, summary, "missing")


class TestHelpers:
    def test_message_text_blocks(self):
        content = [
            {"type": "text", "text": "hello"},
            {"type": "image"},
        ]
        assert "hello" in _message_text(content)
        assert "[image]" in _message_text(content)

    def test_summary_message_count(self):
        assert _summary(_sample_conv())["message_count"] == 1
        assert _summary({**_sample_conv(), "messages": [{}, {}, {}]})["message_count"] == 3
