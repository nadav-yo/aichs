from types import SimpleNamespace

from ui.widgets.chat_panel import ChatPanel, _chat_ref_context, _history_ends_with_assistant_text, _message_files


class _Button:
    def __init__(self):
        self.text = ""
        self.hidden = False
        self.enabled = None

    def setText(self, text):
        self.text = text

    def setStyleSheet(self, _style):
        pass

    def setEnabled(self, enabled):
        self.enabled = enabled

    def hide(self):
        self.hidden = True


class _Composer:
    def __init__(self, text=""):
        self._text = text
        self.enabled = None
        self.focused = False
        self.cleared = False
        self.skill_cleared = False
        self.strip = SimpleNamespace(images=lambda: [])
        self.input = SimpleNamespace(
            exit_mention_mode=lambda: None,
            exit_slash_mode=lambda: None,
        )

    def text(self):
        return self._text

    def set_enabled(self, enabled):
        self.enabled = enabled

    def focus_input(self):
        self.focused = True

    def active_skill(self):
        return None

    def clear(self):
        self.cleared = True

    def clear_skill(self):
        self.skill_cleared = True


class _DraftCursor:
    def __init__(self):
        self.moved_to_end = False

    def movePosition(self, _operation):
        self.moved_to_end = True


class _DraftInput:
    def __init__(self, text=""):
        self._text = text
        self.cursor = _DraftCursor()
        self.signals_blocked = False
        self.mention_exited = False
        self.slash_exited = False

    def toPlainText(self):
        return self._text

    def setPlainText(self, text):
        self._text = text

    def blockSignals(self, blocked):
        previous = self.signals_blocked
        self.signals_blocked = blocked
        return previous

    def exit_mention_mode(self):
        self.mention_exited = True

    def exit_slash_mode(self):
        self.slash_exited = True

    def textCursor(self):
        return self.cursor

    def setTextCursor(self, cursor):
        self.cursor = cursor


class _DraftComposer:
    def __init__(self, text=""):
        self.input = _DraftInput(text)
        self.refs = []
        self.focused = False

    def remember_file_refs(self, refs):
        self.refs.extend(refs)

    def focus_input(self):
        self.focused = True


def test_compaction_mode_keeps_composer_writable(qapp):
    panel = SimpleNamespace()
    panel.composer = _Composer()
    panel.btn = _Button()
    panel.stop_btn = _Button()
    panel.jump_btn = _Button()
    panel._sync_visible_runtime_refs = lambda: None
    panel._visible_run = lambda: None
    panel._visible_compaction = lambda: object()
    panel._update_queue_ui = lambda: None
    panel._enter_compaction = lambda: ChatPanel._enter_compaction(panel)

    ChatPanel._refresh_runtime_controls(panel)

    assert panel.composer.enabled is True
    assert panel.composer.focused is True
    assert panel.btn.text == "Queue"
    assert panel.stop_btn.hidden is True


def test_draft_diagnostic_fix_inserts_prompt_and_hidden_file_ref():
    panel = SimpleNamespace()
    panel.composer = _DraftComposer("Existing thought")
    panel._file_picker = None
    panel._skill_picker = None

    ChatPanel.draft_diagnostic_fix(
        panel,
        "Please fix this diagnostic in @src/main.py:2.",
        ["src/main.py"],
    )

    assert panel.composer.input.toPlainText() == (
        "Existing thought\n\nPlease fix this diagnostic in @src/main.py:2."
    )
    assert panel.composer.refs == ["src/main.py"]
    assert panel.composer.input.signals_blocked is False
    assert panel.composer.input.mention_exited is True
    assert panel.composer.input.slash_exited is True
    assert panel.composer.input.cursor.moved_to_end is True
    assert panel.composer.focused is True


