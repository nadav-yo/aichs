from types import SimpleNamespace

from ui.widgets.chat_panel import ChatPanel, _history_ends_with_assistant_text


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
