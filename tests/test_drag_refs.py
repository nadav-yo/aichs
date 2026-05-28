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
from ui.widgets.git_panel import GitPanel
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