def test_send_queues_during_compaction(workspace):
    runtime = SimpleNamespace(queued=[])
    panel = SimpleNamespace()
    panel.cwd = str(workspace)
    panel.conv_id = "c1"
    panel.composer = _Composer("write this next")
    panel._skill_picker = None
    panel._file_picker = None
    panel._settings = SimpleNamespace(load=lambda: {})
    panel.model_combo = SimpleNamespace(currentText=lambda: "claude-sonnet-4-6")
    panel._visible_run = lambda: None
    panel._visible_compaction = lambda: object()
    panel._ensure_conversation = lambda _title, _model: None
    panel._runtime_for = lambda _conv_id: runtime
    panel._update_queue_ui = lambda: None
    panel._send_draft = lambda _draft: (_ for _ in ()).throw(AssertionError("should queue"))

    ChatPanel.send(panel)

    assert panel.composer.cleared is True
    assert panel.composer.skill_cleared is True
    assert len(runtime.queued) == 1
    assert runtime.queued[0]["title_text"] == "write this next"


def test_runtime_text_queue_marks_draft_synthetic(workspace):
    runtime = SimpleNamespace(queued=[])
    panel = SimpleNamespace()
    panel.conv_id = "c1"
    panel.model_combo = SimpleNamespace(currentText=lambda: "claude-sonnet-4-6")
    panel._visible_run = lambda: None
    panel._visible_compaction = lambda: object()
    panel._ensure_conversation = lambda _title, _model: None
    panel._runtime_for = lambda _conv_id: runtime
    panel._update_queue_ui = lambda: None
    panel._send_draft = lambda _draft: (_ for _ in ()).throw(AssertionError("should queue"))

    ChatPanel._send_or_queue_text(
        panel,
        "continue internally",
        prefer_queue=True,
        synthetic="extension_resume",
    )

    assert runtime.queued[0]["title_text"] == "continue internally"
    assert runtime.queued[0]["synthetic"] == "extension_resume"


def test_history_ends_with_assistant_text():
    history = [{"role": "assistant", "content": "done"}]

    assert _history_ends_with_assistant_text(history, "done")
    assert not _history_ends_with_assistant_text(history, "other")
    assert not _history_ends_with_assistant_text([{"role": "user", "content": "done"}], "done")


def test_message_files_includes_hidden_clipboard_refs(workspace):
    target = workspace / "services" / "git_diff.py"
    target.parent.mkdir()
    target.write_text("content", encoding="utf-8")

    files = _message_files(str(workspace), "coverage says services\\git_diff.py: 77%", ["services\\git_diff.py"])

    assert len(files) == 1
    assert files[0]["path"] == "services\\git_diff.py"
    assert files[0]["content"] == "content"


def test_message_files_keeps_sentence_punctuation_outside_bare_refs(workspace):
    target = workspace / "services" / "chat.py"
    target.parent.mkdir()
    target.write_text("content", encoding="utf-8")

    files = _message_files(str(workspace), "I read @services\\chat.py.")

    assert len(files) == 1
    assert files[0]["path"] == "services\\chat.py"
    assert files[0]["content"] == "content"


def test_edit_file_result_emits_completion_signal(qapp, store, workspace, monkeypatch):
    panel = ChatPanel(store, cwd=str(workspace))
    panel.conv_id = "c1"
    run = SimpleNamespace(conv_id="c1", active_terminal=None, last_edit_path="src/main.py")
    completed = []
    cards = []

    panel.file_write_completed.connect(completed.append)
    monkeypatch.setattr(panel, "_find_run", lambda _run_id: run)
    monkeypatch.setattr(panel, "_add_file_card", lambda path: cards.append(path))
    monkeypatch.setattr(panel, "_show_post_tool_thinking", lambda _run: None)

    panel._on_tool_result("run-1", "edit_file", "Edited src/main.py")

    assert completed == ["src/main.py"]
    assert cards == ["src/main.py"]
    assert run.last_edit_path == ""
    panel.close()


def test_chat_ref_context_dedupes_and_names_exact_tool():
    text = _chat_ref_context([
        {"id": "c1", "title": "  Viewport   Picking "},
        {"id": "c1", "title": "Duplicate"},
    ])

    assert "read_project_chat" in text
    assert "Viewport Picking (conversation_id: c1)" in text
    assert text.count("conversation_id: c1") == 1
