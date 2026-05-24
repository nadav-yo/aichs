import json
import copy
import os
import re
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QFrame,
    QLabel, QPushButton, QComboBox,
)
from PyQt6.QtCore import Qt, QPoint, QTimer, pyqtSignal

from config import IGNORED, MAX_FILE_PREVIEW_BYTES, MAX_TOOL_READ_BYTES
from config import MODELS, MODEL_PROVIDER
from storage.repository import ConversationStore
from storage.settings import SettingsStore
from services.chat import ChatThread
from services.compaction import CompactionThread, should_compact, can_compact, _estimate_tokens
from services.content import build_user_content, content_preview
from services.auto_title import TitleThread
from services.context_budget import analyze_context
from services.model_registry import api_key_env_var, get_provider_config, load_user_providers
from services.workspace import build_system, agents_md
from services.export import export_conversation_dialog
from ui.theme import (
    palette, input_bar_style, agents_banner_style,
    send_button_style, stop_button_style, floating_button_style,
    tool_notice_style, center_notice_style, icon_button_style,
)
from services.skills import load_all as load_skills
from services.slash_commands import BUILTIN_COMMANDS, parse_builtin_command
from ui.widgets.bubble import MessageBubble
from ui.widgets.code_card import ArtifactCard
from ui.widgets.message_input import ComposerWidget
from ui.widgets.file_mention_picker import FileMentionPicker
from ui.widgets.skill_picker import SkillPicker
from ui.widgets.terminal_card import TerminalCard
from ui.widgets.context_ring import ContextRing
from ui.widgets.context_breakdown import ContextBreakdownDialog

_INITIAL_RENDER_BYTES = 1 * 1024 * 1024
_INITIAL_RENDER_MESSAGES = 150
_OLDER_RENDER_BYTES = 512 * 1024
_OLDER_RENDER_MESSAGES = 75


class _ScrollHost(QWidget):
    """Scroll area container with a floating jump-to-bottom button."""

    def __init__(self, scroll: QScrollArea, jump_btn: QPushButton, parent=None):
        super().__init__(parent)
        self._jump_btn = jump_btn

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

        jump_btn.setParent(self)
        jump_btn.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        btn = self._jump_btn
        btn.move(self.width() - btn.width() - 16, self.height() - btn.height() - 16)


