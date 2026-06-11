from services.git_snapshot import GitSnapshot
from services.git_status import GitFileChange
from services.workspace_snapshot import (
    build_workspace_snapshot,
    display_chat_time,
    display_updated_at,
)
from storage.repository import ConversationStore, register_workspace


def test_workspace_snapshot_collects_home_context(workspace, tmp_path):
    (workspace / "README.md").write_text("# Project Readme\n\nUseful context.\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("Always run the tests.\n", encoding="utf-8")
    skills_dir = workspace / ".aichs" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "review.md").write_text("Review carefully.\n", encoding="utf-8")
    ext_dir = workspace / ".aichs" / "extensions"
    ext_dir.mkdir(parents=True)
    (ext_dir / "demo.py").write_text("def register(registry):\n    pass\n", encoding="utf-8")
    ConversationStore(str(workspace)).save(
        "dash-chat",
        {
            "id": "dash-chat",
            "title": "Dashboard chat",
            "updated_at": "2026-02-03T04:05:00",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    other = tmp_path / "other"
    other.mkdir()
    register_workspace(other)

    snapshot = build_workspace_snapshot(str(workspace))

    assert snapshot.name == workspace.name
    assert snapshot.readme_exists is True
    assert "Project Readme" in snapshot.readme_text
    assert snapshot.agents_exists is True
    assert "Always run the tests." in snapshot.agents_text
    assert snapshot.skills_count == 1
    assert snapshot.extensions_count == 1
    assert snapshot.recent_chats[0].title == "Dashboard chat"
    assert any(row.path == str(other.resolve()) for row in snapshot.recent_workspaces)


def test_workspace_snapshot_uses_supplied_git_snapshot(workspace, monkeypatch):
    import services.workspace_snapshot as workspace_snapshot

    change = GitFileChange(
        code=" M",
        label="M",
        rel_path="src/main.py",
        abs_path=str(workspace / "src" / "main.py"),
    )
    monkeypatch.setattr(
        workspace_snapshot,
        "build_git_snapshot",
        lambda _root: (_ for _ in ()).throw(AssertionError("should not refresh git")),
    )

    snapshot = build_workspace_snapshot(
        str(workspace),
        git_snapshot=GitSnapshot(
            repo_path=str(workspace.resolve()),
            is_repo=True,
            changes=(change,),
            branch="main",
        ),
    )

    assert snapshot.git_repo is True
    assert snapshot.changed_count == 1
    assert snapshot.branch == "main"


def test_workspace_snapshot_keeps_supplied_git_changes_compatibility(workspace, monkeypatch):
    import services.workspace_snapshot as workspace_snapshot

    change = GitFileChange(
        code=" M",
        label="M",
        rel_path="src/main.py",
        abs_path=str(workspace / "src" / "main.py"),
    )
    monkeypatch.setattr(
        workspace_snapshot,
        "build_git_snapshot",
        lambda _root: (_ for _ in ()).throw(AssertionError("should not refresh git")),
    )

    snapshot = build_workspace_snapshot(str(workspace), git_changes=[change])

    assert snapshot.git_repo is True
    assert snapshot.changed_count == 1


def test_workspace_snapshot_reads_full_agents_text(workspace):
    long_agents = "Project instructions.\n" + ("keep this rule visible " * 1000)
    (workspace / "AGENTS.md").write_text(long_agents, encoding="utf-8")

    snapshot = build_workspace_snapshot(str(workspace))

    assert snapshot.agents_text == long_agents


def test_workspace_snapshot_display_dates():
    assert display_updated_at("2026-02-03T04:05:00") == "Last opened Feb 03, 2026 04:05"
    assert display_chat_time("2026-02-03T04:05:00") == "Feb 03, 2026 04:05"
