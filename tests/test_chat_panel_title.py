from ui.widgets.chat_panel import ChatPanel


class _Signal:
    def connect(self, _callback):
        pass


class _FakeTitleThread:
    created = []

    def __init__(self, conv_id, model, user_text):
        self.conv_id = conv_id
        self.model = model
        self.user_text = user_text
        self.done = _Signal()
        self.error = _Signal()
        self.started = False
        self.created.append(self)

    def isRunning(self):
        return False

    def start(self):
        self.started = True


class _ModelCombo:
    def currentText(self):
        return "claude-sonnet-4-6"


def test_auto_title_starts_from_first_user_message(monkeypatch):
    _FakeTitleThread.created = []
    monkeypatch.setattr("ui.widgets.chat_panel.TitleThread", _FakeTitleThread)

    panel = type("Panel", (), {})()
    panel.conv_id = "chat-1"
    panel.conv_data = {"title_auto": True}
    panel.history = [{"role": "user", "content": "x" * 200}]
    panel.title_thread = None
    panel.model_combo = _ModelCombo()
    panel._resolve_model = lambda model: model
    panel._on_auto_title_done = lambda *_args: None
    panel._on_auto_title_error = lambda *_args: None

    ChatPanel._maybe_auto_title(panel)

    thread = _FakeTitleThread.created[0]
    assert thread.started is True
    assert thread.conv_id == "chat-1"
    assert thread.user_text == "x" * 100


def test_auto_title_waits_for_single_first_user(monkeypatch):
    _FakeTitleThread.created = []
    monkeypatch.setattr("ui.widgets.chat_panel.TitleThread", _FakeTitleThread)

    panel = type("Panel", (), {})()
    panel.conv_id = "chat-1"
    panel.conv_data = {"title_auto": True}
    panel.history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second"},
    ]
    panel.title_thread = None
    panel.model_combo = _ModelCombo()
    panel._resolve_model = lambda model: model
    panel._on_auto_title_done = lambda *_args: None
    panel._on_auto_title_error = lambda *_args: None

    ChatPanel._maybe_auto_title(panel)

    assert _FakeTitleThread.created == []
