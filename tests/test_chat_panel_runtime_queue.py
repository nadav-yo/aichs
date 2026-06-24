from types import SimpleNamespace
import time

from storage.settings import COMPACT_RESUME_PROMPT_KEY, SettingsStore
from services.slash_commands import SlashCommand
from ui.widgets.chat_panel import (
    ChatPanel,
    _ToolNoticeWidget,
    _chat_ref_context,
    _history_ends_with_assistant_text,
    _message_files,
    _tool_output_display_text,
    _tool_debug_text,
    _tool_notice_html,
)


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

    def show(self):
        self.hidden = False


class _Composer:
    def __init__(self, text=""):
        self._text = text
        self.enabled = None
        self.focused = False
        self.cleared = False
        self.skill_cleared = False
        self.skill = None
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
        return self.skill

    def set_skill(self, skill):
        self.skill = skill

    def clear(self):
        self.cleared = True

    def clear_skill(self):
        self.skill_cleared = True
        self.skill = None


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


def test_streaming_mode_keeps_composer_as_queue(qapp):
    panel = SimpleNamespace()
    panel.composer = _Composer()
    panel.btn = _Button()
    panel.stop_btn = _Button()
    panel.jump_btn = _Button()
    panel._sync_visible_runtime_refs = lambda: None
    panel._visible_run = lambda: object()
    panel._visible_compaction = lambda: None
    panel._update_queue_ui = lambda: None
    panel._enter_streaming = lambda: ChatPanel._enter_streaming(panel)

    ChatPanel._refresh_runtime_controls(panel)

    assert panel.composer.enabled is True
    assert panel.btn.text == "Queue"
    assert panel.stop_btn.hidden is False
    assert panel.stop_btn.enabled is True


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
    panel._add_notice = lambda _message: None
    panel._queue_visible_draft = lambda draft: ChatPanel._queue_visible_draft(panel, draft)
    panel._send_draft = lambda _draft: (_ for _ in ()).throw(AssertionError("should queue"))

    ChatPanel.send(panel)

    assert panel.composer.cleared is True
    assert panel.composer.skill_cleared is True
    assert len(runtime.queued) == 1
    assert runtime.queued[0]["title_text"] == "write this next"


def test_send_queues_during_active_run(workspace):
    runtime = SimpleNamespace(queued=[])
    panel = SimpleNamespace()
    panel.cwd = str(workspace)
    panel.conv_id = "c1"
    panel.composer = _Composer("use a smaller patch")
    panel._skill_picker = None
    panel._file_picker = None
    panel._settings = SimpleNamespace(load=lambda: {})
    panel.model_combo = SimpleNamespace(currentText=lambda: "claude-sonnet-4-6")
    panel._visible_run = lambda: object()
    panel._visible_compaction = lambda: None
    panel._ensure_conversation = lambda _title, _model: None
    panel._runtime_for = lambda _conv_id: runtime
    panel._update_queue_ui = lambda: None
    panel._add_notice = lambda _message: None
    panel._queue_visible_draft = lambda draft: ChatPanel._queue_visible_draft(panel, draft)
    panel._send_draft = lambda _draft: (_ for _ in ()).throw(AssertionError("should queue"))

    ChatPanel.send(panel)

    assert panel.composer.cleared is True
    assert len(runtime.queued) == 1
    assert runtime.queued[0]["title_text"] == "use a smaller patch"


def test_steer_queued_promotes_message_and_cancels_active_run(workspace):
    cancelled = []
    notices = []
    runtime = SimpleNamespace(
        queued=[
            {"title_text": "already queued"},
            {"title_text": "use a smaller patch"},
        ],
        run=SimpleNamespace(thread=SimpleNamespace(cancel=lambda: cancelled.append(True))),
    )
    panel = SimpleNamespace()
    panel.conv_id = "c1"
    panel.stop_btn = _Button()
    panel._visible_run = lambda: runtime.run
    panel._visible_compaction = lambda: None
    panel._runtime_for = lambda _conv_id: runtime
    panel._visible_queue = lambda: runtime.queued
    panel._update_queue_ui = lambda: None
    panel._add_notice = notices.append
    panel._start_next_queued = lambda: (_ for _ in ()).throw(AssertionError("should cancel active run"))

    ChatPanel._steer_queued(panel, 1)

    assert [draft["title_text"] for draft in runtime.queued] == [
        "use a smaller patch",
        "already queued",
    ]
    assert cancelled == [True]
    assert panel.stop_btn.enabled is False
    assert notices == ["Steering current response with queued message: use a smaller patch"]


