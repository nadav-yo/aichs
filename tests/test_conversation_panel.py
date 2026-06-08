from ui.widgets.conversation_panel import (
    _ROLE_TRASH_HEADER,
    ConversationItem,
    ConversationPanel,
    TrashHeader,
    TitleLabel,
)


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


def test_pinned_chat_hides_delete_button(qapp):
    pinned = ConversationItem("Pinned chat", "12:00", pinned=True)
    unpinned = ConversationItem("Open chat", "12:00", pinned=False)
    pinned.show()
    unpinned.show()
    qapp.processEvents()
    assert not pinned.del_btn.isVisible()
    assert unpinned.del_btn.isVisible()


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
    panel._delete(str(path))
    qapp.processEvents()

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

    assert store.list_trash() == []
    assert [summary["id"] for _, summary in store.list_all()] == ["panel_trash"]
