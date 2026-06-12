from contextlib import contextmanager
from types import SimpleNamespace

import ui.widgets.chat_panel as chat_panel_module
from ui.widgets.chat_panel import (
    ChatPanel,
    _ActiveConversationExportWorker,
    _ConversationRuntime,
    _ConversationLoadWorker,
    _ExtensionCommandWorker,
    _ExtensionReloadWorker,
    _ContextBudgetWorker,
    _MentionFilesWorker,
    _SkillPickerLoadWorker,
    _ConversationSaveWorker,
    _list_mention_files,
    _saved_tool_calls,
)


class _Signal:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class _Store:
    def __init__(self):
        self.saved = []

    def save(self, conv_id, data):
        self.saved.append((conv_id, data))


class _WorkerPool:
    def __init__(self):
        self.workers = []

    def start(self, worker):
        self.workers.append(worker)


class _LoadStore:
    def __init__(self, data=None):
        self.data = data or {
            "id": "chat-1",
            "title": "Loaded chat",
            "messages": [{"role": "user", "content": "hello"}],
        }
        self.loaded = []

    def load(self, path):
        self.loaded.append(path)
        return self.data


class _FailingStore:
    def save(self, _conv_id, _data):
        raise OSError("disk full")


class _Thread:
    def __init__(self, history):
        self.history = history
        self.last_usage = {}


class _Bubble:
    def __init__(self):
        self._history_index = -1
        self.usage = None
        self.finalized = ""

    def set_usage(self, usage):
        self.usage = usage

    def finalize(self, text, on_artifact=None):
        self.finalized = text


class _Layout:
    def indexOf(self, _widget):
        return 0


class _HistoryLayout:
    def __init__(self, widgets=()):
        self.widgets = list(widgets)
        self.inserted = []
        self.removed = []

    def count(self):
        return len(self.widgets) + 1

    def indexOf(self, widget):
        try:
            return self.widgets.index(widget)
        except ValueError:
            return -1

    def itemAt(self, index):
        return _LayoutItem(self.widgets[index])

    def insertWidget(self, index, widget, *args):
        index = min(index, len(self.widgets))
        self.widgets.insert(index, widget)
        self.inserted.append((index, widget, args))

    def takeAt(self, index):
        widget = self.widgets.pop(index)
        self.removed.append((index, widget))
        return _LayoutItem(widget)


class _MessageContainer:
    def __init__(self):
        self.enabled = True
        self.events = []

    def updatesEnabled(self):
        return self.enabled

    def setUpdatesEnabled(self, enabled):
        self.enabled = enabled
        self.events.append(("updates", enabled))

    def update(self):
        self.events.append(("update",))


class _DeletedWidget:
    def __init__(self):
        self.deleted = False

    def deleteLater(self):
        self.deleted = True


class _LayoutItem:
    def __init__(self, widget):
        self._widget = widget

    def widget(self):
        return self._widget


class _PrependLayout:
    def __init__(self, older_btn):
        self.older_btn = older_btn
        self.removed = []

    def indexOf(self, widget):
        return 0 if widget is self.older_btn else -1

    def takeAt(self, index):
        assert index == 0
        self.removed.append(index)
        return _LayoutItem(self.older_btn)


class _ScrollBar:
    def __init__(self):
        self._value = 25
        self._maximum = 100

    def maximum(self):
        return self._maximum

    def value(self):
        return self._value


class _Scroll:
    def __init__(self):
        self.bar = _ScrollBar()

    def verticalScrollBar(self):
        return self.bar


class _Timer:
    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


class _ContextRing:
    def __init__(self):
        self.budgets = []

    def set_budget(self, budget):
        self.budgets.append(budget)


class _SavePool:
    def __init__(self):
        self.workers = []

    def start(self, worker):
        self.workers.append(worker)


class _FakeMentionPool:
    def __init__(self):
        self.workers = []

    def start(self, worker):
        self.workers.append(worker)


class _FakeMentionPicker:
    created = []

    def __init__(self, files, crew=None, parent=None):
        self.files = list(files)
        self.crew = list(crew or [])
        self.parent = parent
        self.file_selected = _ConnectSignal()
        self.crew_selected = _ConnectSignal()
        self.filters = []
        self.hidden = 0
        self.shown = 0
        self.raised = 0
        self.widths = []
        self.moves = []
        _FakeMentionPicker.created.append(self)

    def set_files(self, files):
        self.files = list(files)

    def set_crew(self, crew):
        self.crew = list(crew or [])

    def filter(self, text):
        self.filters.append(text)

    def count(self):
        return len(self.files) + len(self.crew)

    def hide(self):
        self.hidden += 1

    def show(self):
        self.shown += 1

    def raise_(self):
        self.raised += 1

    def height(self):
        return 20

    def setFixedWidth(self, width):
        self.widths.append(width)

    def move(self, x, y):
        self.moves.append((x, y))


class _ConnectSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)