def test_send_runs_loaded_extension_command_without_sync_file_refs(workspace, monkeypatch):
    command = SlashCommand(
        "demo_cmd",
        "Demo command",
        source="extension:demo",
        executable=True,
    )
    ran = []
    panel = SimpleNamespace()
    panel.cwd = str(workspace)
    panel.composer = _Composer("/demo_cmd run this")
    panel._slash_commands = [command]
    panel._slash_commands_cwd = str(workspace)
    panel._skill_picker = None
    panel._file_picker = None
    panel._visible_run = lambda: None
    panel._visible_compaction = lambda: None
    panel._run_extension_command = lambda name, args: ran.append((name, args))
    monkeypatch.setattr(
        "ui.widgets.chat_panel._message_files",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not read message files")),
    )

    ChatPanel.send(panel)

    assert ran == [("demo_cmd", "run this")]
    assert panel.composer.cleared is True


def test_send_waits_for_slash_command_snapshot_before_unknown_slash(workspace, monkeypatch):
    notices = []
    loads = []
    panel = SimpleNamespace()
    panel.cwd = str(workspace)
    panel.composer = _Composer("/demo_cmd run this")
    panel._slash_commands = []
    panel._slash_commands_cwd = ""
    panel._skill_picker_loading = False
    panel._skill_picker = None
    panel._file_picker = None
    panel._visible_run = lambda: None
    panel._visible_compaction = lambda: None
    panel._start_skill_picker_load = lambda: loads.append("load")
    panel._add_notice = notices.append
    monkeypatch.setattr(
        "ui.widgets.chat_panel._message_files",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should not read message files")),
    )

    ChatPanel.send(panel)

    assert loads == ["load"]
    assert notices == ["Slash commands are still loading. Try again in a moment."]
    assert panel.composer.cleared is False


def test_send_uses_loaded_builtin_prompt_command_without_sync_settings(workspace, monkeypatch):
    from services.slash_commands import SlashCommand

    drafts = []
    command = SlashCommand(
        "archivist",
        "Memory",
        prompt="Use project memory.",
        tools=["search_project_chats", "read_project_chat"],
    )
    panel = SimpleNamespace()
    panel.cwd = str(workspace)
    panel.composer = _Composer("/archivist find the plan")
    panel._slash_commands = [command]
    panel._slash_commands_cwd = str(workspace)
    panel._skill_picker = None
    panel._file_picker = None
    panel._settings = SimpleNamespace(load=lambda: {})
    panel._visible_run = lambda: None
    panel._visible_compaction = lambda: None
    panel._skill_from_command = lambda cmd: ChatPanel._skill_from_command(panel, cmd)
    panel._send_draft = drafts.append
    monkeypatch.setattr(
        "ui.widgets.chat_panel.SettingsStore.load",
        lambda *_args: (_ for _ in ()).throw(AssertionError("settings should not load")),
    )

    ChatPanel.send(panel)

    assert drafts[0]["title_text"] == "find the plan"
    assert drafts[0]["skill"].name == "archivist"
    assert drafts[0]["skill"].prompt == "Use project memory."
    assert drafts[0]["skill"].tools == ["search_project_chats", "read_project_chat"]


