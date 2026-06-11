from ui.widgets.conversation_panel import (
    _ConversationActionWorker,
    _ConversationExportWorker,
    _ROLE_TRASH_HEADER,
    ConversationItem,
    ConversationPanel,
    TrashHeader,
    TitleLabel,
)


class _WorkerPool:
    def __init__(self):
        self.workers = []

    def start(self, worker):
        self.workers.append(worker)


def test_title_label_single_line_elide(qapp):
    full = "read our @README.md does it talk about our crews?"
    label = TitleLabel(full)
    label.resize(120, 20)
    label.show()
    qapp.processEvents()
    assert not label.wordWrap()
    shown = label.elided_display(120)
    assert len(shown) < len(full)
    assert shown.endswith("…") or shown.endswith("...")
    assert label.toolTip() == full


def test_normalize_title_collapses_newlines():
    from ui.widgets.conversation_panel import _normalize_title

    assert _normalize_title("read our @README.md\ndoes it talk") == (
        "read our @README.md does it talk"
    )


def test_conversation_item_cancel_edit_tolerates_deleted_widget(qapp, monkeypatch):
    item = ConversationItem("Demo", "12:00")
    item._start_edit()

    def _gone():
        raise RuntimeError("wrapped C/C++ object of type RenameEdit has been deleted")

    monkeypatch.setattr(item.title_edit, "isVisible", _gone)
    item.cancel_edit()


def test_item_click_ignored_while_renaming(store, qapp):
    store.save(
        "panel_edit",
        {
            "id": "panel_edit",
            "title": "One",
            "messages": [],
            "updated_at": "2026-02-01T12:00:00",
        },
    )
    panel = ConversationPanel(store)
    widget = panel.list.itemWidget(panel.list.item(0))
    widget._start_edit()

    selected = []
    panel.selected.connect(lambda p: selected.append(p))
    panel._on_item_clicked(panel.list.item(0))

    assert selected == []


def test_conversation_row_actions_are_quiet_until_active(qapp):
    pinned = ConversationItem("Pinned chat", "12:00", pinned=True)
    unpinned = ConversationItem("Open chat", "12:00", pinned=False)

    assert pinned.del_btn.isHidden()
    assert not pinned.pin_btn.isHidden()
    assert unpinned.del_btn.isHidden()
    assert unpinned.pin_btn.isHidden()

    unpinned.set_active(True)

    assert not unpinned.del_btn.isHidden()
    assert not unpinned.pin_btn.isHidden()


def test_refresh_clears_editing_item(store, qapp):
    store.save(
        "panel_refresh",
        {
            "id": "panel_refresh",
            "title": "Sample",
            "messages": [],
            "updated_at": "2026-02-01T12:00:00",
        },
    )
    panel = ConversationPanel(store)
    widget = panel.list.itemWidget(panel.list.item(0))
    panel._editing_item = widget
    panel.refresh()
    assert panel._editing_item is None


def test_search_filter_is_debounced(store, qapp, monkeypatch):
    panel = ConversationPanel(store)
    calls = []
    monkeypatch.setattr(panel, "refresh", lambda selected_id=None: calls.append(selected_id))

    panel.search.setText("p")
    panel.search.setText("pl")

    assert calls == []
    assert panel._filter_timer.isActive()

    panel._filter_timer.timeout.emit()

    assert calls == [None]
    panel._filter_timer.stop()


def test_search_filter_uses_indexed_summary_text(store, qapp, monkeypatch):
    store.save(
        "panel_search",
        {
            "id": "panel_search",
            "title": "Body match only",
            "messages": [{"role": "user", "content": "needle from indexed text"}],
            "updated_at": "2026-02-01T12:00:00",
        },
    )
    panel = ConversationPanel(store)

    def fail_load(_path):
        raise AssertionError("conversation search should not load bodies during refresh")

    monkeypatch.setattr(store, "load", fail_load)
    panel.search.setText("indexed text")
    panel._filter_timer.stop()
    panel.refresh()

    assert panel.list.count() == 1


def test_conversation_export_worker_emits_written_path(qapp, monkeypatch):
    done = []

    monkeypatch.setattr(
        "ui.widgets.conversation_panel.export_conversation_file_to_path",
        lambda conv_path, out_path: f"{conv_path}->{out_path}",
    )
    worker = _ConversationExportWorker(4, "chat.json", "chat.md")
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert done == [(4, "chat.json->chat.md", "")]