def test_on_done_preserves_thread_tool_history(qapp):
    thread_history = [
        {"role": "user", "content": "read missing"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tu_1"}]},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu_1",
                    "content": "[tool error] File does not exist: non_existent_file.txt",
                },
                {
                    "type": "text",
                    "text": "Continue the active user task. Use the tool results above as evidence.",
                    "synthetic": "active_task",
                    "internal": True,
                },
            ],
            "synthetic": "tool_results",
        },
        {
            "role": "user",
            "content": "Runtime guard detected the same tool failure twice.",
            "synthetic": "extension",
        },
        {
            "role": "user",
            "content": "Continue the active task from the compacted context.",
            "synthetic": "extension_resume",
        },
        {"role": "tool", "tool_call_id": "tu_2", "content": "hidden"},
        {"role": "assistant", "content": "Stopped."},
    ]
    expected_history = [
        {"role": "user", "content": "read missing"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tu_1"}]},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu_1",
                    "content": "[tool error] File does not exist: non_existent_file.txt",
                }
            ],
            "synthetic": "tool_results",
        },
        {"role": "tool", "tool_call_id": "tu_2", "content": "hidden"},
        {"role": "assistant", "content": "Stopped."},
    ]
    run = SimpleNamespace(
        run_id="r1",
        conv_id="c1",
        thread=_Thread(thread_history),
        history_snapshot=[{"role": "user", "content": "read missing"}],
        data_snapshot={"messages": []},
        bubble=_Bubble(),
        crew=None,
    )
    bubble = run.bubble
    runtime = _ConversationRuntime(run=run)
    panel = SimpleNamespace()
    panel.conv_id = "c1"
    panel.history = list(run.history_snapshot)
    panel.conv_data = {}
    panel.active_bubble = run.bubble
    panel.msg_layout = _Layout()
    panel._bubbles = {}
    panel._history_widgets = {}
    panel._render_end_index = len(panel.history)
    panel._runtimes = {"c1": runtime}
    panel.saved = _Signal()
    panel.model_combo = SimpleNamespace(currentText=lambda: "claude-sonnet-4-6")
    panel._find_run = lambda run_id: run if run_id == "r1" else None
    panel._runtime_for = lambda conv_id: runtime
    panel._flush_stream_buffer = lambda: None
    panel._sync_regenerate_flags = lambda: None
    panel._sync_visible_runtime_refs = lambda: None
    panel._exit_streaming = lambda: None
    panel._maybe_auto_title = lambda: None
    panel._context_budget = lambda: SimpleNamespace(used_tokens=0)
    panel.compact_conversation = lambda force=False: None
    panel._save = lambda touch_updated=False: None
    panel._start_next_queued = lambda: None
    panel._keep_thread_until_finished = lambda thread: None
    panel._add_bubble = lambda *args, **kwargs: _Bubble()
    panel._add_notice = lambda _text: None
    panel._wrap_artifact = lambda artifact: artifact
    panel._bottom = lambda: None
    panel._track_history_widget = lambda idx, widget: ChatPanel._track_history_widget(panel, idx, widget)

    ChatPanel._on_done(panel, "r1", "Stopped.")

    assert panel.history == expected_history
    assert panel.conv_data["messages"] == expected_history
    assert panel._bubbles[len(expected_history) - 1] is bubble


def test_insert_history_bubble_skips_runtime_messages(qapp):
    panel = SimpleNamespace()
    panel.history = [{"role": "user", "content": "hidden", "synthetic": "extension"}]
    panel._make_bubble = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("hidden"))

    assert ChatPanel._insert_history_bubble(panel, 0) is None


def test_insert_history_bubble_replays_openai_tool_call_notice(qapp):
    panel = SimpleNamespace()
    notices = []
    notice_widget = _DeletedWidget()
    panel.history = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "read_file", "arguments": '{"path": "src/main.py"}'},
                }
            ],
        }
    ]
    panel.cwd = "C:\\repo"
    panel._history_widgets = {}
    panel._make_bubble = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("hidden"))
    panel._track_history_widget = lambda idx, widget: ChatPanel._track_history_widget(panel, idx, widget)
    panel._insert_tool_notice = lambda text, debug_text="", **kwargs: (
        notices.append((text, debug_text, kwargs)) or notice_widget
    )

    assert ChatPanel._insert_history_bubble(panel, 0) is None
    assert notices[0][0].replace("\\", "/") == "Reading file 'src/main.py'"
    assert "Tool: read_file" in notices[0][1]
    assert notices[0][2] == {"at_top": False}
    assert panel._history_widgets == {0: [notice_widget]}


def test_render_history_tail_batches_layout_updates(qapp, monkeypatch):
    container = _MessageContainer()
    pins = []
    depths = []
    operations = []

    @contextmanager
    def fake_time_operation(operation, *, detail="", slow_ms=100.0):
        operations.append((operation, detail))
        yield

    monkeypatch.setattr(chat_panel_module, "time_operation", fake_time_operation)
    panel = SimpleNamespace()
    panel.history = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
    ]
    panel.msg_container = container
    panel._message_layout_batch_depth = 0
    panel._auto_scroll = True
    panel._pin_to_bottom = lambda: pins.append("pin")
    panel._batch_message_layout = lambda: ChatPanel._batch_message_layout(panel)
    panel._clear_bubbles = lambda: None
    panel._sync_history_paging_buttons = lambda: depths.append(
        ("paging", panel._message_layout_batch_depth)
    )
    panel._insert_history_bubble = lambda idx: (
        depths.append(("insert", idx, panel._message_layout_batch_depth)),
        ChatPanel._bottom(panel),
    )
    panel._sync_regenerate_flags = lambda: depths.append(("regen", panel._message_layout_batch_depth))

    ChatPanel._render_history_tail(panel)

    assert pins == []
    assert container.events == [("updates", False), ("updates", True), ("update",)]
    assert panel._message_layout_batch_depth == 0
    assert all(depth[-1] == 1 for depth in depths)
    assert operations == [("chat.render.tail", "messages=3")]


def test_render_history_tail_progressively_prepends_initial_window(qapp, monkeypatch):
    monkeypatch.setattr("ui.widgets.chat_panel._INITIAL_RENDER_SYNC_MESSAGES", 3)
    monkeypatch.setattr("ui.widgets.chat_panel._HISTORY_RENDER_BATCH_MESSAGES", 2)
    container = _MessageContainer()
    timer = _Timer()
    inserted = []
    syncs = []
    panel = SimpleNamespace()
    panel.history = [{"role": "user", "content": f"message {i}"} for i in range(8)]
    panel.msg_container = container
    panel._message_layout_batch_depth = 0
    panel._pending_history_render_target = None
    panel._pending_history_render_next = -1
    panel._history_render_timer = timer
    panel._clear_bubbles = lambda: None
    panel._batch_message_layout = lambda: ChatPanel._batch_message_layout(panel)
    panel._insert_history_bubble = lambda idx, at_top=False: inserted.append(
        (idx, at_top, panel._message_layout_batch_depth)
    )
    panel._sync_regenerate_flags = lambda: None
    panel._trim_rendered_history_from_bottom = lambda: None
    panel._sync_history_paging_buttons = lambda: syncs.append(panel._render_start_index)
    panel._finish_pending_history_render = lambda: ChatPanel._finish_pending_history_render(panel)

    ChatPanel._render_history_tail(panel)

    assert inserted == [(5, False, 1), (6, False, 1), (7, False, 1)]
    assert panel._render_start_index == 5
    assert panel._pending_history_render_target == 0
    assert panel._pending_history_render_next == 4
    assert timer.started == 1
    assert syncs == []

    ChatPanel._render_pending_history_batch(panel)

    assert inserted[-2:] == [(4, True, 1), (3, True, 1)]
    assert panel._render_start_index == 3
    assert panel._pending_history_render_next == 2
    assert timer.started == 2
    assert syncs == []

    ChatPanel._render_pending_history_batch(panel)
    ChatPanel._render_pending_history_batch(panel)

    assert inserted == [
        (5, False, 1),
        (6, False, 1),
        (7, False, 1),
        (4, True, 1),
        (3, True, 1),
        (2, True, 1),
        (1, True, 1),
        (0, True, 1),
    ]
    assert panel._render_start_index == 0
    assert panel._pending_history_render_target is None
    assert panel._pending_history_render_next == -1
    assert syncs == [0]


