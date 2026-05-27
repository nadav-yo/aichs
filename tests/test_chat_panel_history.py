from types import SimpleNamespace

from ui.widgets.chat_panel import ChatPanel, _ConversationRuntime


class _Signal:
    def emit(self, *args):
        pass


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

    ChatPanel._on_done(panel, "r1", "Stopped.")

    assert panel.history == expected_history
    assert panel.conv_data["messages"] == expected_history
    assert panel._bubbles[len(expected_history) - 1] is bubble


def test_insert_history_bubble_skips_runtime_messages(qapp):
    panel = SimpleNamespace()
    panel.history = [{"role": "user", "content": "hidden", "synthetic": "extension"}]
    panel._make_bubble = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("hidden"))

    assert ChatPanel._insert_history_bubble(panel, 0) is None
