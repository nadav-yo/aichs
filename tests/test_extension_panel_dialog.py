from PyQt6.QtWidgets import QPushButton

from services.tool_registry import StatusBadge
from ui.widgets.extension_contributions import ExtensionContributionsBar, _ExtensionBadgeWorker
from ui.widgets.extension_panel_dialog import (
    ExtensionPanelDialog,
    _ExtensionPanelRefreshWorker,
)


def test_extension_panel_refresh_worker_emits_data(qapp):
    done = []
    worker = _ExtensionPanelRefreshWorker(
        3,
        lambda: ("Panel title", {"title": "Panel title", "body": "Loaded"}),
    )
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert done == [(3, "Panel title", {"title": "Panel title", "body": "Loaded"}, "")]


def test_extension_badge_worker_emits_badges(qapp, monkeypatch):
    badge = StatusBadge(name="health", provider=lambda _ctx: "ok")
    done = []
    monkeypatch.setattr(
        "ui.widgets.extension_contributions.extension_status_badges",
        lambda cwd, *, model="", history=None: ([(badge, {"label": "OK"})], ["warn"]),
    )
    worker = _ExtensionBadgeWorker(5, "repo", "model-a", [{"role": "user"}])
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert done == [(5, [(badge, {"label": "OK"})], ["warn"], "")]


def test_extension_badge_worker_emits_error(qapp, monkeypatch):
    done = []

    def fail(*_args, **_kwargs):
        raise RuntimeError("badge failed")

    monkeypatch.setattr(
        "ui.widgets.extension_contributions.extension_status_badges",
        fail,
    )
    worker = _ExtensionBadgeWorker(6, "repo", "", [])
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert done == [(6, [], [], "badge failed")]


def test_extension_panel_refresh_worker_emits_error(qapp):
    done = []

    def fail():
        raise RuntimeError("slow panel failed")

    worker = _ExtensionPanelRefreshWorker(4, fail)
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert done == [(4, "", None, "slow panel failed")]


def test_extension_panel_refresh_queues_callback_without_running_inline(qapp, monkeypatch):
    dialog = ExtensionPanelDialog("Demo", {"body": "Old"})
    started = []
    calls = []
    dialog.set_refresh_callback(lambda: calls.append(True) or ("Demo", {"body": "New"}))
    monkeypatch.setattr(
        "ui.widgets.extension_panel_dialog.QThreadPool.start",
        lambda _pool, worker: started.append(worker),
    )

    dialog.refresh_panel()

    assert calls == []
    assert isinstance(started[0], _ExtensionPanelRefreshWorker)
    assert dialog._refresh_active


def test_extension_contributions_refresh_queues_badges_without_running_inline(qapp, monkeypatch):
    started = []
    calls = []
    monkeypatch.setattr(
        "ui.widgets.extension_contributions.QThreadPool.start",
        lambda _pool, worker: started.append(worker),
    )
    monkeypatch.setattr(
        "ui.widgets.extension_contributions.extension_status_badges",
        lambda *_args, **_kwargs: calls.append(True) or ([], []),
    )

    bar = ExtensionContributionsBar("repo")
    bar.set_context(cwd="repo", model="model-a", history=[{"role": "user"}])

    assert calls == []
    assert all(isinstance(worker, _ExtensionBadgeWorker) for worker in started)
    assert started[-1]._model == "model-a"
    assert started[-1]._history == [{"role": "user"}]


def test_extension_contributions_applies_current_badges_and_ignores_stale(qapp, monkeypatch):
    badge = StatusBadge(name="health", provider=lambda _ctx: "ok")
    monkeypatch.setattr(
        "ui.widgets.extension_contributions.QThreadPool.start",
        lambda *_args: None,
    )
    bar = ExtensionContributionsBar("repo")
    bar._badge_generation = 2
    bar._badge_active = True

    bar._on_badges_done(1, [(badge, {"label": "STALE"})], [], "")

    assert bar.findChildren(QPushButton) == []
    assert bar._badge_active

    bar._on_badges_done(2, [(badge, {"label": "OK", "tooltip": "Ready"})], ["warn"], "")

    buttons = bar.findChildren(QPushButton)
    assert [button.text() for button in buttons] == ["OK"]
    assert buttons[0].toolTip() == "Ready"
    assert bar._badge_errors == ["warn"]
    assert not bar._badge_active
    assert not bar.isHidden()


def test_extension_panel_applies_current_refresh_and_ignores_stale(qapp):
    dialog = ExtensionPanelDialog("Demo", {"title": "Old", "body": "Old body"})
    dialog._refresh_generation = 2
    dialog._refresh_active = True

    dialog._on_refresh_done(1, "Stale", {"title": "Stale", "body": "Stale body"}, "")

    assert dialog._heading.text() == "Old"
    assert dialog._refresh_active

    dialog._on_refresh_done(2, "Loaded", {"title": "Loaded", "body": "Fresh body"}, "")

    labels = [label.text() for label in dialog.findChildren(type(dialog._heading))]
    assert dialog._heading.text() == "Loaded"
    assert "Fresh body" in labels
    assert not dialog._refresh_active


def test_extension_contributions_open_panel_loads_asynchronously(qapp, monkeypatch):
    created = []
    calls = []

    class FakeDialog:
        def __init__(self, title, data, *, on_action=None, parent=None):
            self.title = title
            self.data = data
            self.on_action = on_action
            self.parent = parent
            self.refresh_called = False
            self.callback = None
            created.append(self)

        def set_refresh_callback(self, callback):
            self.callback = callback

        def refresh_panel(self):
            self.refresh_called = True

        def exec(self):
            return 0

    def panel_data(*args, **kwargs):
        calls.append((args, kwargs))
        return "Panel", {"body": "Loaded"}, []

    monkeypatch.setattr(
        "ui.widgets.extension_contributions.ExtensionPanelDialog",
        FakeDialog,
    )
    monkeypatch.setattr(
        "ui.widgets.extension_contributions.QThreadPool.start",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "ui.widgets.extension_contributions.extension_status_badges",
        lambda *_args, **_kwargs: ([], []),
    )
    monkeypatch.setattr(
        "ui.widgets.extension_contributions.extension_panel_data",
        panel_data,
    )
    bar = ExtensionContributionsBar("repo")

    bar._open_panel("demo_panel")

    assert calls == []
    assert created[0].title == "demo_panel"
    assert created[0].data == {"title": "demo_panel", "body": "Loading panel..."}
    assert created[0].refresh_called

    assert created[0].callback() == ("Panel", {"body": "Loaded"})
    assert calls