def test_prepend_history_page_waits_for_pending_initial_render(qapp):
    panel = SimpleNamespace(
        _pending_history_render_target=0,
        _render_start_index=10,
        _sync_older_button=lambda: (_ for _ in ()).throw(AssertionError("pending")),
    )

    assert ChatPanel._prepend_history_page(panel) is None


def test_prepend_history_page_batches_inserts_and_preserves_restore(qapp):
    container = _MessageContainer()
    older_btn = _DeletedWidget()
    layout = _PrependLayout(older_btn)
    pins = []
    inserted = []
    sync_depths = []
    panel = SimpleNamespace()
    panel.history = [{"role": "user", "content": f"message {i}"} for i in range(6)]
    panel.msg_container = container
    panel.msg_layout = layout
    panel.scroll = _Scroll()
    panel._message_layout_batch_depth = 0
    panel._render_start_index = 5
    panel._render_end_index = len(panel.history)
    panel._older_btn = older_btn
    panel._newer_btn = None
    panel._pending_history_render_target = None
    panel._auto_scroll = True
    panel._pin_to_bottom = lambda: pins.append("pin")
    panel._batch_message_layout = lambda: ChatPanel._batch_message_layout(panel)
    panel._insert_history_bubble = lambda idx, at_top=False: (
        inserted.append((idx, at_top, panel._message_layout_batch_depth)),
        ChatPanel._bottom(panel),
    )
    panel._remove_layout_widget = lambda widget: ChatPanel._remove_layout_widget(panel, widget)
    panel._remove_paging_button = lambda attr: ChatPanel._remove_paging_button(panel, attr)
    panel._trim_rendered_history_from_bottom = lambda: None
    panel._sync_history_paging_buttons = lambda: sync_depths.append(
        panel._message_layout_batch_depth
    )
    panel._prepend_restore_timer = _Timer()

    ChatPanel._prepend_history_page(panel)

    assert pins == []
    assert layout.removed == [0]
    assert older_btn.deleted is True
    assert inserted == [
        (4, True, 1),
        (3, True, 1),
        (2, True, 1),
        (1, True, 1),
        (0, True, 1),
    ]
    assert sync_depths == [1]
    assert panel._render_start_index == 0
    assert panel._prepend_restore == (25, 100)
    assert panel._prepend_restore_timer.started == 1
    assert container.events == [("updates", False), ("updates", True), ("update",)]


def test_remove_history_index_widgets_removes_tracked_artifacts_and_bubble(qapp):
    artifact = _DeletedWidget()
    bubble = _DeletedWidget()
    other = _DeletedWidget()
    layout = _HistoryLayout([artifact, bubble, other])
    panel = SimpleNamespace(
        msg_layout=layout,
        _history_widgets={2: [artifact]},
        _bubbles={2: bubble},
    )
    panel._remove_layout_widget = lambda widget: ChatPanel._remove_layout_widget(panel, widget)

    ChatPanel._remove_history_index_widgets(panel, 2)

    assert layout.widgets == [other]
    assert artifact.deleted is True
    assert bubble.deleted is True
    assert panel._history_widgets == {}
    assert panel._bubbles == {}


def test_trim_rendered_history_from_bottom_keeps_bounded_window(qapp, monkeypatch):
    monkeypatch.setattr("ui.widgets.chat_panel._MAX_RENDERED_HISTORY_MESSAGES", 3)
    widgets = {idx: _DeletedWidget() for idx in range(2, 7)}
    layout = _HistoryLayout(widgets.values())
    panel = SimpleNamespace(
        msg_layout=layout,
        _render_start_index=2,
        _render_end_index=7,
        _history_widgets={idx: [widget] for idx, widget in widgets.items()},
        _bubbles={},
    )
    panel._remove_layout_widget = lambda widget: ChatPanel._remove_layout_widget(panel, widget)
    panel._remove_history_index_widgets = (
        lambda idx: ChatPanel._remove_history_index_widgets(panel, idx)
    )

    ChatPanel._trim_rendered_history_from_bottom(panel)

    assert panel._render_end_index == 5
    assert layout.widgets == [widgets[2], widgets[3], widgets[4]]
    assert widgets[5].deleted is True
    assert widgets[6].deleted is True


def test_append_history_page_loads_newer_and_trims_older_head(qapp, monkeypatch):
    monkeypatch.setattr("ui.widgets.chat_panel._MAX_RENDERED_HISTORY_MESSAGES", 4)
    monkeypatch.setattr("ui.widgets.chat_panel._NEWER_RENDER_MESSAGES", 2)
    monkeypatch.setattr("ui.widgets.chat_panel._NEWER_RENDER_BYTES", 10_000)
    existing = {idx: _DeletedWidget() for idx in range(4)}
    newer_btn = _DeletedWidget()
    layout = _HistoryLayout([existing[0], existing[1], existing[2], existing[3], newer_btn])
    inserted = {}
    syncs = []
    panel = SimpleNamespace(
        history=[{"role": "user", "content": f"message {idx}"} for idx in range(8)],
        msg_container=_MessageContainer(),
        msg_layout=layout,
        _message_layout_batch_depth=0,
        _render_start_index=0,
        _render_end_index=4,
        _history_widgets={idx: [widget] for idx, widget in existing.items()},
        _bubbles={},
        _newer_btn=newer_btn,
        _older_btn=None,
    )
    panel._batch_message_layout = lambda: ChatPanel._batch_message_layout(panel)
    panel._remove_layout_widget = lambda widget: ChatPanel._remove_layout_widget(panel, widget)
    panel._remove_paging_button = lambda attr: ChatPanel._remove_paging_button(panel, attr)
    panel._track_history_widget = lambda idx, widget: ChatPanel._track_history_widget(panel, idx, widget)
    panel._remove_history_index_widgets = (
        lambda idx: ChatPanel._remove_history_index_widgets(panel, idx)
    )
    panel._trim_rendered_history_from_top = lambda: ChatPanel._trim_rendered_history_from_top(panel)
    panel._sync_regenerate_flags = lambda: None
    panel._sync_history_paging_buttons = lambda: syncs.append(panel._message_layout_batch_depth)

    def insert(idx, at_top=False):
        assert at_top is False
        widget = _DeletedWidget()
        inserted[idx] = widget
        panel.msg_layout.insertWidget(panel.msg_layout.count() - 1, widget)
        ChatPanel._track_history_widget(panel, idx, widget)
        return widget

    panel._insert_history_bubble = insert

    ChatPanel._append_history_page(panel)

    assert sorted(inserted) == [4, 5]
    assert panel._render_start_index == 2
    assert panel._render_end_index == 6
    assert panel._newer_btn is None
    assert newer_btn.deleted is True
    assert existing[0].deleted is True
    assert existing[1].deleted is True
    assert layout.widgets == [existing[2], existing[3], inserted[4], inserted[5]]
    assert syncs == [1]


