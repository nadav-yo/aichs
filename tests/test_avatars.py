
from ui.avatars import avatar_pixmap, clear_cache, list_builtin_avatars, persist_portrait, portrait_source


def test_list_builtin_avatars():
    names = list_builtin_avatars()
    assert "agent" in names
    assert "human" in names
    assert "crew_scout" in names
    assert "crew_archivist" in names
    assert "crew_critic" not in names


def test_portrait_source_default(isolate_aichs_home):
    assert portrait_source("user") == "user"


def test_persist_builtin_name():
    assert persist_portrait("agent", "assistant") == "agent"


def test_persist_custom_file(tmp_path, isolate_aichs_home):
    src = tmp_path / "pic.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n")
    dest = persist_portrait(str(src), "user")
    assert dest.endswith(".png")
    clear_cache()


def test_persist_existing_custom_file_does_not_copy_onto_itself(tmp_path, isolate_aichs_home):
    src = tmp_path / "pic.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n")
    dest = persist_portrait(str(src), "human")

    assert persist_portrait(dest, "human") == dest


def test_invalid_custom_avatar_falls_back_without_null_pixmap(tmp_path, qapp):
    source = tmp_path / "truncated.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\n")

    image = avatar_pixmap(str(source))

    assert not image.isNull()
    assert image.size().width() == 28
