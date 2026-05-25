from pathlib import Path

from ui.avatars import avatar_label, avatar_pixmap, persist_portrait


def test_avatar_pixmap_builtin(qapp):
    pix = avatar_pixmap("agent", size=32)
    assert not pix.isNull()


def test_avatar_label_widget(qapp):
    label = avatar_label("human", size=24)
    assert label.pixmap() is not None or label.text() != ""


def test_persist_missing_file_returns_role(isolate_aicc_home):
    assert persist_portrait(str(Path("nope.png")), "user") == "user"