def test_sync_newer_button_tracks_hidden_tail_and_removes_at_end(qapp):
    layout = _HistoryLayout()
    panel = SimpleNamespace(
        history=[{"role": "user", "content": str(idx)} for idx in range(5)],
        msg_layout=layout,
        _render_end_index=3,
        _newer_btn=None,
        _append_history_page=lambda: None,
        _older_button_style=lambda: "",
    )
    panel._remove_layout_widget = lambda widget: ChatPanel._remove_layout_widget(panel, widget)
    panel._remove_paging_button = lambda attr: ChatPanel._remove_paging_button(panel, attr)

    ChatPanel._sync_newer_button(panel)

    button = panel._newer_btn
    assert button is not None
    assert button.text() == "Load newer messages (2 hidden)"
    assert layout.widgets == [button]

    panel._render_end_index = len(panel.history)

    ChatPanel._sync_newer_button(panel)

    assert panel._newer_btn is None
    assert layout.widgets == []


def test_ensure_tail_rendered_for_append_rerenders_when_window_is_not_at_tail(qapp):
    renders = []
    panel = SimpleNamespace(
        _render_end_index=4,
        _render_history_tail=lambda: renders.append("tail"),
    )

    assert ChatPanel._ensure_tail_rendered_for_append(panel, 5) is True
    assert renders == ["tail"]

    panel._render_end_index = 5

    assert ChatPanel._ensure_tail_rendered_for_append(panel, 5) is False
    assert renders == ["tail"]


def test_chat_panel_save_queues_snapshot_without_sync_store_write(qapp):
    store = _Store()
    pool = _SavePool()
    panel = SimpleNamespace()
    panel.conv_id = "chat-1"
    panel.conv_data = {"id": "chat-1", "title": "Chat"}
    panel.history = [{"role": "user", "content": "draft"}]
    panel.store = store
    panel.saved = _Signal()
    panel._conversation_save_pool = pool
    panel._queue_conversation_save = lambda conv_id, data: ChatPanel._queue_conversation_save(panel, conv_id, data)
    panel._on_conversation_save_done = lambda conv_id, ok, error: None
    panel._update_context_ui = lambda: None

    ChatPanel._save(panel, touch_updated=True)
    panel.history.append({"role": "assistant", "content": "later"})

    assert store.saved == []
    assert panel.saved.calls == []
    assert len(pool.workers) == 1
    worker = pool.workers[0]
    assert worker._conv_id == "chat-1"
    assert worker._data["messages"] == [{"role": "user", "content": "draft"}]
    assert panel.conv_data["updated_at"]


def test_direct_save_paths_queue_conversation_snapshots(qapp):
    queued = []
    created = _Signal()
    changed = _Signal()
    header_syncs = []
    panel = SimpleNamespace()
    panel.conv_id = None
    panel.cwd = "C:\\repo"
    panel.conversation_created = created
    panel.conversation_changed = changed
    panel._runtime_for = lambda conv_id: conv_id
    panel._queue_conversation_save = lambda conv_id, data: queued.append((conv_id, data.copy()))
    panel._sync_header_title = lambda: header_syncs.append("header")

    ChatPanel._ensure_conversation(panel, "hello from the user", "claude-test")

    conv_id = panel.conv_id
    assert queued == [(conv_id, panel.conv_data.copy())]
    assert created.calls == [(conv_id,)]
    assert changed.calls == [(conv_id,)]
    assert header_syncs == ["header"]

    queued.clear()
    panel.history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
    ]
    panel.conv_data = {"title": "Main"}
    panel.model_combo = SimpleNamespace(currentText=lambda: "claude-test")

    ChatPanel._branch(panel, 0)

    assert len(queued) == 1
    branch_id, branch_data = queued[0]
    assert branch_id == branch_data["id"]
    assert branch_data["title"] == "Main (branch)"
    assert branch_data["messages"] == [{"role": "user", "content": "first"}]


def test_auto_title_done_queues_save(qapp):
    queued = []
    kept_threads = []
    header_syncs = []
    thread = object()
    panel = SimpleNamespace(
        conv_id="chat-1",
        conv_data={"id": "chat-1", "title_auto": True, "title": "Old"},
        title_thread=thread,
        _queue_conversation_save=lambda conv_id, data: queued.append((conv_id, data.copy())),
        _sync_header_title=lambda: header_syncs.append("header"),
        _keep_thread_until_finished=lambda completed: kept_threads.append(completed),
    )

    ChatPanel._on_auto_title_done(panel, "chat-1", "New title")

    assert panel.title_thread is None
    assert queued == [("chat-1", {"id": "chat-1", "title_auto": False, "title": "New title"})]
    assert header_syncs == ["header"]
    assert kept_threads == [thread]


def test_conversation_save_worker_emits_success(qapp):
    store = _Store()
    worker = _ConversationSaveWorker(store, "chat-1", {"title": "Saved"})
    done = []
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert store.saved == [("chat-1", {"title": "Saved"})]
    assert done == [("chat-1", True, "")]


def test_conversation_save_worker_emits_failure(qapp):
    worker = _ConversationSaveWorker(_FailingStore(), "chat-1", {"title": "Saved"})
    done = []
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert done == [("chat-1", False, "disk full")]


