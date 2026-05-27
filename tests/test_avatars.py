import shutil

from ui.avatars import clear_cache, list_builtin_avatars, persist_portrait, portrait_source


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