def test_send_uses_bare_mcp_command_as_tool_request(workspace):
    drafts = []
    command = SlashCommand(
        "mcp__unreal_mcp__describe_toolset",
        "Describe a toolset",
        prompt="Use the selected MCP tool.",
        tools=["mcp__unreal_mcp__describe_toolset"],
        source="mcp",
    )
    panel = SimpleNamespace()
    panel.cwd = str(workspace)
    panel.composer = _Composer("/mcp__unreal_mcp__describe_toolset")
    panel._slash_commands = [command]
    panel._slash_commands_cwd = str(workspace)
    panel._skill_picker = None
    panel._file_picker = None
    panel._settings = SimpleNamespace(load=lambda: {})
    panel._visible_run = lambda: None
    panel._visible_compaction = lambda: None
    panel._skill_from_command = lambda cmd: ChatPanel._skill_from_command(panel, cmd)
    panel._skill_from_extension_command = lambda cmd: ChatPanel._skill_from_extension_command(panel, cmd)
    panel._send_draft = drafts.append

    ChatPanel.send(panel)

    assert drafts[0]["title_text"] == "Use MCP capability mcp__unreal_mcp__describe_toolset."
    assert drafts[0]["skill"].name == "mcp__unreal_mcp__describe_toolset"
    assert drafts[0]["skill"].tools == ["mcp__unreal_mcp__describe_toolset"]
    assert panel.composer.cleared is True
    assert panel.composer.skill_cleared is True


def test_send_carries_file_refs_without_reading_files_on_ui_thread(workspace, monkeypatch):
    drafts = []
    panel = SimpleNamespace()
    panel.cwd = str(workspace)
    panel.composer = _Composer("please read @services\\chat.py.")
    panel._slash_commands = []
    panel._slash_commands_cwd = str(workspace)
    panel._skill_picker = None
    panel._file_picker = None
    panel._settings = SimpleNamespace(load=lambda: {})
    panel._visible_run = lambda: None
    panel._visible_compaction = lambda: None
    panel._send_draft = drafts.append
    monkeypatch.setattr(
        "ui.widgets.chat_panel.files_for_refs",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should resolve in ChatThread")),
    )

    ChatPanel.send(panel)

    assert drafts[0]["content"] == "please read @services\\chat.py."
    assert drafts[0]["file_refs"] == ["services\\chat.py"]


def test_send_draft_passes_deferred_file_refs_to_assistant_start(qapp):
    starts = []
    bubbles = []
    panel = SimpleNamespace()
    panel.history = []
    panel.conv_data = {"id": "c1"}
    panel._bubbles = {}
    panel._settings = SimpleNamespace(load=lambda: {})
    panel._model_for_crew = lambda _crew: "claude-sonnet-4-6"
    panel._ensure_conversation = lambda _title, _model: None
    panel._enter_streaming = lambda: None
    panel._ensure_tail_rendered_for_append = lambda _idx: False
    panel._add_bubble = lambda *args, **kwargs: bubbles.append((args, kwargs))
    panel._save = lambda touch_updated=True: None
    panel._maybe_auto_title = lambda: None
    panel._start_assistant = lambda **kwargs: starts.append(kwargs)
    panel._pin_to_bottom = lambda: None

    ChatPanel._send_draft(panel, {
        "content": "read @src/main.py",
        "title_text": "read @src/main.py",
        "skill": None,
        "crew": None,
        "file_refs": ["src/main.py"],
    })

    assert starts == [{
        "skill": None,
        "crew": None,
        "deferred_file_refs": ["src/main.py"],
        "deferred_file_target": 0,
    }]
    assert panel.history[0]["content"] == "read @src/main.py"
    assert bubbles


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


def test_compact_and_resume_command_uses_configured_default_prompt():
    SettingsStore().save({
        COMPACT_RESUME_PROMPT_KEY: "Resume from the short context.",
    })
    panel = SimpleNamespace()
    panel.history = [{"role": "user", "content": "old"}]
    panel._settings = SettingsStore()
    panel.sent = []
    panel.notices = []
    panel.compacted = []
    panel._send_or_queue_runtime_text = lambda text, synthetic: panel.sent.append((text, synthetic))
    panel._visible_run = lambda: None
    panel._visible_compaction = lambda: None
    panel._add_notice = panel.notices.append
    panel.compact_conversation = lambda force=False: panel.compacted.append(force)
    panel._start_next_queued = lambda: None

    ChatPanel._compact_and_resume_from_command(panel, "", True)

    assert panel.sent == [("Resume from the short context.", "extension_resume")]
    assert panel.compacted == [True]
    assert panel.notices == []


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