def test_conversation_load_worker_emits_loaded_data(qapp):
    store = _LoadStore({"id": "chat-2", "messages": []})
    worker = _ConversationLoadWorker(store, 7, "chat-2.json")
    done = []
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert store.loaded == ["chat-2.json"]
    assert done == [(7, "chat-2.json", {"id": "chat-2", "messages": []}, "")]


def test_active_conversation_export_worker_emits_written_path(qapp, monkeypatch):
    done = []

    monkeypatch.setattr(
        "ui.widgets.chat_panel.write_conversation_markdown",
        lambda data, out_path: f"{data['title']}->{out_path}",
    )
    worker = _ActiveConversationExportWorker(5, {"title": "Chat", "messages": []}, "out.md")
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert done == [(5, "Chat->out.md", "")]


def test_active_export_starts_worker_without_writing_on_ui_thread(qapp, monkeypatch, tmp_path):
    pool = _SavePool()
    dialogs = []
    saved = []
    panel = SimpleNamespace(
        history=[{"role": "user", "content": "hello"}],
        conv_id="chat-1",
        conv_data={"id": "chat-1", "title": "Export Active"},
        _active_export_running=False,
        _active_export_generation=0,
        _active_export_pool=pool,
        _save=lambda: saved.append("save"),
        window=lambda: None,
    )
    panel._on_active_export_done = lambda *args: ChatPanel._on_active_export_done(panel, *args)

    monkeypatch.setattr(
        "ui.widgets.chat_panel.QFileDialog.getSaveFileName",
        lambda *args: dialogs.append(args) or (str(tmp_path / "active.md"), ""),
    )
    monkeypatch.setattr(
        "ui.widgets.chat_panel.write_conversation_markdown",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should run in worker")),
    )

    ChatPanel.export_conversation(panel)

    assert saved == ["save"]
    assert panel._active_export_running
    assert dialogs[0][2] == "Export-Active.md"
    assert len(pool.workers) == 1
    assert isinstance(pool.workers[0], _ActiveConversationExportWorker)
    assert pool.workers[0]._data["messages"] == [{"role": "user", "content": "hello"}]


def test_active_export_done_ignores_stale_and_reports_error(qapp, monkeypatch):
    notices = []
    warnings = []
    panel = SimpleNamespace(
        _active_export_generation=3,
        _active_export_running=True,
        _add_notice=lambda text: notices.append(text),
    )
    monkeypatch.setattr(
        "ui.widgets.chat_panel.QMessageBox.warning",
        lambda *args: warnings.append(args),
    )

    ChatPanel._on_active_export_done(panel, 2, "", "stale")

    assert panel._active_export_running
    assert notices == []

    ChatPanel._on_active_export_done(panel, 3, "", "disk full")

    assert not panel._active_export_running
    assert notices == ["Conversation export failed: disk full"]
    assert warnings and warnings[0][1] == "Export failed"


def test_mention_files_worker_emits_loaded_files(qapp, tmp_path, monkeypatch):
    import ui.widgets.chat_panel as chat_panel

    files = [("src/main.py", str(tmp_path / "src" / "main.py"))]
    monkeypatch.setattr(chat_panel, "_list_mention_files", lambda cwd, limit=800: files)
    worker = _MentionFilesWorker(4, str(tmp_path), limit=12)
    done = []
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert done == [(4, str(tmp_path), files)]


def test_extension_command_worker_emits_result_and_runtime_directives(qapp, monkeypatch):
    calls = []
    approvals = []

    def fake_run(cwd, name, args, *, model, history, conversation_id, runtime):
        calls.append((cwd, name, args, model, history, conversation_id))
        approvals.append(runtime.bind_extension("demo_ext").processes._approve_start("request"))
        runtime.notice("note")
        runtime.send("send now")
        runtime.enqueue("send later")
        runtime.compact(force=False)
        runtime.continue_after_compact("resume", force=True)
        return {"notice": "done"}, []

    monkeypatch.setattr("ui.widgets.chat_panel.run_extension_command", fake_run)
    worker = _ExtensionCommandWorker(
        "repo",
        "demo",
        "args",
        model="claude-test",
        history=[{"role": "user", "content": "hi"}],
        conversation_id="c1",
        approve_start=lambda request: request == "request",
    )
    done = []
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert calls == [
        (
            "repo",
            "demo",
            "args",
            "claude-test",
            [{"role": "user", "content": "hi"}],
            "c1",
        )
    ]
    assert approvals == [True]
    assert done[0][:4] == ("repo", "c1", "demo", {"notice": "done"})
    assert done[0][4]["errors"] == []
    assert done[0][4]["directives"] == [
        ("notice", "note"),
        ("send", "send now"),
        ("enqueue", "send later"),
        ("compact", False),
        ("continue_after_compact", ("resume", True)),
    ]


def test_run_extension_command_queues_worker_without_executing_inline(qapp, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "ui.widgets.chat_panel.run_extension_command",
        lambda *args, **kwargs: calls.append((args, kwargs)) or ("done", []),
    )
    pool = _WorkerPool()
    panel = SimpleNamespace(
        cwd="repo",
        model_combo=SimpleNamespace(currentText=lambda: "claude-test"),
        history=[{"role": "user", "content": "hi"}],
        conv_id="c1",
        _extension_command_pool=pool,
        _extension_command_approval=SimpleNamespace(request_start=lambda _request: True),
        _on_extension_command_done=lambda *_args: None,
    )

    ChatPanel._run_extension_command(panel, "demo", "args")

    assert calls == []
    assert len(pool.workers) == 1
    assert isinstance(pool.workers[0], _ExtensionCommandWorker)


def test_extension_process_approval_request_uses_existing_prompt(qapp, monkeypatch):
    prompts = []
    done = SimpleNamespace(set=lambda: prompts.append("done"))
    result = {}
    panel = SimpleNamespace(window=lambda: "window")
    monkeypatch.setattr(
        "ui.widgets.chat_panel.confirm_process_start",
        lambda parent, request: prompts.append((parent, request)) or True,
    )

    ChatPanel._on_extension_process_approval_requested(panel, "request", (done, result))

    assert prompts == [("window", "request"), "done"]
    assert result == {"approved": True}


def test_extension_reload_worker_emits_errors(qapp, monkeypatch):
    done = []
    monkeypatch.setattr(
        "ui.widgets.chat_panel.extension_errors",
        lambda cwd: [f"{cwd}: bad"],
    )
    worker = _ExtensionReloadWorker(7, "repo")
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert done == [(7, "repo", ["repo: bad"])]


