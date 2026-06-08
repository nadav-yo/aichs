import pytest

from PyQt6.QtCore import Qt
from services.chat_drag import (
    AICHS_CHAT_DROP_MIME,
    AICHS_COMMIT_DROP_MIME,
    AICHS_FILE_DROP_MIME,
    chat_drop_payload,
    chat_drop_text,
    commit_drop_payload,
    commit_drop_text,
    file_drop_payload,
    file_drop_text,
    parse_chat_drop,
    parse_commit_drop,
    parse_file_drop,
)
from PyQt6.QtWidgets import QLabel, QListWidget, QTextBrowser
from PyQt6.QtWidgets import QMessageBox

from services.git_status import stage_files
from storage.settings import SettingsStore
from ui.theme import palette
from ui.widgets.git_panel import GitPanel, _CommitDiffDialog
from ui.widgets.left_panel import FileTree
from ui.widgets.conversation_panel import ConversationPanel


def test_files_tree_drags_file_mentions(qapp, workspace):
    tree = FileTree(str(workspace))
    src = tree.topLevelItem(0)
    tree._on_item_expanded(src)
    item = src.child(0)

    mime = tree.mimeData([item])

    assert mime.hasFormat(AICHS_FILE_DROP_MIME)
    assert parse_file_drop(mime.data(AICHS_FILE_DROP_MIME)) == ["src/main.py"]
    assert mime.text() == "@src/main.py"


def test_files_tree_reveals_nested_file(qapp, workspace):
    nested_dir = workspace / "src" / "pkg"
    nested_dir.mkdir()
    target = nested_dir / "api.py"
    target.write_text("API = True\n", encoding="utf-8")
    tree = FileTree(str(workspace))

    assert tree.reveal_file(str(target)) is True

    item = tree.currentItem()
    assert item is not None
    assert item.data(0, Qt.ItemDataRole.UserRole) == str(target)
    assert item.text(0).endswith("api.py")


def test_files_tree_reveal_rejects_outside_workspace(qapp, workspace, tmp_path):
    tree = FileTree(str(workspace))
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")

    assert tree.reveal_file(str(outside)) is False


def test_files_tree_creates_file_in_folder(qapp, workspace):
    tree = FileTree(str(workspace))

    created = tree.create_file(str(workspace / "src"), "notes.txt")

    assert created == workspace / "src" / "notes.txt"
    assert created.read_text(encoding="utf-8") == ""


def test_files_tree_creates_folder_in_folder(qapp, workspace):
    tree = FileTree(str(workspace))

    created = tree.create_folder(str(workspace / "src"), "package")

    assert created == workspace / "src" / "package"
    assert created.is_dir()


def test_files_tree_renames_file(qapp, workspace):
    tree = FileTree(str(workspace))
    old = workspace / "src" / "main.py"

    new = tree.rename_file(str(old), "app.py")

    assert new == workspace / "src" / "app.py"
    assert new.read_text(encoding="utf-8") == "print('hi')\n"
    assert not old.exists()


@pytest.mark.parametrize("name", ["", "  ", ".", "..", "../escape.py", "nested/file.py"])
def test_files_tree_rejects_path_like_file_names(qapp, workspace, name):
    tree = FileTree(str(workspace))

    with pytest.raises(ValueError):
        tree.create_file(str(workspace / "src"), name)