def test_edit_file_result_emits_completion_signal():
    completed = []
    cards = []
    run = SimpleNamespace(conv_id="c1", active_terminal=None, last_edit_path="src/main.py")
    panel = SimpleNamespace(
        conv_id="c1",
        file_write_completed=SimpleNamespace(emit=completed.append),
        _find_run=lambda _run_id: run,
        _add_file_card=lambda path: cards.append(path),
        _show_post_tool_thinking=lambda _run: None,
        _active_terminal=None,
    )

    ChatPanel._on_tool_result(panel, "run-1", "edit_file", "Edited src/main.py")

    assert completed == ["src/main.py"]
    assert cards == ["src/main.py"]
    assert run.last_edit_path == ""


def test_tool_error_updates_existing_notice_without_second_error_row():
    added = []
    updates = []
    run = SimpleNamespace(
        conv_id="c1",
        active_terminal=None,
        last_edit_path="",
        last_tool_name="mcp__docs__lookup",
        last_tool_inputs={},
        last_tool_notice=object(),
    )
    panel = SimpleNamespace(
        conv_id="c1",
        cwd="C:\\repo",
        _find_run=lambda _run_id: run,
        _add_tool_notice=lambda *args, **kwargs: added.append((args, kwargs)),
        _update_tool_notice_debug=lambda wrapper, text: updates.append((wrapper, text)),
        _show_post_tool_thinking=lambda _run: None,
        _active_terminal=None,
    )

    ChatPanel._on_tool_result(panel, "run-1", "mcp__docs__lookup", "[tool error] HTTP 400")

    assert added == []
    assert updates
    assert updates[0][0] is run.last_tool_notice
    assert "HTTP 400" in updates[0][1]


def test_mcp_notice_uses_mcp_tool_label():
    html = _tool_notice_html("Using tool 'mcp__docs__lookup'")

    assert "MCP tool" in html
    assert "mcp__docs__lookup" in html


def test_chat_panel_initializes_context_ui(qapp, store, workspace):
    panel = ChatPanel(store, cwd=str(workspace))

    assert panel._context_ui_suspended is False
    deadline = time.monotonic() + 2
    while panel.context_ring._budget is None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert panel.context_ring._budget is not None
    panel.close()
    panel.deleteLater()
    qapp.processEvents()


def test_edit_file_tool_debug_counts_old_text_matches(workspace):
    target = workspace / "src" / "main.py"
    target.write_bytes(b"import sys\r\nfrom pathlib import Path\r\n")

    text = _tool_debug_text(
        "edit_file",
        {
            "path": "src/main.py",
            "edits": [{"oldText": "import sys\n", "newText": ""}],
        },
        "[tool error] edits[0].oldText in src/main.py must match exactly once; found 0.",
        str(workspace),
    )

    assert "Tool: edit_file" in text
    assert "Output:" in text
    assert "resolved:" in text
    assert "edits[0].oldText exact occurrences: 0" in text
    assert "edits[0].oldText newline-flexible occurrences: 1" in text
    assert "edits[0].oldText repr: 'import sys\\n'" in text


def test_edit_file_tool_debug_omits_newline_flexible_when_same(workspace):
    target = workspace / "src" / "main.py"
    target.write_text("import sys\n", encoding="utf-8")

    text = _tool_debug_text(
        "edit_file",
        {
            "path": "src/main.py",
            "edits": [{"oldText": "import sys", "newText": ""}],
        },
        "",
        str(workspace),
    )

    assert "edits[0].oldText exact occurrences: 1" in text
    assert "newline-flexible occurrences" not in text


