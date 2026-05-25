from unittest.mock import patch

from services.auto_title import TitleThread
from services.compaction import CompactionThread


def test_title_thread_emits_done(qapp):
    thread = TitleThread("id1", "claude-sonnet-4-6", "user", "assistant")
    results = []

    def on_done(conv_id, title):
        results.append((conv_id, title))

    thread.done.connect(on_done)
    with patch("services.auto_title.generate_title", return_value="Short title"):
        thread.run()
    assert results == [("id1", "Short title")]


def test_title_thread_emits_error(qapp):
    thread = TitleThread("id2", "claude-sonnet-4-6", "u", "a")
    errors = []
    thread.error.connect(errors.append)
    with patch("services.auto_title.generate_title", side_effect=RuntimeError("api down")):
        thread.run()
    assert errors == ["api down"]


def test_compaction_thread_emits_done(qapp):
    history = [{"role": "user", "content": "hi"}]
    thread = CompactionThread("claude-sonnet-4-6", history)
    done = []
    thread.done.connect(done.append)
    compacted = history + [{"role": "assistant", "content": "summary"}]
    with patch("services.compaction.compact", return_value=compacted):
        thread.run()
    assert done[0] == compacted


def test_compaction_thread_emits_error(qapp):
    thread = CompactionThread("claude-sonnet-4-6", [])
    errors = []
    thread.error.connect(errors.append)
    with patch("services.compaction.compact", side_effect=ValueError("fail")):
        thread.run()
    assert errors == ["fail"]