def test_builtin_reload_queues_extension_error_scan(qapp, monkeypatch):
    pool = _WorkerPool()
    notices = []
    calls = []
    panel = SimpleNamespace(
        cwd="repo",
        _skill_picker="picker",
        _file_picker="files",
        _extension_reload_generation=0,
        _extension_reload_pool=pool,
        _invalidate_mention_files=lambda: calls.append("mentions"),
        _update_context_ui=lambda: calls.append("context"),
        _refresh_extension_ui=lambda: calls.append("extensions"),
        _start_extension_reload_check=lambda: ChatPanel._start_extension_reload_check(panel),
        _on_extension_reload_done=lambda *_args: None,
        _add_notice=lambda text: notices.append(text),
    )
    monkeypatch.setattr(
        "ui.widgets.chat_panel.extension_errors",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should run in worker")),
    )

    ChatPanel._run_builtin_command(panel, "reload")

    assert panel._skill_picker is None
    assert panel._file_picker is None
    assert calls == ["mentions", "context", "extensions"]
    assert notices == []
    assert isinstance(pool.workers[0], _ExtensionReloadWorker)


def test_extension_reload_done_ignores_stale_and_reports_current(qapp):
    notices = []
    panel = SimpleNamespace(
        cwd="repo",
        _extension_reload_generation=3,
        _add_notice=lambda text: notices.append(text),
    )

    ChatPanel._on_extension_reload_done(panel, 2, "repo", ["old"])
    ChatPanel._on_extension_reload_done(panel, 3, "other", ["wrong cwd"])
    ChatPanel._on_extension_reload_done(panel, 3, "repo", ["bad"])
    ChatPanel._on_extension_reload_done(panel, 3, "repo", [])

    assert notices == [
        "Reloaded with 1 extension error(s). Check the extension file.",
        "Reloaded skills and extensions.",
    ]


def test_skill_picker_load_worker_emits_skills_and_commands(qapp, monkeypatch):
    from services.skills import Skill
    from services.slash_commands import SlashCommand

    done = []
    skill = Skill("review", "Review code", "prompt")
    command = SlashCommand("compact", "Compact")
    monkeypatch.setattr("ui.widgets.chat_panel.load_skills", lambda cwd: [skill])
    monkeypatch.setattr("ui.widgets.chat_panel.load_all_commands", lambda cwd: [command])
    worker = _SkillPickerLoadWorker(8, "repo")
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert done == [(8, "repo", [skill], [command], "")]


def test_context_budget_worker_loads_settings_and_emits_budget(qapp, monkeypatch):
    done = []
    budget = SimpleNamespace(used_tokens=42)
    settings = SimpleNamespace(load=lambda: {"system_prompt": "Custom"})
    calls = []
    monkeypatch.setattr(
        "ui.widgets.chat_panel.analyze_context",
        lambda model, cwd, history, custom_system="", active_skill=None: (
            calls.append((model, cwd, history, custom_system, active_skill)) or budget
        ),
    )
    skill = SimpleNamespace(name="review", prompt="Review")
    worker = _ContextBudgetWorker(
        9,
        "repo",
        "claude-sonnet-4-6",
        [{"role": "user", "content": "hi"}],
        settings,
        skill,
    )
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert done == [(9, "repo", "claude-sonnet-4-6", budget, "")]
    assert calls == [(
        "claude-sonnet-4-6",
        "repo",
        [{"role": "user", "content": "hi"}],
        "Custom",
        skill,
    )]


def test_slash_changed_queues_picker_load_without_sync_extension_scan(qapp, monkeypatch):
    calls = []

    class FakePicker:
        def __init__(self, skills, commands, *, include_terminal=False, parent=None):
            self.skills = skills
            self.commands = commands
            self.include_terminal = include_terminal
            self.parent = parent
            self.skill_selected = SimpleNamespace(connect=lambda _cb: None)
            self.command_selected = SimpleNamespace(connect=lambda _cb: None)
            self.terminal_selected = SimpleNamespace(connect=lambda _cb: None)
            self.filtered = []
            self.hidden = False

        def filter(self, text):
            self.filtered.append(text)

        def count(self):
            return 0

        def hide(self):
            self.hidden = True

    monkeypatch.setattr("ui.widgets.chat_panel.SkillPicker", FakePicker)
    monkeypatch.setattr(
        "ui.widgets.chat_panel.load_skills",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should load in worker")),
    )
    monkeypatch.setattr(
        "ui.widgets.chat_panel.load_all_commands",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should load in worker")),
    )
    pool = _WorkerPool()
    panel = SimpleNamespace(
        cwd="repo",
        _skill_picker=None,
        _skill_picker_loading=False,
        _skill_picker_generation=0,
        _skill_picker_pool=pool,
        _skill_picker_query="",
        _on_skill_selected=lambda *_args: None,
        _on_command_selected=lambda *_args: None,
        _on_terminal_hint_selected=lambda *_args: None,
        _ensure_skill_picker=lambda query: ChatPanel._ensure_skill_picker(panel, query),
        _start_skill_picker_load=lambda: ChatPanel._start_skill_picker_load(panel),
        _on_skill_picker_loaded=lambda *_args: None,
        _position_skill_picker=lambda: calls.append("position"),
    )

    ChatPanel._on_slash_changed(panel, "/rev")

    assert isinstance(panel._skill_picker, FakePicker)
    assert panel._skill_picker.filtered == ["/rev"]
    assert panel._skill_picker.hidden
    assert isinstance(pool.workers[0], _SkillPickerLoadWorker)
    assert calls == []