def test_tool_notice_context_menu_copies_debug_info(qapp, store, workspace, monkeypatch):
    from PyQt6.QtCore import QPoint
    from PyQt6.QtWidgets import QLabel, QMenu, QWidget

    parent = QWidget()
    label = QLabel("Tool error: exact match failed", parent)
    label.setProperty("aichs-tool-text", "Tool error: exact match failed")
    label.setProperty("aichs-tool-debug-text", "debug payload")
    copied = []

    def choose_copy_debug(menu, _pos):
        return menu.actions()[1]

    clipboard = SimpleNamespace(setText=copied.append)
    monkeypatch.setattr(QMenu, "exec", choose_copy_debug)
    monkeypatch.setattr(
        "ui.widgets.chat_panel.QGuiApplication",
        SimpleNamespace(clipboard=lambda: clipboard),
    )

    ChatPanel._show_tool_notice_menu(parent, label, QPoint(0, 0))

    assert copied == ["debug payload"]
    parent.close()
    parent.deleteLater()
    qapp.processEvents()


def test_tool_notice_context_menu_copies_message(qapp, store, workspace, monkeypatch):
    from PyQt6.QtCore import QPoint
    from PyQt6.QtWidgets import QLabel, QMenu, QWidget

    parent = QWidget()
    label = QLabel("Tool error: exact match failed", parent)
    label.setProperty("aichs-tool-text", "Tool error: exact match failed")
    label.setProperty("aichs-tool-debug-text", "debug payload")
    copied = []

    def choose_copy_message(menu, _pos):
        return menu.actions()[0]

    clipboard = SimpleNamespace(setText=copied.append)
    monkeypatch.setattr(QMenu, "exec", choose_copy_message)
    monkeypatch.setattr(
        "ui.widgets.chat_panel.QGuiApplication",
        SimpleNamespace(clipboard=lambda: clipboard),
    )

    ChatPanel._show_tool_notice_menu(parent, label, QPoint(0, 0))

    assert copied == ["Tool error: exact match failed"]
    parent.close()
    parent.deleteLater()
    qapp.processEvents()


