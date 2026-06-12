from unittest.mock import MagicMock


from services.palette import PaletteContext, PaletteItem, _list_files, build_palette_items, filter_items, fuzzy_score


def test_fuzzy_score():
    assert fuzzy_score("", "Anything") == 1
    assert fuzzy_score("exp", "export file") > fuzzy_score("zzz", "export file")
    assert fuzzy_score("ef", "export file") > 0
    assert fuzzy_score("xyz", "export file") == 0


def test_filter_items():
    items = [
        PaletteItem("Alpha", "a"),
        PaletteItem("Beta", "b"),
    ]
    assert len(filter_items(items, "")) == 2
    filtered = filter_items(items, "alp")
    assert len(filtered) == 1
    assert filtered[0].label == "Alpha"


def test_build_palette_items(workspace):
    store = MagicMock()
    store.list_all.return_value = []
    ctx = PaletteContext(
        store=store,
        cwd=str(workspace),
        is_streaming=lambda: True,
        on_open_conversation=lambda p: None,
        on_open_file=lambda p: None,
        on_new_chat=lambda: None,
        on_export=lambda: None,
        on_compact=lambda: None,
        on_settings=lambda: None,
        on_stop=lambda: None,
        on_set_model=lambda m: None,
    )
    items = build_palette_items(ctx)
    labels = {it.label for it in items}
    assert "New chat" in labels
    assert "Stop streaming" in labels
    assert any(it.label.startswith("Switch model:") for it in items)


def test_build_palette_items_without_streaming_and_lists_visible_files(workspace):
    (workspace / "README.md").write_text("# Demo\n", encoding="utf-8")
    (workspace / ".hidden").write_text("secret\n", encoding="utf-8")
    store = MagicMock()
    store.list_all.return_value = []
    ctx = PaletteContext(
        store=store,
        cwd=str(workspace),
        is_streaming=lambda: False,
        on_open_conversation=lambda p: None,
        on_open_file=lambda p: None,
        on_new_chat=lambda: None,
        on_export=lambda: None,
        on_compact=lambda: None,
        on_settings=lambda: None,
        on_stop=lambda: None,
        on_set_model=lambda m: None,
    )

    items = build_palette_items(ctx)
    labels = {it.label for it in items}

    assert "Stop streaming" not in labels
    assert "README.md" in labels
    assert all(".hidden" not in label for label in labels)
    assert _list_files(workspace / "missing") == []