def test_sidebar_export_starts_worker_without_reading_body(store, qapp, tmp_path, monkeypatch):
    path = store.save(
        "panel_export",
        {
            "id": "panel_export",
            "title": "Export body later",
            "messages": [{"role": "user", "content": "hi"}],
            "updated_at": "2026-02-01T12:00:00",
        },
    )
    panel = ConversationPanel(store)
    started = []
    dialogs = []

    monkeypatch.setattr(
        "ui.widgets.conversation_panel.QFileDialog.getSaveFileName",
        lambda *args: dialogs.append(args) or (str(tmp_path / "out.md"), ""),
    )
    monkeypatch.setattr(
        "ui.widgets.conversation_panel.export_conversation_file_to_path",
        lambda *_args: (_ for _ in ()).throw(AssertionError("should run in worker")),
    )
    monkeypatch.setattr(
        "ui.widgets.conversation_panel.QThreadPool.start",
        lambda _pool, worker: started.append(worker),
    )

    panel._export(str(path), "Export body later")

    assert panel._export_active
    assert isinstance(started[0], _ConversationExportWorker)
    assert dialogs[0][2] == "Export-body-later.md"


def test_sidebar_export_done_reports_errors(store, qapp, monkeypatch):
    panel = ConversationPanel(store)
    warnings = []

    monkeypatch.setattr(
        "ui.widgets.conversation_panel.QMessageBox.warning",
        lambda *args: warnings.append(args),
    )

    panel._export_generation = 2
    panel._export_active = True
    panel._on_export_done(1, "", "stale")

    assert panel._export_active
    assert warnings == []

    panel._on_export_done(2, "", "write failed")

    assert not panel._export_active
    assert warnings and warnings[0][1] == "Export failed"


def test_conversation_action_worker_deletes_and_emits_id(store, qapp):
    path = store.save(
        "action_delete",
        {
            "id": "action_delete",
            "title": "Delete me",
            "messages": [],
            "updated_at": "2026-02-01T12:00:00",
        },
    )
    done = []
    worker = _ConversationActionWorker(store, "delete", str(path))
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert done == [("delete", "action_delete", "", "")]
    assert not path.exists()
    assert store.list_trash()[0][1]["id"] == "action_delete"


def test_conversation_action_methods_queue_workers_without_mutating_inline(store, qapp):
    path = store.save(
        "action_queue",
        {
            "id": "action_queue",
            "title": "Queue me",
            "messages": [],
            "updated_at": "2026-02-01T12:00:00",
        },
    )
    panel = ConversationPanel(store)
    pool = _WorkerPool()
    panel._action_pool = pool

    panel._rename(str(path), "Renamed")
    panel._toggle_pin(str(path))
    panel._delete(str(path))

    assert path.exists()
    assert [worker._action for worker in pool.workers] == ["rename", "pin", "delete"]
    assert pool.workers[0]._title == "Renamed"


def test_trash_section_is_hidden_until_needed(store, qapp):
    panel = ConversationPanel(store)

    assert panel.list.count() == 0


def test_trash_section_expands_and_restores_chat(store, qapp):
    path = store.save(
        "panel_trash",
        {
            "id": "panel_trash",
            "title": "Trashed",
            "messages": [],
            "updated_at": "2026-02-01T12:00:00",
        },
    )
    panel = ConversationPanel(store)
    pool = _WorkerPool()
    panel._action_pool = pool
    panel._delete(str(path))

    assert panel.list.count() == 1
    pool.workers.pop(0).run()
    qapp.processEvents()
    panel.refresh()

    assert panel.list.count() == 1
    header = panel.list.item(0)
    assert header.data(_ROLE_TRASH_HEADER) is True
    header_widget = panel.list.itemWidget(header)
    assert isinstance(header_widget, TrashHeader)
    assert header_widget.title_lbl.text() == "Trash"
    assert header_widget.count_lbl.text() == "1"
    assert header_widget.minimumWidth() >= 140
    assert header.sizeHint().height() == header_widget.height()
    header_widget.clicked.emit()
    qapp.processEvents()

    widget = panel.list.itemWidget(panel.list.item(1))
    assert not widget.restore_btn.isHidden()
    widget.restore_btn.click()
    pool.workers.pop(0).run()
    qapp.processEvents()
    panel.refresh()

    assert store.list_trash() == []
    assert [summary["id"] for _, summary in store.list_all()] == ["panel_trash"]
