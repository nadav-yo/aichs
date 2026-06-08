import pytest

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