def test_skill_picker_loaded_ignores_stale_and_applies_current(qapp):
    calls = []

    class FakePicker:
        def __init__(self):
            self.items = None
            self.hidden = False
            self.shown = False

        def set_items(self, skills, commands, query=""):
            self.items = (skills, commands, query)

        def count(self):
            return 1

        def hide(self):
            self.hidden = True

        def show(self):
            self.shown = True

        def raise_(self):
            calls.append("raise")

    picker = FakePicker()
    panel = SimpleNamespace(
        cwd="repo",
        _skill_picker=picker,
        _skill_picker_generation=2,
        _skill_picker_loading=True,
        _skill_picker_query="/rev",
        _slash_commands=[],
        _slash_commands_cwd="",
        _add_notice=lambda text: calls.append(("notice", text)),
        _position_skill_picker=lambda: calls.append("position"),
    )

    ChatPanel._on_skill_picker_loaded(panel, 1, "repo", ["old"], [], "")
    ChatPanel._on_skill_picker_loaded(panel, 2, "other", ["wrong"], [], "")

    assert picker.items is None
    assert panel._skill_picker_loading

    ChatPanel._on_skill_picker_loaded(panel, 2, "repo", ["skill"], ["cmd"], "")

    assert not panel._skill_picker_loading
    assert picker.items == (["skill"], ["cmd"], "/rev")
    assert panel._slash_commands == ["cmd"]
    assert panel._slash_commands_cwd == "repo"
    assert calls == ["position", "raise"]
    assert picker.shown


def test_start_assistant_run_defers_system_build_until_chat_thread_runs(qapp, monkeypatch):
    builds = []
    started = []

    class FakeThread:
        def __init__(self, model, history, system, cwd, **kwargs):
            self.model = model
            self.history = history
            self.system = system
            self.cwd = cwd
            self.kwargs = kwargs
            self.chunk = _ConnectSignal()
            self.tool_called = _ConnectSignal()
            self.bash_line = _ConnectSignal()
            self.tool_result = _ConnectSignal()
            self.crew_started = _ConnectSignal()
            self.crew_chunk = _ConnectSignal()
            self.crew_done = _ConnectSignal()
            self.crew_error = _ConnectSignal()
            self.runtime_event = _ConnectSignal()
            self.done = _ConnectSignal()
            self.error = _ConnectSignal()

        def start(self):
            started.append(self)

    monkeypatch.setattr("ui.widgets.chat_panel.ChatThread", FakeThread)
    monkeypatch.setattr(
        "ui.widgets.chat_panel.build_system",
        lambda cwd, base=None: builds.append((cwd, base)) or "built system",
    )
    runtime = SimpleNamespace(tool_policy=object(), run=None)
    panel = SimpleNamespace(
        cwd="repo",
        _settings=SimpleNamespace(load=lambda: {"system_prompt": "Custom base"}),
        _runtime_for=lambda _conv_id: runtime,
        _approval_bus=object(),
        _configured_providers=lambda: ["anthropic"],
    )

    ChatPanel._start_assistant_run(
        panel,
        "c1",
        "claude-sonnet-4-6",
        [{"role": "user", "content": "hi"}],
        {"id": "c1"},
        visible=False,
        deferred_file_refs=["src/main.py"],
        deferred_file_target=0,
    )

    assert builds == []
    assert len(started) == 1
    assert callable(started[0].system)
    assert started[0].system() == "built system"
    assert builds == [("repo", "Custom base")]
    assert started[0].kwargs["deferred_file_refs"] == ["src/main.py"]
    assert started[0].kwargs["deferred_file_target"] == 0
    assert runtime.run.thread is started[0]


def test_extension_command_done_applies_directives_before_result_notice(qapp):
    notices = []
    sent = []
    compacted = []
    resumed = []
    panel = SimpleNamespace(
        cwd="repo",
        conv_id="c1",
        _add_notice=lambda text: notices.append(text),
        _send_or_queue_text=lambda text, *, prefer_queue: sent.append((text, prefer_queue)),
        compact_conversation=lambda force: compacted.append(force),
        _compact_and_resume_from_command=lambda prompt, force: resumed.append((prompt, force)),
    )
    panel._apply_extension_command_directive = (
        lambda action, value: ChatPanel._apply_extension_command_directive(panel, action, value)
    )

    ChatPanel._on_extension_command_done(
        panel,
        "repo",
        "c1",
        "demo",
        {"notice": "finished"},
        {
            "errors": [],
            "directives": [
                ("notice", "runtime note"),
                ("send", "now"),
                ("enqueue", "later"),
                ("compact", True),
                ("continue_after_compact", ("resume", False)),
            ],
        },
    )

    assert notices == ["runtime note", "finished"]
    assert sent == [("now", False), ("later", True)]
    assert compacted == [True]
    assert resumed == [("resume", False)]


def test_list_mention_files_delegates_to_workspace_file_service(tmp_path, monkeypatch):
    import ui.widgets.chat_panel as chat_panel

    root = tmp_path / "repo"
    nested = root / "src" / "main.py"
    outside = tmp_path / "outside.py"
    calls = []

    def fake_list_workspace_files(workspace_root, *, limit):
        calls.append((workspace_root, limit))
        return [str(nested), str(outside)]

    monkeypatch.setattr(chat_panel, "list_workspace_files", fake_list_workspace_files)

    assert _list_mention_files(str(root), limit=17) == [("src/main.py", str(nested))]
    assert calls == [(root.resolve(), 17)]


def test_file_mention_picker_loads_once_and_reuses_latest_query(qapp, tmp_path, monkeypatch):
    import ui.widgets.chat_panel as chat_panel

    _FakeMentionPicker.created = []
    pool = _FakeMentionPool()
    positions = []
    panel = SimpleNamespace(
        cwd=str(tmp_path),
        _file_picker=None,
        _settings=SimpleNamespace(load=lambda: {}),
        _mention_files_pool=pool,
        _mention_files_generation=0,
        _mention_files_loading=False,
        _mention_files_cwd="",
        _mention_files=[],
        _file_mention_text="",
        _position_file_picker=lambda: positions.append("position"),
        _on_file_mention_selected=lambda *_args: None,
        _on_crew_mention_selected=lambda *_args: None,
    )
    panel._ensure_mention_files_loading = lambda: ChatPanel._ensure_mention_files_loading(panel)
    panel._mention_file_candidates = lambda: ChatPanel._mention_file_candidates(panel)
    panel._on_mention_files_ready = lambda *args: ChatPanel._on_mention_files_ready(panel, *args)

    monkeypatch.setattr(chat_panel, "FileMentionPicker", _FakeMentionPicker)
    monkeypatch.setattr(chat_panel, "_enabled_crew", lambda _settings=None: [])
    monkeypatch.setattr(
        chat_panel,
        "_list_mention_files",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("sync scan")),
    )

    ChatPanel._on_file_mention_changed(panel, "s")
    ChatPanel._on_file_mention_changed(panel, "sr")

    assert len(pool.workers) == 1
    picker = _FakeMentionPicker.created[0]
    assert picker.files == []
    assert picker.filters == ["s", "sr"]
    assert picker.hidden == 2

    files = [("src/main.py", str(tmp_path / "src" / "main.py"))]
    ChatPanel._on_mention_files_ready(
        panel,
        panel._mention_files_generation,
        str(tmp_path),
        files,
    )

    assert picker.files == files
    assert picker.filters[-1] == "sr"
    assert picker.shown == 1
    assert picker.raised == 1
    assert positions == ["position"]