def test_tool_notice_details_toggle_shows_input_and_output(qapp):
    from PyQt6.QtWidgets import QFrame, QPlainTextEdit, QToolButton

    widget = _ToolNoticeWidget("Using tool 'lookup'", "Inputs:\n{}")
    details = widget.findChild(QFrame, "aichs-tool-details-panel")
    boxes = widget.findChildren(QPlainTextEdit)
    button = widget.findChild(QToolButton, "aichs-tool-details-toggle")

    assert details is not None
    assert button is not None
    assert boxes
    assert button.text() == ">"
    assert details.isHidden()

    widget.set_debug_text("Inputs:\n{\"q\":\"x\"}\nOutput:\nok")
    button.click()

    assert not details.isHidden()
    assert button.text() == "v"
    assert any("\"q\":\"x\"" in box.toPlainText() for box in boxes)
    assert any("ok" in box.toPlainText() for box in boxes)
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def test_tool_notice_hides_empty_inputs(qapp):
    widget = _ToolNoticeWidget("Using tool 'lookup'", "Tool: lookup\nCWD: C:\\repo\nOutput:\nok")

    assert widget._section_frames["inputs"].isHidden()
    assert "not captured" not in widget._section_boxes["inputs"].toPlainText()
    assert widget._section_boxes["output"].toPlainText() == "ok"
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def test_mcp_notice_error_uses_mcp_tool_label_and_clean_output(qapp):
    widget = _ToolNoticeWidget(
        "Using tool 'mcp__docs__lookup'",
        "Tool: mcp__docs__lookup\nCWD: C:\\repo\nOutput:\n[tool error] Connection closed",
    )

    assert widget._meta_title_labels["tool"].text() == "MCP tool"
    assert widget._section_titles["output"].text() == "Error"
    assert widget._section_boxes["output"].toPlainText() == "Connection closed"
    assert not widget._error_status.isHidden()
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def test_tool_notice_error_status_hides_on_success(qapp):
    widget = _ToolNoticeWidget("Using tool 'lookup'", "Tool: lookup\nCWD: C:\\repo\nOutput:\n[tool error] failed")

    assert not widget._error_status.isHidden()
    widget.set_debug_text("Tool: lookup\nCWD: C:\\repo\nOutput:\nok")

    assert widget._meta_title_labels["tool"].text() == "Tool"
    assert widget._section_titles["output"].text() == "Output"
    assert widget._section_boxes["output"].toPlainText() == "ok"
    assert widget._error_status.isHidden()
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def test_tool_notice_renders_image_output_preview(qapp):
    import base64
    import json

    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    from PyQt6.QtGui import QColor, QPixmap
    from PyQt6.QtWidgets import QLabel

    pixmap = QPixmap(2, 2)
    pixmap.fill(QColor("#ff0000"))
    raw = QByteArray()
    buffer = QBuffer(raw)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    payload = {
        "returnValue": {
            "image": {
                "mimeType": "image/png",
                "data": base64.b64encode(bytes(raw)).decode("ascii"),
            }
        }
    }
    widget = _ToolNoticeWidget(
        "Using tool 'mcp__docs__screenshot'",
        f"Tool: mcp__docs__screenshot\nCWD: C:\\repo\nOutput:\n{json.dumps(payload)}",
    )

    image = widget.findChild(QLabel, "aichs-tool-output-image")

    assert image is not None
    assert not image.isHidden()
    assert image.pixmap() is not None
    assert not image.pixmap().isNull()
    assert "base64 image/png data omitted" in widget._section_boxes["output"].toPlainText()
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def test_tool_notice_renders_standard_mcp_image_output_preview(qapp):
    import base64
    import json

    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    from PyQt6.QtGui import QColor, QPixmap
    from PyQt6.QtWidgets import QLabel

    pixmap = QPixmap(2, 2)
    pixmap.fill(QColor("#00ff00"))
    raw = QByteArray()
    buffer = QBuffer(raw)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    payload = {
        "content": [
            {"type": "text", "text": "captured viewport"},
            {
                "type": "image",
                "mimeType": "image/png",
                "data": base64.b64encode(bytes(raw)).decode("ascii"),
            },
        ]
    }
    widget = _ToolNoticeWidget(
        "Using tool 'mcp__docs__screenshot'",
        f"Tool: mcp__docs__screenshot\nCWD: C:\\repo\nOutput:\n{json.dumps(payload)}",
    )

    image = widget.findChild(QLabel, "aichs-tool-output-image")
    output = widget._section_boxes["output"].toPlainText()

    assert image is not None
    assert not image.isHidden()
    assert image.pixmap() is not None
    assert not image.pixmap().isNull()
    assert "captured viewport" in output
    assert "Image: image/png" in output
    assert payload["content"][1]["data"] not in output
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def test_tool_output_display_text_summarizes_standard_mcp_content_types():
    import json

    payload = {
        "content": [
            {"type": "audio", "mimeType": "audio/wav", "data": "abcdef"},
            {
                "type": "resource_link",
                "name": "main.py",
                "uri": "file:///repo/main.py",
                "mimeType": "text/x-python",
            },
            {
                "type": "resource",
                "resource": {
                    "uri": "file:///repo/readme.md",
                    "mimeType": "text/markdown",
                    "text": "# Title",
                },
            },
            {
                "type": "resource",
                "resource": {
                    "uri": "file:///repo/screenshot.png",
                    "mimeType": "image/png",
                    "blob": "abc123",
                },
            },
        ],
        "structuredContent": {"ok": True},
    }

    text = _tool_output_display_text(json.dumps(payload))

    assert "Audio: audio/wav" in text
    assert "Resource link: main.py" in text
    assert "Embedded resource: file:///repo/readme.md (text/markdown)" in text
    assert "# Title" in text
    assert "Embedded resource: image/png" in text
    assert "URI: file:///repo/screenshot.png" in text
    assert "Structured content:" in text
    assert "abc123" not in text


def test_chat_ref_context_dedupes_and_names_exact_tool():
    text = _chat_ref_context([
        {"id": "c1", "title": "  Viewport   Picking "},
        {"id": "c1", "title": "Duplicate"},
    ])

    assert "read_project_chat" in text
    assert "Viewport Picking (conversation_id: c1)" in text
    assert text.count("conversation_id: c1") == 1
