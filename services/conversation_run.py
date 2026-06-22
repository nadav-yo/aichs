import copy
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from services.chat import ChatThread
from services.content import prepare_for_storage
from services.tool_policy import ToolApprovalBus
from storage.repository import ConversationStore


@dataclass
class ConversationRun:
    run_id: str
    conv_id: str
    thread: ChatThread
    data: dict
    partial_text: str = ""


class ConversationRunManager(QObject):
    conversation_created = pyqtSignal(str)
    conversation_updated = pyqtSignal(str)
    chunk = pyqtSignal(str, str, str)
    tool_called = pyqtSignal(str, str, str, dict)
    tool_result = pyqtSignal(str, str, str, str)
    approval_required = pyqtSignal(str, str, object, object)
    done = pyqtSignal(str, str, str)
    error = pyqtSignal(str, str, str)
    finished = pyqtSignal(str, str)

    def __init__(self, store: ConversationStore, cwd: str, parent=None):
        super().__init__(parent)
        self.store = store
        self.cwd = cwd
        self._runs: dict[str, ConversationRun] = {}
        self._pending_saves: set[str] = set()
        self._save_timer = QTimer(self)
        self._save_timer.setInterval(800)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._flush_pending_saves)

    def set_workspace(self, store: ConversationStore, cwd: str):
        self.store = store
        self.cwd = cwd

    def start(
        self,
        *,
        conv_id: str = "",
        title: str,
        prompt: str,
        model: str,
        system: str,
        allowed_tools,
        tool_policy,
        approval_bus,
        write_roots,
        crew_settings: dict,
        configured_providers: set[str],
        metadata: dict | None = None,
    ) -> ConversationRun:
        created = False
        conv_id = str(conv_id or "").strip()
        if conv_id:
            try:
                data = self.store.load_by_id(conv_id)
            except FileNotFoundError:
                data = {}
                created = True
        else:
            conv_id = ConversationStore.new_id()
            data = {}
            created = True
        now = datetime.now().isoformat()
        messages = prepare_for_storage(data.get("messages", [])) if isinstance(data, dict) else []
        messages.append(
            {
                "role": "user",
                "content": str(prompt or ""),
                "created_at": now,
                "synthetic": "canvas_run_prompt",
            }
        )
        data = {
            **(data if isinstance(data, dict) else {}),
            "id": conv_id,
            "title": str(title or "Canvas action")[:100],
            "title_auto": False,
            "created_at": str(data.get("created_at") or now) if isinstance(data, dict) else now,
            "updated_at": now,
            "model": str(model or ""),
            "cwd": self.cwd,
            "messages": prepare_for_storage(messages),
            "canvas": dict(metadata or {}),
        }
        self._save_data(conv_id, data)
        if created:
            self.conversation_created.emit(conv_id)

        run_id = uuid4().hex
        run_approval_bus = ToolApprovalBus(self)
        run_approval_bus.approval_needed.connect(
            lambda pending, bus=run_approval_bus, cid=conv_id, rid=run_id: self.approval_required.emit(cid, rid, bus, pending)
        )
        thread = ChatThread(
            model,
            copy.deepcopy(messages),
            system,
            self.cwd,
            allowed_tools=allowed_tools,
            tool_policy=tool_policy,
            approval_bus=run_approval_bus,
            write_roots=write_roots,
            enable_crew_tool=False,
            crew_settings=crew_settings,
            configured_providers=configured_providers,
            tool_surface="canvas",
        )
        run = ConversationRun(run_id=run_id, conv_id=conv_id, thread=thread, data=data)
        self._runs[run_id] = run
        thread.chunk.connect(lambda text, rid=run_id: self._on_chunk(rid, text))
        thread.tool_called.connect(lambda name, inputs, rid=run_id: self._on_tool_called(rid, name, inputs))
        thread.tool_result.connect(lambda name, output, rid=run_id: self._on_tool_result(rid, name, output))
        thread.history_updated.connect(lambda rid=run_id: self._on_history_updated(rid))
        thread.done.connect(lambda text, rid=run_id: self._on_done(rid, text))
        thread.error.connect(lambda text, rid=run_id: self._on_error(rid, text))
        thread.finished.connect(lambda rid=run_id: self._on_finished(rid))
        thread.start()
        return run

    def _on_chunk(self, run_id: str, text: str):
        run = self._runs.get(run_id)
        if run is None:
            return
        run.partial_text += str(text or "")
        self.chunk.emit(run.conv_id, run_id, str(text or ""))
        self._schedule_save(run_id)

    def _on_tool_called(self, run_id: str, name: str, inputs: dict):
        run = self._runs.get(run_id)
        if run is None:
            return
        run.partial_text = ""
        self._save_run_thread_history(run)
        self.tool_called.emit(run.conv_id, run_id, str(name or "tool"), dict(inputs or {}))

    def _on_tool_result(self, run_id: str, name: str, output: str):
        run = self._runs.get(run_id)
        if run is None:
            return
        self._save_run_thread_history(run)
        self.tool_result.emit(run.conv_id, run_id, str(name or "tool"), str(output or ""))

    def _on_history_updated(self, run_id: str):
        run = self._runs.get(run_id)
        if run is None:
            return
        run.partial_text = ""
        self._save_run_thread_history(run)

    def _on_done(self, run_id: str, text: str):
        run = self._runs.get(run_id)
        if run is None:
            return
        run.partial_text = ""
        history = prepare_for_storage(run.thread.history)
        final = str(text or "")
        if final and not _history_ends_with_assistant_text(history, final):
            history.append({"role": "assistant", "content": final, "created_at": datetime.now().isoformat()})
        self._save_run_messages(run, history)
        self.done.emit(run.conv_id, run_id, final)

    def _on_error(self, run_id: str, text: str):
        run = self._runs.get(run_id)
        if run is None:
            return
        message = str(text or "")
        history = prepare_for_storage(run.thread.history)
        if message:
            history.append(
                {
                    "role": "assistant",
                    "content": f"[Error: {message}]",
                    "created_at": datetime.now().isoformat(),
                    "synthetic": "canvas_run_error",
                }
            )
        self._save_run_messages(run, history)
        self.error.emit(run.conv_id, run_id, message)

    def _on_finished(self, run_id: str):
        run = self._runs.pop(run_id, None)
        if run is None:
            return
        self._pending_saves.discard(run_id)
        self.finished.emit(run.conv_id, run_id)

    def _schedule_save(self, run_id: str):
        self._pending_saves.add(run_id)
        if not self._save_timer.isActive():
            self._save_timer.start()

    def _flush_pending_saves(self):
        pending = list(self._pending_saves)
        self._pending_saves.clear()
        for run_id in pending:
            run = self._runs.get(run_id)
            if run is not None:
                self._save_run_thread_history(run)

    def _save_run_thread_history(self, run: ConversationRun):
        messages = prepare_for_storage(run.thread.history)
        if run.partial_text:
            messages.append(
                {
                    "role": "assistant",
                    "content": run.partial_text,
                    "created_at": datetime.now().isoformat(),
                    "synthetic": "canvas_run_partial",
                }
            )
        self._save_run_messages(run, messages)

    def _save_run_messages(self, run: ConversationRun, messages: list[dict]):
        run.data["messages"] = prepare_for_storage(messages)
        run.data["updated_at"] = datetime.now().isoformat()
        self._save_data(run.conv_id, run.data)

    def _save_data(self, conv_id: str, data: dict):
        self.store.save(conv_id, copy.deepcopy(data))
        self.conversation_updated.emit(conv_id)


def _history_ends_with_assistant_text(history: list[dict], text: str) -> bool:
    wanted = str(text or "").strip()
    if not wanted:
        return True
    for message in reversed(history):
        if not isinstance(message, dict):
            continue
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content.strip() == wanted
        if isinstance(content, list):
            parts = [
                str(block.get("text") or "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            return "".join(parts).strip() == wanted
        return False
    return False