def test_files_tree_rename_rejects_existing_file(qapp, workspace):
    tree = FileTree(str(workspace))
    (workspace / "src" / "app.py").write_text("already here\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        tree.rename_file(str(workspace / "src" / "main.py"), "app.py")


def test_files_tree_create_folder_rejects_existing_path(qapp, workspace):
    tree = FileTree(str(workspace))

    with pytest.raises(FileExistsError):
        tree.create_folder(str(workspace), "src")


def test_files_tree_deletes_file(qapp, workspace):
    tree = FileTree(str(workspace))
    path = workspace / "src" / "main.py"

    tree.delete_path(str(path))

    assert not path.exists()


def test_files_tree_deletes_folder_recursively(qapp, workspace):
    tree = FileTree(str(workspace))
    folder = workspace / "src" / "package"
    folder.mkdir()
    (folder / "module.py").write_text("x = 1\n", encoding="utf-8")

    tree.delete_path(str(folder))

    assert not folder.exists()


def test_files_tree_delete_rejects_workspace_root(qapp, workspace):
    tree = FileTree(str(workspace))

    with pytest.raises(ValueError):
        tree.delete_path(str(workspace))


def test_files_tree_delete_dialog_requires_confirmation(qapp, workspace, monkeypatch):
    tree = FileTree(str(workspace))
    path = workspace / "src" / "main.py"
    monkeypatch.setattr(
        "ui.widgets.left_panel.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )

    tree._delete_path_dialog(str(path))

    assert path.exists()


def test_files_tree_delete_dialog_removes_confirmed_path(qapp, workspace, monkeypatch):
    tree = FileTree(str(workspace))
    path = workspace / "src" / "main.py"
    monkeypatch.setattr(
        "ui.widgets.left_panel.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    tree._delete_path_dialog(str(path))

    assert not path.exists()


def test_files_tree_discard_option_only_for_modified_files(qapp, git_repo):
    modified = git_repo / "src" / "main.py"
    added = git_repo / "note.txt"
    modified.write_text("print('modified')\n", encoding="utf-8")
    added.write_text("new\n", encoding="utf-8")

    tree = FileTree(str(git_repo))

    assert tree._is_discardable_modified_file(str(modified))
    assert not tree._is_discardable_modified_file(str(added))


def test_files_tree_discard_dialog_restores_modified_file(qapp, git_repo, monkeypatch):
    path = git_repo / "src" / "main.py"
    path.write_text("print('discard from files tab')\n", encoding="utf-8")
    tree = FileTree(str(git_repo))
    questions = []
    monkeypatch.setattr(
        "ui.widgets.left_panel.QMessageBox.question",
        lambda parent, title, detail, buttons, default: questions.append(
            (parent, title, detail, buttons, default)
        )
        or QMessageBox.StandardButton.Discard,
    )

    tree._discard_file_dialog(str(path))

    assert path.read_text(encoding="utf-8") == "print('hi')\n"
    assert questions
    assert questions[0][0] is tree
    assert questions[0][1] == "Discard changes?"
    assert "src/main.py" in questions[0][2]


def test_files_tree_discard_dialog_restores_staged_modified_file(qapp, git_repo, monkeypatch):
    path = git_repo / "src" / "main.py"
    path.write_text("print('discard staged from files tab')\n", encoding="utf-8")
    assert stage_files(str(git_repo), ["src/main.py"]).ok
    tree = FileTree(str(git_repo))
    monkeypatch.setattr(
        "ui.widgets.left_panel.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Discard,
    )

    tree._discard_file_dialog(str(path))

    assert path.read_text(encoding="utf-8") == "print('hi')\n"


def test_files_tree_discard_cancel_keeps_modified_file(qapp, git_repo, monkeypatch):
    path = git_repo / "src" / "main.py"
    path.write_text("print('keep files tab change')\n", encoding="utf-8")
    tree = FileTree(str(git_repo))
    monkeypatch.setattr(
        "ui.widgets.left_panel.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel,
    )

    tree._discard_file_dialog(str(path))

    assert path.read_text(encoding="utf-8") == "print('keep files tab change')\n"


def test_git_log_drags_commit_reference(qapp, git_repo):
    panel = GitPanel(str(git_repo))
    item = panel.log.item(0)

    mime = panel.log.mimeData([item])
    commits = parse_commit_drop(mime.data(AICHS_COMMIT_DROP_MIME))

    assert mime.hasFormat(AICHS_COMMIT_DROP_MIME)
    assert len(commits) == 1
    assert commits[0]["subject"] == "initial"
    assert mime.text().startswith("commit ")
    assert "initial" in mime.text()


def test_git_log_double_click_opens_commit_diff(qapp, git_repo, monkeypatch):
    panel = GitPanel(str(git_repo))
    item = panel.log.item(0)
    calls = []

    def fake_commit_diff(repo_path, commit_hash):
        calls.append(("diff", repo_path, commit_hash))
        return "@@ diff\n-old\n+new\n"

    def fake_show(short_hash, subject, diff_text):
        calls.append(("show", short_hash, subject, diff_text))

    monkeypatch.setattr("ui.widgets.git_panel.commit_diff", fake_commit_diff)
    monkeypatch.setattr(panel, "_show_commit_diff_dialog", fake_show)

    panel.log.itemDoubleClicked.emit(item)

    assert calls[0][0] == "diff"
    assert calls[0][1] == str(git_repo)
    assert len(calls[0][2]) >= 7
    assert calls[1] == ("show", item.text().split(" ", 1)[0], "initial", "@@ diff\n-old\n+new\n")


def test_commit_diff_dialog_uses_file_list_and_single_diff_viewer(qapp):
    diff = "\n".join([
        "diff --git a/src/main.py b/src/main.py",
        "index 111..222 100644",
        "--- a/src/main.py",
        "+++ b/src/main.py",
        "@@ -1 +1 @@",
        "-old",
        "+new",
        "diff --git a/README.md b/README.md",
        "index 333..444 100644",
        "--- a/README.md",
        "+++ b/README.md",
        "@@ -0,0 +1 @@",
        "+hello",
    ])
    dlg = _CommitDiffDialog("abc1234", "files", diff)

    file_list = dlg.findChild(QListWidget, "commitFileList")
    viewers = dlg.findChildren(QTextBrowser)
    summary = dlg.findChild(QLabel, "commitSummary")

    assert summary.text() == "2 files changed  +2 -1"
    assert file_list is not None
    assert [file_list.item(row).text() for row in range(file_list.count())] == [
        "src/main.py (+1 -1)",
        "README.md (+1)",
    ]
    assert len(viewers) == 1
    assert "old" in viewers[0].toPlainText()
    file_list.setCurrentRow(1)
    assert "hello" in viewers[0].toPlainText()


@pytest.mark.parametrize("theme_name", ["dark", "light", "modern"])
def test_commit_diff_dialog_uses_active_theme(qapp, theme_name):
    SettingsStore().update({"theme": theme_name})
    dlg = _CommitDiffDialog(
        "abc1234",
        "theme check",
        "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new\n",
    )
    p = palette(theme_name)

    css = dlg.styleSheet()
    viewer = dlg.findChild(QTextBrowser, "commitDiffViewer")

    assert p["BG2"] in css
    assert p["BG3"] in css
    assert p["BORDER"] in css
    assert viewer is not None
    assert p["BG3"] in viewer.toHtml()


def test_conversation_list_drags_chat_reference(qapp, store):
    store.save(
        "drag_chat",
        {
            "id": "drag_chat",
            "title": "Viewport picking",
            "messages": [],
            "updated_at": "2026-02-01T12:00:00",
        },
    )
    panel = ConversationPanel(store)
    item = panel.list.item(0)

    mime = panel.list.mimeData([item])

    assert mime.hasFormat(AICHS_CHAT_DROP_MIME)
    assert parse_chat_drop(mime.data(AICHS_CHAT_DROP_MIME)) == [
        {"id": "drag_chat", "title": "Viewport picking"}
    ]
    assert mime.text() == '@Archivist using chat "Viewport picking", '


def test_drag_payload_helpers_clean_bad_values():
    assert parse_file_drop(b"not json") == []
    assert parse_commit_drop(file_drop_payload(["README.md"])) == []
    assert parse_chat_drop(b"not json") == []
    assert parse_chat_drop(file_drop_payload(["README.md"])) == []
    assert parse_chat_drop(b'{"kind":"aichs-chat-drop","chats":[null,{"id":""},{"id":"c3"}]}') == [
        {"id": "c3", "title": "Untitled"}
    ]
    assert chat_drop_text([]) == ""
    assert chat_drop_text([
        {"id": "c1", "title": "One"},
        {"id": "c2", "title": "Two"},
    ]) == '@Archivist using chats "One", "Two", '
    assert parse_chat_drop(chat_drop_payload([None, {"id": ""}, {"id": "c1", "title": "  A   B  "}])) == [
        {"id": "c1", "title": "A B"}
    ]
    assert parse_commit_drop(
        b'{"kind":"aichs-commit-drop","commits":[null,{"hash":""},{"hash":"def","subject":"  fix   it  "}]}'
    ) == [{"hash": "def", "subject": "fix it"}]
    assert parse_commit_drop(commit_drop_payload([None, {"hash": ""}, {"hash": "abc"}])) == [
        {"hash": "abc", "subject": ""}
    ]
    assert chat_drop_text([{"id": "c1", "title": 'Say "hi"'}]) == '@Archivist using chat "Say \'hi\'", '
    assert commit_drop_text([{"hash": "abc", "subject": ""}]) == "commit abc"
    assert file_drop_text(["docs/read me.md", "docs/read me.md"]) == '@"docs/read me.md"'