class ChatPanel(QWidget):
    saved        = pyqtSignal()
    open_code    = pyqtSignal(str, str)   # content, title
    file_written = pyqtSignal(str)

    def __init__(self, store: ConversationStore, cwd: str = "",
                 settings: SettingsStore | None = None, parent=None):
        super().__init__(parent)
        self.store              = store
        self._settings          = settings or SettingsStore()
        self.cwd                = cwd or os.getcwd()
        self.history            = []
        self.conv_id            = None
        self.conv_data          = None
        self.active_bubble      = None
        self.thread             = None
        self._active_run_conv_id = None
        self._active_run_data   = None
        self._active_run_history = None
        self.compaction_thread  = None
        self.title_thread       = None
        self._last_write_path   = ""
        self._active_terminal   = None
        self._auto_scroll       = True
        self._programmatic_scroll = False
        self._bubbles: dict[int, MessageBubble] = {}
        self._queued_messages: list[dict] = []
        self._render_start_index = 0
        self._older_btn: QPushButton | None = None
        self._stream_buffer: list[str] = []
        self._stream_flush_timer = QTimer(self)
        self._stream_flush_timer.setInterval(100)
        self._stream_flush_timer.timeout.connect(self._flush_stream_buffer)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # top bar
        bar = QHBoxLayout()
        bar.setContentsMargins(16, 10, 16, 8)
        bar.setSpacing(6)

        self.provider_combo = QComboBox()
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)

        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(220)
        self.model_combo.currentTextChanged.connect(self._on_model_changed)

        bar.addWidget(self.provider_combo)
        bar.addWidget(self.model_combo)
        bar.addStretch()

        self.export_btn = QPushButton("Export")
        self.export_btn.setToolTip("Export conversation as Markdown")
        self.export_btn.clicked.connect(self.export_conversation)
        bar.addWidget(self.export_btn)

        self.context_ring = ContextRing()
        self.context_ring.clicked.connect(self._show_context_breakdown)
        bar.addWidget(self.context_ring)

        root.addLayout(bar)

        self.refresh_models()

        self._sep = QFrame()
        self._sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(self._sep)

        # AGENTS.md banner
        self._agents_banner = QLabel()
        self._agents_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._agents_banner.setStyleSheet(agents_banner_style())
        self._agents_banner.hide()
        root.addWidget(self._agents_banner)
        self._refresh_agents_banner()

        # messages
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.msg_container = QWidget()
        self.msg_layout = QVBoxLayout(self.msg_container)
        self.msg_layout.setContentsMargins(0, 16, 0, 16)
        self.msg_layout.setSpacing(2)
        self.msg_layout.addStretch()

        self.scroll.setWidget(self.msg_container)

        self.jump_btn = QPushButton("↓")
        self.jump_btn.setFixedSize(36, 36)
        self.jump_btn.hide()
        self.jump_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.jump_btn.clicked.connect(self._resume_auto_scroll)

        self.scroll_host = _ScrollHost(self.scroll, self.jump_btn)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
        root.addWidget(self.scroll_host, 1)

        # input bar
        self._input_frame = QFrame()
        input_col = QVBoxLayout(self._input_frame)
        input_col.setContentsMargins(16, 12, 16, 14)
        input_col.setSpacing(8)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self.composer = ComposerWidget()
        self.composer.send_requested.connect(self.send)
        self.composer.input.edit_last_requested.connect(self.edit_last_message)

        self._skill_picker: SkillPicker | None = None
        self._file_picker: FileMentionPicker | None = None
        self.composer.input.slash_changed.connect(self._on_slash_changed)
        self.composer.input.picker_next.connect(lambda: self._skill_picker and self._skill_picker.select_next())
        self.composer.input.picker_prev.connect(lambda: self._skill_picker and self._skill_picker.select_prev())
        self.composer.input.picker_confirm.connect(lambda: self._skill_picker and self._skill_picker.confirm())
        self.composer.input.mention_changed.connect(self._on_file_mention_changed)
        self.composer.input.mention_next.connect(lambda: self._file_picker and self._file_picker.select_next())
        self.composer.input.mention_prev.connect(lambda: self._file_picker and self._file_picker.select_prev())
        self.composer.input.mention_confirm.connect(lambda: self._file_picker and self._file_picker.confirm())

        self.btn = QPushButton("Send")
        self.btn.setFixedHeight(36)
        self.btn.clicked.connect(self.send)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setFixedHeight(36)
        self.stop_btn.clicked.connect(self._stop_streaming)
        self.stop_btn.hide()

        self._queue_label = QLabel()
        self._queue_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._queue_label.hide()

        self._queue_list = QWidget()
        self._queue_list_layout = QVBoxLayout(self._queue_list)
        self._queue_list_layout.setContentsMargins(0, 0, 0, 0)
        self._queue_list_layout.setSpacing(4)
        self._queue_list.hide()

        input_row.addWidget(self.composer, 1)
        input_row.addWidget(self.btn)
        input_row.addWidget(self.stop_btn)
        input_col.addLayout(input_row)
        input_col.addWidget(self._queue_label)
        input_col.addWidget(self._queue_list)
        root.addWidget(self._input_frame)

        self._apply_chrome()

    # ── public API ────────────────────────────────────────────────────────────

    def new_conversation(self):
        self._save()
        self.history       = []
        self.conv_id       = None
        self.conv_data     = None
        self.active_bubble = None
        self._stream_buffer.clear()
        self._stream_flush_timer.stop()
        self._bubbles      = {}
        self._render_start_index = 0
        self._older_btn = None
        self._update_queue_ui()
        self._clear_bubbles()
        self._update_context_ui()
        self._apply_default_model(self.provider_combo.currentText())
        self._refresh_agents_banner()
        self.composer.focus_input()

    def load_conversation(self, path: str):
        data = self.store.load(path)
        self.new_conversation()
        self.conv_id   = data["id"]
        self.conv_data = data
        self.history   = data["messages"]
        self._set_model(data.get("model", MODELS["claude"][1]))
        self._update_queue_ui()
        self._render_history_tail()
        self._scroll_to_bottom_later()

    def update_title(self, conv_id: str, title: str):
        if self.conv_id == conv_id and self.conv_data is not None:
            self.conv_data["title"] = title

    def is_streaming(self) -> bool:
        return self.thread is not None

    def stop_streaming(self):
        if self.thread:
            self._stop_streaming()

    def attach_file(self, path: str):
        try:
            rel = Path(path).resolve().relative_to(Path(self.cwd).resolve()).as_posix()
        except ValueError:
            rel = os.path.basename(path)
        self.composer.input.add_file_mention(rel)
        self.composer.focus_input()

    def edit_last_message(self):
        if self.thread:
            return
        for i in range(len(self.history) - 1, -1, -1):
            if self.history[i]["role"] != "user":
                continue
            bubble = self._bubbles.get(i)
            if bubble:
                bubble._start_edit()
                self.scroll.ensureWidgetVisible(bubble)
            return

    def export_conversation(self):
        if not self.history:
            return
        if self.conv_id and self.conv_data is not None:
            self._save()
            data = dict(self.conv_data)
            data["messages"] = list(self.history)
        else:
            data = {
                "title": "Untitled",
                "model": self.model_combo.currentText(),
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "messages": list(self.history),
            }
        export_conversation_dialog(data, parent=self.window())

    # ── send / receive ────────────────────────────────────────────────────────

    def send(self):
        text = self.composer.text()
        images = self.composer.strip.images()
        files = _mentioned_files(self.cwd, text)
        if not text and not images:
            return

        cmd = parse_builtin_command(text) if text and not images else None
        if cmd:
            if self.thread:
                self._add_notice("Finish the current response before running a command.")
                return
            self.composer.clear()
            self.composer.input.exit_slash_mode()
            self.composer.input.exit_mention_mode()
            if self._skill_picker:
                self._skill_picker.hide()
            if self._file_picker:
                self._file_picker.hide()
            self._run_builtin_command(cmd)
            return

        draft = {
            "content": build_user_content(text, images, files),
            "title_text": text or "Image",
            "skill": self.composer.active_skill(),
        }

        self.composer.clear()
        self.composer.clear_skill()
        self.composer.input.exit_mention_mode()
        self.composer.input.exit_slash_mode()
        if self._skill_picker:
            self._skill_picker.hide()
        if self._file_picker:
            self._file_picker.hide()

        if self.thread:
            self._ensure_conversation(draft["title_text"], self.model_combo.currentText())
            draft["_conv_id"] = self.conv_id
            self._queued_messages.append(draft)
            self._update_queue_ui()
            return

        self._send_draft(draft)

    def _send_draft(self, draft: dict):
        model = self.model_combo.currentText()
        content = draft["content"]
        title_text = draft["title_text"]

        self._ensure_conversation(title_text, model)

        self._enter_streaming()

        now = datetime.now().isoformat()
        self.history.append({"role": "user", "content": content, "created_at": now})
        user_idx = len(self.history) - 1
        if self._render_start_index == user_idx:
            self._render_start_index = user_idx
        self._add_bubble(content, is_user=True, history_index=user_idx, timestamp=now)

        self._save()

        self._start_assistant(skill=draft.get("skill"))

    def _ensure_conversation(self, title_text: str, model: str):
        if self.conv_id is not None:
            return
        self.conv_id = ConversationStore.new_id()
        now = datetime.now().isoformat()
        self.conv_data = {
            "id":         self.conv_id,
            "title":      ConversationStore.make_title(title_text),
            "title_auto": True,
            "created_at": now,
            "updated_at": now,
            "model":      model,
            "messages":   [],
        }
        self.store.save(self.conv_id, self.conv_data)
        self.saved.emit()

    def _start_assistant(self, skill=None):
        model = self.model_combo.currentText()
        self.active_bubble = self._add_bubble("", is_user=False, typing=True)
        self._active_run_conv_id = self.conv_id
        self._active_run_history = copy.deepcopy(self.history)
        self._active_run_data = copy.deepcopy(self.conv_data) if self.conv_data else None
        if self._active_run_data is not None:
            self._active_run_data["messages"] = copy.deepcopy(self._active_run_history)

        system = self._build_system(skill)
        allowed_tools = skill.tools if skill else None
        self.thread = ChatThread(model, copy.deepcopy(self._active_run_history), system, self.cwd,
                                 allowed_tools=allowed_tools)
        self.thread.chunk.connect(self._on_chunk)
        self.thread.tool_called.connect(self._on_tool_called)
        self.thread.bash_line.connect(self._on_bash_line)
        self.thread.tool_result.connect(self._on_tool_result)
        self.thread.done.connect(self._on_done)
        self.thread.error.connect(self._on_error)
        self.thread.start()

    def regenerate(self):
        if self.thread:
            return
        user_idx = self._find_turn_user_index()
        if user_idx is None:
            return
        self.history = self.history[: user_idx + 1]
        bubble = self._bubbles.get(user_idx)
        if bubble:
            self._truncate_ui_after(bubble)
        for k in list(self._bubbles.keys()):
            if k > user_idx:
                del self._bubbles[k]
        self._save()
        self._enter_streaming()
        self._start_assistant()

    def _edit_resend(self, idx: int, text: str):
        if self.thread or idx < 0 or idx >= len(self.history):
            return
        if not text:
            return
        old = self.history[idx]
        content = old["content"]
        if isinstance(content, list):
            blocks = [b for b in content if b.get("type") != "text"]
            if text:
                blocks.insert(0, {"type": "text", "text": text})
            new_content = blocks if blocks else text
        else:
            new_content = text

        bubble = self._bubbles.get(idx)
        if bubble:
            self._truncate_ui_after(bubble)
        for k in list(self._bubbles.keys()):
            if k >= idx:
                del self._bubbles[k]

        self.history = self.history[:idx]
        now = datetime.now().isoformat()
        self.history.append({"role": "user", "content": new_content, "created_at": now})
        new_idx = len(self.history) - 1
        self._add_bubble(new_content, is_user=True, history_index=new_idx, timestamp=now)
        self._save()
        self._enter_streaming()
        self._start_assistant()

    def _branch(self, idx: int):
        if idx < 0 or idx >= len(self.history):
            return
        conv_id = ConversationStore.new_id()
        base_title = (
            self.conv_data.get("title", "Untitled") if self.conv_data else "Conversation"
        )
        data = {
            "id":         conv_id,
            "title":      f"{base_title} (branch)",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "model":      self.model_combo.currentText(),
            "messages":   [dict(m) for m in self.history[: idx + 1]],
        }
        self.store.save(conv_id, data)
        self.saved.emit()

    # ── slots ─────────────────────────────────────────────────────────────────

    def _on_provider_changed(self, provider: str):
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(MODELS.get(provider, []))
        self.model_combo.blockSignals(False)
        self._apply_default_model(provider)

    def _on_model_changed(self, model: str):
        if not model:
            return
        self._update_context_ui()
        provider = self.provider_combo.currentText()
        data = self._settings.load()
        defaults = data.get("default_models", {})
        if defaults.get(provider) == model:
            return
        defaults[provider] = model
        self._settings.update({"default_models": defaults})

    def _apply_default_model(self, provider: str):
        defaults = self._settings.load().get("default_models", {})
        model = defaults.get(provider)
        if model and model in MODELS.get(provider, []):
            self.model_combo.blockSignals(True)
            self.model_combo.setCurrentText(model)
            self.model_combo.blockSignals(False)

    def refresh_models(self):
        provider = self.provider_combo.currentText()
        model = self.model_combo.currentText()
        providers = self._configured_providers()

        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        self.provider_combo.addItems(providers)
        self.provider_combo.blockSignals(False)

        if provider in providers:
            self.provider_combo.setCurrentText(provider)
        elif providers:
            self.provider_combo.setCurrentText(providers[0])
        self._on_provider_changed(self.provider_combo.currentText())
        if model and model in MODELS.get(self.provider_combo.currentText(), []):
            self.model_combo.setCurrentText(model)
        self._update_context_ui()

    def _configured_providers(self) -> list[str]:
        saved = self._settings.load()
        user_providers = set(load_user_providers().keys())
        provider_keys = saved.get("provider_api_keys", {})
        configured = []
        for provider in MODELS:
            cfg = get_provider_config(provider)
            if not cfg:
                continue
            if provider in user_providers:
                configured.append(provider)
                continue
            key = str(provider_keys.get(provider, "")).strip()
            if not key and provider == "claude":
                key = str(saved.get("anthropic_api_key", "")).strip()
            if not key and provider == "openai":
                key = str(saved.get("openai_api_key", "")).strip()
            env_var = api_key_env_var(cfg.api_key_spec)
            if key or (env_var and os.environ.get(env_var)) or (cfg.api_key_spec and not env_var):
                configured.append(provider)
        return configured

    def _refresh_agents_banner(self):
        p = agents_md(self.cwd)
        if p:
            self._agents_banner.setText(f"  📋  {p.name}  ·  project memory active")
            self._agents_banner.show()
        else:
            self._agents_banner.hide()

    def _build_system(self, skill=None) -> str:
        custom = self._settings.load().get("system_prompt", "").strip()
        base = skill.prompt if skill else (custom or None)
        return build_system(self.cwd, base)

    def _on_slash_changed(self, text: str):
        if not text:
            if self._skill_picker:
                self._skill_picker.hide()
            return
        if self._skill_picker is None:
            self._skill_picker = SkillPicker(
                load_skills(self.cwd), BUILTIN_COMMANDS, parent=self,
            )
            self._skill_picker.skill_selected.connect(self._on_skill_selected)
            self._skill_picker.command_selected.connect(self._on_command_selected)
        self._skill_picker.filter(text)
        if self._skill_picker.count() == 0:
            self._skill_picker.hide()
            return
        self._position_skill_picker()
        self._skill_picker.show()
        self._skill_picker.raise_()

    def _on_file_mention_changed(self, text: str):
        if not text:
            if self._file_picker:
                self._file_picker.hide()
            return
        if self._file_picker is None:
            self._file_picker = FileMentionPicker(_list_mention_files(self.cwd), parent=self)
            self._file_picker.file_selected.connect(self._on_file_mention_selected)
        else:
            self._file_picker.set_files(_list_mention_files(self.cwd))
        self._file_picker.filter(text)
        if self._file_picker.count() == 0:
            self._file_picker.hide()
            return
        self._position_file_picker()
        self._file_picker.show()
        self._file_picker.raise_()

    def _position_skill_picker(self):
        frame_pos = self._input_frame.mapTo(self, QPoint(0, 0))
        w = self._input_frame.width()
        h = self._skill_picker.height()
        self._skill_picker.setFixedWidth(w)
        self._skill_picker.move(frame_pos.x(), frame_pos.y() - h - 4)

    def _position_file_picker(self):
        frame_pos = self._input_frame.mapTo(self, QPoint(0, 0))
        w = self._input_frame.width()
        h = self._file_picker.height()
        self._file_picker.setFixedWidth(w)
        self._file_picker.move(frame_pos.x(), frame_pos.y() - h - 4)

    def _on_file_mention_selected(self, rel_path: str, _abs_path: str):
        self.composer.input.insert_file_mention(rel_path)
        if self._file_picker:
            self._file_picker.hide()
        self.composer.focus_input()

    def _on_skill_selected(self, skill):
        self.composer.set_skill(skill)
        self.composer.input.clear()
        self.composer.input.exit_slash_mode()
        self._skill_picker.hide()
        self.composer.focus_input()

    def _on_command_selected(self, name: str):
        self.composer.clear()
        self.composer.input.exit_slash_mode()
        self._skill_picker.hide()
        self._run_builtin_command(name)

    def _run_builtin_command(self, name: str):
        if name == "compact":
            self.compact_conversation(force=True)

    def compact_conversation(self, force: bool = False):
        if self.thread or self.compaction_thread:
            return
        if not self.history:
            self._add_notice("Nothing to compact — start a conversation first.")
            return
        model = self.model_combo.currentText()
        if not force and not should_compact(model, self.history):
            self._add_notice("Context is not large enough to compact yet.")
            return
        if not can_compact(self.history):
            self._add_notice("Nothing to compact — recent messages already fit in context.")
            return
        self._set_input_enabled(False)
        self._add_notice("Compacting conversation context…")
        self.compaction_thread = CompactionThread(model, self.history)
        self.compaction_thread.done.connect(self._on_compacted)
        self.compaction_thread.error.connect(self._on_compaction_error)
        self.compaction_thread.start()

    def _on_chunk(self, text: str):
        if not self._active_run_is_current():
            return
        self._stream_buffer.append(text)
        if not self._stream_flush_timer.isActive():
            self._stream_flush_timer.start()

    def _on_tool_called(self, name: str, inputs: dict):
        if not self._active_run_is_current():
            return
        self._flush_stream_buffer()
        self._remove_empty_active_typing_bubble()
        self.active_bubble = None
        if name == "write_file":
            path = inputs.get("path", "")
            self._last_write_path = path
            if path:
                self.file_written.emit(path)
        preview = json.dumps(inputs, ensure_ascii=False)
        if len(preview) > 120:
            preview = preview[:120] + "…"
        self._add_tool_notice(f"⚙ {name}({preview})")
        if name == "bash":
            self._active_terminal = self._add_terminal_card()

    def _on_bash_line(self, line: str):
        if not self._active_run_is_current():
            return
        if self._active_terminal:
            self._active_terminal.append_line(line)
            self._bottom()

    def _on_tool_result(self, name: str, output: str):
        if not self._active_run_is_current():
            return
        if name == "bash" and self._active_terminal:
            import re
            m = re.search(r'\[exit (\d+)\]', output)
            self._active_terminal.finish(int(m.group(1)) if m else 0)
            self._active_terminal = None
        elif name == "write_file" and self._last_write_path:
            self._add_file_card(self._last_write_path)
            self._last_write_path = ""
        else:
            preview = output[:200].replace("\n", " ") + ("…" if len(output) > 200 else "")
            self._add_tool_notice(f"↳ {preview}")
        self.active_bubble = None

    def _on_done(self, full: str):
        is_current = self._active_run_is_current()
        if is_current:
            self._flush_stream_buffer()
        else:
            self._stream_buffer.clear()
            self._stream_flush_timer.stop()
        now = datetime.now().isoformat()
        assistant_msg = {"role": "assistant", "content": full, "created_at": now}
        run_conv_id = self._active_run_conv_id
        run_history = list(self._active_run_history or [])
        run_data = dict(self._active_run_data or {})

        if is_current:
            self.history.append(assistant_msg)
            asst_idx = len(self.history) - 1
        else:
            run_history.append(assistant_msg)
            if run_conv_id and run_data:
                run_data["messages"] = run_history
                run_data["updated_at"] = now
                self.store.save(run_conv_id, run_data)
                self.saved.emit()
            asst_idx = -1

        bubble = self.active_bubble if is_current else None
        if is_current:
            self.active_bubble = None
        self.thread = None
        self._active_run_conv_id = None
        self._active_run_data = None
        self._active_run_history = None

        if is_current and bubble is None and full:
            bubble = self._add_bubble(full, is_user=False)

        if is_current and bubble:
            bubble._history_index = asst_idx
            self._bubbles[asst_idx] = bubble
            bubble_idx = self.msg_layout.indexOf(bubble)
            offset = [1]

            def add_artifact(lang, code):
                title = lang or "snippet"
                card = self._wrap_artifact(
                    ArtifactCard(lang, code,
                                 lambda c, t: self.open_code.emit(c, t), title)
                )
                self.msg_layout.insertWidget(bubble_idx + offset[0], card)
                offset[0] += 1
                self._bottom()

            bubble.finalize(full, on_artifact=add_artifact)

        self._exit_streaming()
        if is_current:
            self._maybe_auto_title()
            model = self.model_combo.currentText()
            if should_compact(model, self.history):
                self.compact_conversation(force=False)
            else:
                self._save()
                self._start_next_queued()
        else:
            self._start_next_queued()

    def _on_compacted(self, compacted: list):
        self.history = compacted
        self.compaction_thread = None
        self._render_history_tail()
        self._scroll_to_bottom_later()
        self._add_notice("Context compacted — conversation continues.")
        self._set_input_enabled(True)
        self._update_context_ui()
        self._save()
        self._start_next_queued()

    def _on_compaction_error(self, msg: str):
        self.compaction_thread = None
        self._add_notice(f"Compaction failed: {msg}")
        self._set_input_enabled(True)
        self._update_context_ui()
        self._save()
        self._start_next_queued()

    def _maybe_auto_title(self):
        if not self.conv_id or not self.conv_data:
            return
        if not self.conv_data.get("title_auto", False):
            return
        if sum(1 for m in self.history if m["role"] == "assistant") != 1:
            return

        user_msg = next((m for m in self.history if m["role"] == "user"), None)
        asst_msg = next((m for m in self.history if m["role"] == "assistant"), None)
        if not user_msg or not asst_msg:
            return

        if self.title_thread and self.title_thread.isRunning():
            return

        self.title_thread = TitleThread(
            self.conv_id,
            self.model_combo.currentText(),
            content_preview(user_msg["content"]),
            content_preview(asst_msg["content"]),
        )
        self.title_thread.done.connect(self._on_auto_title_done)
        self.title_thread.error.connect(self._on_auto_title_error)
        self.title_thread.start()

    def _on_auto_title_done(self, conv_id: str, title: str):
        self.title_thread = None
        if conv_id != self.conv_id or self.conv_data is None:
            return
        if not self.conv_data.get("title_auto", False):
            return
        self.conv_data["title"] = title
        self.conv_data["title_auto"] = False
        self.store.save(self.conv_id, self.conv_data)
        self.saved.emit()

    def _on_auto_title_error(self, _msg: str):
        self.title_thread = None

    def _on_error(self, msg: str):
        is_current = self._active_run_is_current()
        if is_current:
            self._flush_stream_buffer()
        else:
            self._stream_buffer.clear()
            self._stream_flush_timer.stop()
        if is_current and self.active_bubble:
            self.active_bubble.append(f"[Error: {msg}]")
        if is_current:
            self.active_bubble = None
        self.thread = None
        self._active_run_conv_id = None
        self._active_run_data = None
        self._active_run_history = None
        self._exit_streaming()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _set_model(self, model: str):
        provider = MODEL_PROVIDER.get(model, "claude")
        self.provider_combo.setCurrentText(provider)
        self._on_provider_changed(provider)
        self.model_combo.setCurrentText(model)

    def set_model(self, model: str):
        if model in MODEL_PROVIDER:
            self._set_model(model)

    def _save(self):
        if self.conv_id and self.conv_data is not None:
            self.conv_data["messages"]   = self.history
            self.conv_data["updated_at"] = datetime.now().isoformat()
            self.store.save(self.conv_id, self.conv_data)
            self.saved.emit()
        self._update_context_ui()

    def _context_budget(self):
        custom = self._settings.load().get("system_prompt", "").strip()
        return analyze_context(
            self.model_combo.currentText(),
            self.cwd,
            self.history,
            custom_system=custom,
        )

    def _update_context_ui(self):
        budget = self._context_budget()
        self.context_ring.set_budget(budget)

    def _flush_stream_buffer(self):
        if not self._active_run_is_current():
            self._stream_buffer.clear()
            self._stream_flush_timer.stop()
            return
        if not self._stream_buffer:
            self._stream_flush_timer.stop()
            return
        text = "".join(self._stream_buffer)
        self._stream_buffer.clear()
        if self.active_bubble is None:
            self.active_bubble = self._add_bubble("", is_user=False, typing=True)
        if self.active_bubble:
            self.active_bubble.append(text)
            self._bottom()

    def _active_run_is_current(self) -> bool:
        return bool(self._active_run_conv_id and self.conv_id == self._active_run_conv_id)

    def _show_context_breakdown(self):
        ContextBreakdownDialog(
            self._context_budget(),
            self.model_combo.currentText(),
            parent=self.window(),
        ).exec()

    def _add_terminal_card(self) -> TerminalCard:
        card = TerminalCard()
        self.msg_layout.insertWidget(self.msg_layout.count() - 1, self._wrap_artifact(card))
        self._bottom()
        return card

    def _add_file_card(self, file_path: str):
        abs_path = str(
            Path(file_path) if Path(file_path).is_absolute() else Path(self.cwd) / file_path
        )
        ext  = os.path.splitext(abs_path)[1].lstrip(".") or "file"
        name = os.path.basename(abs_path)

        def on_open(_, __):
            try:
                content = _read_text_preview(abs_path)
            except OSError as e:
                content = f"[Could not read file: {e}]"
            self.open_code.emit(content, name)

        card = self._wrap_artifact(ArtifactCard(ext, "", on_open, name))
        self.msg_layout.insertWidget(self.msg_layout.count() - 1, card)
        self._bottom()

    def _wrap_artifact(self, card) -> QWidget:
        """Left-align an ArtifactCard to match AI bubble positioning."""
        wrapper = QWidget()
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(16, 2, 16, 2)
        row.addWidget(card)
        row.addStretch()
        return wrapper

    def _add_tool_notice(self, text: str):
        lbl = QLabel(text)
        lbl.setObjectName("aicc-tool-notice")
        lbl.setWordWrap(True)
        lbl.setStyleSheet(tool_notice_style())
        self.msg_layout.insertWidget(self.msg_layout.count() - 1, lbl)
        self._bottom()

    def _add_notice(self, text: str):
        lbl = QLabel(text)
        lbl.setObjectName("aicc-center-notice")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(center_notice_style())
        self.msg_layout.insertWidget(self.msg_layout.count() - 1, lbl)
        self._bottom()

    def _add_bubble(self, content, is_user: bool, typing: bool = False,
                    history_index: int = -1, timestamp: str = "") -> MessageBubble:
        bubble = self._make_bubble(
            content, is_user, typing=typing,
            history_index=history_index, timestamp=timestamp,
        )
        self.msg_layout.insertWidget(self.msg_layout.count() - 1, bubble)
        if history_index >= 0:
            self._bubbles[history_index] = bubble
        self._bottom()
        return bubble

    def _make_bubble(self, content, is_user: bool, typing: bool = False,
                     history_index: int = -1, timestamp: str = "") -> MessageBubble:
        bubble = MessageBubble(
            content, is_user, typing=typing,
            history_index=history_index, timestamp=timestamp,
        )
        bubble.regenerate_requested.connect(lambda _: self.regenerate())
        bubble.edit_resend_requested.connect(self._edit_resend)
        bubble.branch_requested.connect(self._branch)
        bubble.file_clicked.connect(self._open_linked_file)
        return bubble

    def _open_linked_file(self, path: str):
        abs_path = path if os.path.isabs(path) else os.path.join(self.cwd, path)
        try:
            content = _read_text_preview(abs_path)
        except OSError:
            return
        self.open_code.emit(content, os.path.basename(abs_path))

    def _find_turn_user_index(self) -> int | None:
        i = len(self.history) - 1
        while i >= 0:
            msg = self.history[i]
            if msg["role"] == "user":
                content = msg.get("content")
                if isinstance(content, list) and content:
                    if content[0].get("type") == "tool_result":
                        i -= 1
                        continue
                return i
            i -= 1
        return None

    def _truncate_ui_after(self, widget: QWidget):
        idx = self.msg_layout.indexOf(widget)
        if idx < 0:
            return
        while self.msg_layout.count() > idx + 2:
            item = self.msg_layout.takeAt(idx + 1)
            if item.widget():
                item.widget().deleteLater()

    def _apply_chrome(self):
        p = palette()
        self._update_context_ui()
        self._sep.setStyleSheet(f"background:{p['BORDER']}; max-height:1px;")
        self._input_frame.setStyleSheet(input_bar_style())
        self.jump_btn.setStyleSheet(floating_button_style())
        self.btn.setStyleSheet(send_button_style())
        self.stop_btn.setStyleSheet(stop_button_style())

    def apply_appearance(self):
        self._apply_chrome()
        self.composer.apply_appearance()
        self._update_queue_ui()
        for i in range(self.msg_layout.count() - 1):
            w = self.msg_layout.itemAt(i).widget()
            if isinstance(w, MessageBubble):
                w.apply_appearance()
            elif w:
                for card in w.findChildren(ArtifactCard):
                    card.apply_appearance()
                for card in w.findChildren(TerminalCard):
                    card.apply_appearance()
        for lbl in self.msg_container.findChildren(QLabel):
            name = lbl.objectName()
            if name == "aicc-tool-notice":
                lbl.setStyleSheet(tool_notice_style())
            elif name == "aicc-center-notice":
                lbl.setStyleSheet(center_notice_style())

    def set_cwd(self, cwd: str):
        self.cwd = cwd
        self._refresh_agents_banner()
        self._update_context_ui()

    def _clear_bubbles(self):
        self._bubbles = {}
        self._older_btn = None
        while self.msg_layout.count() > 1:
            item = self.msg_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render_history_tail(self):
        self._clear_bubbles()
        self._render_start_index = _window_start(
            self.history,
            len(self.history),
            _INITIAL_RENDER_BYTES,
            _INITIAL_RENDER_MESSAGES,
        )
        self._sync_older_button()
        for i in range(self._render_start_index, len(self.history)):
            self._insert_history_bubble(i)

    def _prepend_history_page(self):
        if self._render_start_index <= 0:
            self._sync_older_button()
            return

        old_start = self._render_start_index
        new_start = _window_start(
            self.history,
            old_start,
            _OLDER_RENDER_BYTES,
            _OLDER_RENDER_MESSAGES,
        )
        if new_start >= old_start:
            return

        bar = self.scroll.verticalScrollBar()
        old_max = bar.maximum()
        old_value = bar.value()

        if self._older_btn:
            idx = self.msg_layout.indexOf(self._older_btn)
            if idx >= 0:
                item = self.msg_layout.takeAt(idx)
                if item.widget():
                    item.widget().deleteLater()
            self._older_btn = None

        for i in range(old_start - 1, new_start - 1, -1):
            self._insert_history_bubble(i, at_top=True)

        self._render_start_index = new_start
        self._sync_older_button()

        def restore():
            self._programmatic_scroll = True
            bar.setValue(old_value + (bar.maximum() - old_max))
            self._programmatic_scroll = False

        QTimer.singleShot(0, restore)

    def _insert_history_bubble(self, history_index: int, *, at_top: bool = False):
        msg = self.history[history_index]
        bubble = self._make_bubble(
            msg["content"],
            is_user=(msg["role"] == "user"),
            history_index=history_index,
            timestamp=msg.get("created_at", ""),
        )
        insert_at = 1 if at_top and self._older_btn else 0 if at_top else self.msg_layout.count() - 1
        self.msg_layout.insertWidget(insert_at, bubble)
        self._bubbles[history_index] = bubble
        return bubble

    def _sync_older_button(self):
        if self._render_start_index <= 0:
            if self._older_btn:
                idx = self.msg_layout.indexOf(self._older_btn)
                if idx >= 0:
                    item = self.msg_layout.takeAt(idx)
                    if item.widget():
                        item.widget().deleteLater()
                self._older_btn = None
            return

        if self._older_btn is None:
            self._older_btn = QPushButton()
            self._older_btn.setStyleSheet(self._older_button_style())
            self._older_btn.clicked.connect(self._prepend_history_page)
            self.msg_layout.insertWidget(0, self._older_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        self._older_btn.setText(f"Load older messages ({self._render_start_index} hidden)")
        self._older_btn.setStyleSheet(self._older_button_style())

    def _older_button_style(self) -> str:
        p = palette()
        return (
            f"QPushButton {{ background:{p['BG3']}; color:{p['TEXT_DIM']};"
            f"border:1px solid {p['BORDER']}; border-radius:8px;"
            "padding:6px 12px; margin:8px 0; }}"
            f"QPushButton:hover {{ color:{p['TEXT']}; background:{p['BORDER']}; }}"
        )

    def _remove_empty_active_typing_bubble(self):
        bubble = self.active_bubble
        if not bubble or not bubble.is_empty_typing():
            return
        idx = self.msg_layout.indexOf(bubble)
        if idx >= 0:
            item = self.msg_layout.takeAt(idx)
            if item.widget():
                item.widget().deleteLater()
        self.active_bubble = None

    def _enter_streaming(self):
        self._auto_scroll = True
        self.jump_btn.hide()
        self.composer.set_enabled(True)
        self.btn.setText("Queue")
        self.btn.setStyleSheet(send_button_style())
        self.btn.setEnabled(True)
        self.stop_btn.show()
        self.stop_btn.setEnabled(True)
        self.stop_btn.setStyleSheet(stop_button_style())
        self._update_queue_ui()
        self.composer.focus_input()

    def _exit_streaming(self):
        self.jump_btn.hide()
        self.composer.set_enabled(True)
        self.btn.setText("Send")
        self.btn.setStyleSheet(send_button_style())
        self.stop_btn.hide()
        self._update_queue_ui()
        self.composer.focus_input()

    def _stop_streaming(self):
        if self.thread:
            self.thread.cancel()
        self.stop_btn.setEnabled(False)

    def _set_input_enabled(self, enabled: bool):
        """Used for non-streaming states (compaction). Does not touch stop/send mode."""
        self.composer.set_enabled(enabled)
        if enabled:
            self.composer.focus_input()

    def _is_at_bottom(self, threshold: int = 40) -> bool:
        bar = self.scroll.verticalScrollBar()
        return bar.maximum() - bar.value() <= threshold

    def _on_scroll(self, _value: int):
        if self._programmatic_scroll:
            return
        if self.scroll.verticalScrollBar().value() <= 24 and self._render_start_index > 0:
            self._prepend_history_page()
            return
        if not self.thread:
            return
        if self._is_at_bottom():
            self._auto_scroll = True
            self.jump_btn.hide()
        else:
            self._auto_scroll = False
            self.jump_btn.show()
            self.jump_btn.raise_()

    def _resume_auto_scroll(self):
        self._auto_scroll = True
        self.jump_btn.hide()
        self._scroll_to_bottom(force=True)

    def _scroll_to_bottom(self, force: bool = False):
        if not force and not self._auto_scroll:
            return
        bar = self.scroll.verticalScrollBar()
        self._programmatic_scroll = True
        bar.setValue(bar.maximum())
        self._programmatic_scroll = False

    def _bottom(self):
        self._scroll_to_bottom()

    def _scroll_to_bottom_later(self):
        QTimer.singleShot(0, lambda: self._scroll_to_bottom(force=True))
        QTimer.singleShot(50, lambda: self._scroll_to_bottom(force=True))

    def _start_next_queued(self):
        if self.thread or self.compaction_thread or not self._queued_messages:
            return
        idx = self._next_queued_index_for_current_chat()
        if idx is None:
            self._update_queue_ui()
            return
        draft = self._queued_messages.pop(idx)
        self._update_queue_ui()
        self._send_draft(draft)

    def _next_queued_index_for_current_chat(self) -> int | None:
        if not self.conv_id:
            return None
        for idx, draft in enumerate(self._queued_messages):
            if draft.get("_conv_id") == self.conv_id:
                return idx
        return None

    def _update_queue_ui(self):
        while self._queue_list_layout.count():
            item = self._queue_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        visible = [
            (idx, draft)
            for idx, draft in enumerate(self._queued_messages)
            if draft.get("_conv_id") == self.conv_id
        ]
        count = len(visible)
        if count:
            noun = "message" if count == 1 else "messages"
            self._queue_label.setText(f"{count} queued {noun}")
            self._queue_label.setStyleSheet(center_notice_style())
            self._queue_label.show()
            for idx, draft in visible:
                self._queue_list_layout.addWidget(self._queue_item(idx, draft))
            self._queue_list.show()
        else:
            self._queue_label.hide()
            self._queue_list.hide()

    def _queue_item(self, idx: int, draft: dict) -> QWidget:
        row = QFrame()
        row.setObjectName("queueItem")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        label = QLabel(_queue_preview(draft))
        label.setObjectName("queuePreview")
        label.setWordWrap(False)

        cancel = QPushButton("x")
        cancel.setToolTip("Cancel queued message")
        cancel.setFixedSize(24, 24)
        cancel.clicked.connect(lambda _, i=idx: self._cancel_queued(i))

        layout.addWidget(label, 1)
        layout.addWidget(cancel)

        p = palette()
        row.setStyleSheet(
            f"QFrame#queueItem {{ background-color: {p['BG3']};"
            f" border: 1px solid {p['BORDER']}; border-radius: 8px; }}"
        )
        label.setStyleSheet(f"color: {p['TEXT_DIM']}; background: transparent;")
        cancel.setStyleSheet(icon_button_style(24))
        return row

    def _cancel_queued(self, idx: int):
        if 0 <= idx < len(self._queued_messages):
            self._queued_messages.pop(idx)
            self._update_queue_ui()


def _read_text_preview(path: str) -> str:
    p = Path(path)
    size = p.stat().st_size
    with p.open("rb") as f:
        raw = f.read(MAX_FILE_PREVIEW_BYTES + 1)
    truncated = len(raw) > MAX_FILE_PREVIEW_BYTES
    text = raw[:MAX_FILE_PREVIEW_BYTES].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n\n[Preview truncated: showing {MAX_FILE_PREVIEW_BYTES} of {size} bytes]"
    return text


def _queue_preview(draft: dict) -> str:
    content = draft.get("content")
    text = content_preview(content).replace("\n", " ").strip()
    if not text:
        text = draft.get("title_text", "Queued message")
    if len(text) > 120:
        text = text[:117].rstrip() + "..."
    return text


def _message_render_bytes(msg: dict) -> int:
    return len(content_preview(msg.get("content", "")).encode("utf-8", errors="replace"))


def _window_start(history: list[dict], end: int, byte_limit: int, message_limit: int) -> int:
    total = 0
    start = end
    while start > 0 and end - start < message_limit:
        size = _message_render_bytes(history[start - 1])
        if start < end and total + size > byte_limit:
            break
        total += size
        start -= 1
    return start


_MENTION_RE = re.compile(r'@(?:"([^"]+)"|([^\s]+))')


def _list_mention_files(cwd: str, limit: int = 800) -> list[tuple[str, str]]:
    root = Path(cwd).resolve()
    out: list[tuple[str, str]] = []
    try:
        walker = os.walk(root)
        for dirpath, dirnames, filenames in walker:
            dirnames[:] = [
                d for d in dirnames
                if d not in IGNORED and not d.startswith(".")
            ]
            for name in sorted(filenames, key=str.lower):
                if name in IGNORED or name.startswith("."):
                    continue
                abs_path = Path(dirpath) / name
                rel = abs_path.relative_to(root).as_posix()
                out.append((rel, str(abs_path)))
                if len(out) >= limit:
                    return out
    except OSError:
        return out
    return out


def _mentioned_files(cwd: str, text: str) -> list[dict]:
    root = Path(cwd).resolve()
    seen: set[str] = set()
    files: list[dict] = []
    for match in _MENTION_RE.finditer(text):
        raw = (match.group(1) or match.group(2) or "").strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)
        try:
            path = (root / raw).resolve()
            path.relative_to(root)
        except (OSError, ValueError):
            continue
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
            with path.open("rb") as f:
                data = f.read(MAX_TOOL_READ_BYTES + 1)
        except OSError:
            continue
        truncated = len(data) > MAX_TOOL_READ_BYTES
        content = data[:MAX_TOOL_READ_BYTES].decode("utf-8", errors="replace")
        files.append({
            "path": raw,
            "content": content,
            "truncated": truncated,
            "size": size,
        })
    return files