def test_load_conversation_queues_body_read_without_sync_load(qapp):
    pool = _SavePool()
    store = _LoadStore()
    notices = []
    applied = []
    panel = SimpleNamespace(
        store=store,
        conv_id="old",
        conv_data={"id": "old"},
        _current_conversation_path="old.json",
        _pending_conversation_load_path=None,
        _conversation_load_generation=0,
        _conversation_load_pool=pool,
        _flush_stream_buffer=lambda: None,
        _detach_visible_run_ui=lambda: None,
        _save=lambda: None,
        _reset_view=lambda: setattr(panel, "_conversation_load_generation", panel._conversation_load_generation + 1),
        _add_notice=lambda text: notices.append(text),
        _on_conversation_load_done=lambda *args: ChatPanel._on_conversation_load_done(panel, *args),
        _apply_loaded_conversation=lambda path, data: applied.append((path, data)),
    )

    ChatPanel.load_conversation(panel, "chat-1.json")

    assert store.loaded == []
    assert notices == ["Loading conversation..."]
    assert panel._pending_conversation_load_path == "chat-1.json"
    assert len(pool.workers) == 1

    pool.workers[0].run()

    assert store.loaded == ["chat-1.json"]
    assert applied == [("chat-1.json", store.data)]


def test_conversation_load_done_ignores_stale_result(qapp):
    notices = []
    applied = []
    panel = SimpleNamespace(
        _conversation_load_generation=3,
        _pending_conversation_load_path="new.json",
        _add_notice=lambda text: notices.append(text),
        _apply_loaded_conversation=lambda path, data: applied.append((path, data)),
    )

    ChatPanel._on_conversation_load_done(panel, 2, "old.json", {"id": "old"}, "")
    ChatPanel._on_conversation_load_done(panel, 3, "old.json", {"id": "old"}, "")

    assert notices == []
    assert applied == []
    assert panel._pending_conversation_load_path == "new.json"


def test_conversation_save_done_emits_saved_or_notice(qapp):
    saved = _Signal()
    notices = []
    panel = SimpleNamespace(
        conv_id="chat-1",
        saved=saved,
        _add_notice=lambda text: notices.append(text),
    )

    ChatPanel._on_conversation_save_done(panel, "chat-1", True, "")
    ChatPanel._on_conversation_save_done(panel, "chat-1", False, "disk full")
    ChatPanel._on_conversation_save_done(panel, "other", False, "old workspace")

    assert saved.calls == [()]
    assert notices == ["Conversation save failed: disk full"]


def test_update_context_ui_debounces_budget_recompute(qapp):
    timer = _Timer()
    pool = _WorkerPool()
    panel = SimpleNamespace(
        _context_ui_suspended=False,
        _context_update_timer=timer,
        _start_context_budget_analysis=lambda: ChatPanel._start_context_budget_analysis(panel),
        _context_budget_running=False,
        _context_budget_pending=False,
        _context_budget_generation=0,
        _context_budget_pool=pool,
        cwd="repo",
        history=[{"role": "user", "content": "hi"}],
        _settings=SimpleNamespace(load=lambda: {"system_prompt": "Custom"}),
        model_combo=SimpleNamespace(currentText=lambda: "claude-sonnet-4-6"),
        composer=SimpleNamespace(active_skill=lambda: None),
        _on_context_budget_ready=lambda *_args: None,
    )

    ChatPanel._update_context_ui(panel)
    ChatPanel._update_context_ui(panel)
    ChatPanel._update_context_ui(panel)

    assert timer.started == 3
    assert pool.workers == []

    ChatPanel._apply_context_ui(panel)

    assert len(pool.workers) == 1
    assert isinstance(pool.workers[0], _ContextBudgetWorker)
    assert panel._context_budget_generation == 1
    assert panel._context_budget_running is True


def test_update_context_ui_immediate_starts_context_worker(qapp):
    timer = _Timer()
    starts = []
    panel = SimpleNamespace(
        _context_ui_suspended=False,
        _context_update_timer=timer,
        _start_context_budget_analysis=lambda: starts.append("start"),
    )

    ChatPanel._update_context_ui(panel, immediate=True)

    assert timer.stopped == 1
    assert timer.started == 0
    assert starts == ["start"]


def test_context_budget_ready_applies_current_and_restarts_pending(qapp):
    ring = _ContextRing()
    refreshes = []
    starts = []
    budget = SimpleNamespace(used_tokens=99)
    panel = SimpleNamespace(
        cwd="repo",
        _context_budget_generation=2,
        _context_budget_running=True,
        _context_budget_pending=False,
        _context_budget_cache=None,
        _context_budget_model="",
        model_combo=SimpleNamespace(currentText=lambda: "claude-sonnet-4-6"),
        context_ring=ring,
        _refresh_extension_ui=lambda: refreshes.append("refresh"),
        _add_notice=lambda _text: None,
        _start_context_budget_analysis=lambda: starts.append("start"),
    )

    ChatPanel._on_context_budget_ready(panel, 1, "repo", "claude-sonnet-4-6", budget, "")

    assert ring.budgets == []
    assert panel._context_budget_running is True

    ChatPanel._on_context_budget_ready(panel, 2, "repo", "claude-sonnet-4-6", budget, "")

    assert panel._context_budget_cache is budget
    assert panel._context_budget_model == "claude-sonnet-4-6"
    assert ring.budgets == [budget]
    assert refreshes == ["refresh"]

    panel._context_budget_pending = True
    panel._context_budget_running = True
    ChatPanel._on_context_budget_ready(panel, 2, "other", "claude-sonnet-4-6", budget, "")

    assert starts == ["start"]


def test_saved_tool_calls_reads_anthropic_tool_use():
    calls = _saved_tool_calls({
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": "tu_1", "name": "search_files", "input": {"pattern": "persist"}},
        ],
    })

    assert calls == [("search_files", {"pattern": "persist"})]
