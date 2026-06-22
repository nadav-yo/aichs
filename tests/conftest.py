import shutil
import subprocess
import sys
import textwrap
import os
from pathlib import Path

import pytest


os.environ["QT_QPA_PLATFORM"] = "offscreen"


def pytest_configure(config):
    config.option.tbstyle = "short"


def write_extension(workspace: Path, filename: str, source: str) -> Path:
    ext_dir = workspace / ".aichs" / "extensions"
    ext_dir.mkdir(parents=True, exist_ok=True)
    path = ext_dir / filename
    path.write_text(textwrap.dedent(source).strip() + "\n", encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def close_qt_windows():
    yield
    if "PyQt6.QtWidgets" not in sys.modules:
        return
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    from PyQt6.QtCore import QEvent

    for widget in app.topLevelWidgets():
        widget.close()
        if widget.objectName() == "agentCanvas":
            continue
        widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def clear_service_caches():
    from services.file_search import clear_workspace_file_cache
    from services.git_snapshot import clear_git_snapshot_cache
    from services.language_features import clear_matching_language_cache
    from services.language_snapshot import clear_language_status_cache
    from services.tool_registry import clear_all_extension_caches

    clear_workspace_file_cache()
    clear_git_snapshot_cache()
    clear_matching_language_cache()
    clear_language_status_cache()
    clear_all_extension_caches()
    yield
    clear_workspace_file_cache()
    clear_git_snapshot_cache()
    clear_matching_language_cache()
    clear_language_status_cache()
    clear_all_extension_caches()


@pytest.fixture
def conv_dir(monkeypatch, tmp_path, isolate_aichs_home):
    path = tmp_path / "conversations"
    path.mkdir()
    monkeypatch.setattr("config.CONV_DIR", path)
    monkeypatch.setattr("storage.repository.CONV_DIR", path)
    return path


@pytest.fixture
def store(conv_dir):
    from storage.repository import ConversationStore

    return ConversationStore()


@pytest.fixture(autouse=True)
def isolate_aichs_home(monkeypatch, tmp_path):
    """Keep app-owned user data deterministic and out of the real home profile."""
    home = tmp_path / "fake_home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    settings_dir = home / ".aichs"
    settings_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AICHS_HOME", str(settings_dir))
    monkeypatch.setattr("config.AICHS_HOME", settings_dir)
    monkeypatch.setattr("config.SETTINGS_PATH", settings_dir / "settings.json")
    monkeypatch.setattr("config.CONV_DIR", settings_dir / "conversations")
    monkeypatch.setattr("config.AVATARS_DIR", settings_dir / "avatars")
    monkeypatch.setattr("config.WORKSPACES_PATH", settings_dir / "workspaces.json")
    monkeypatch.setattr("storage.repository.AICHS_HOME", settings_dir)
    monkeypatch.setattr("storage.repository.WORKSPACES_PATH", settings_dir / "workspaces.json")
    monkeypatch.setattr("storage.settings.SETTINGS_PATH", settings_dir / "settings.json")
    monkeypatch.setattr("services.skills._USER_DIR", settings_dir / "skills")
    monkeypatch.setattr("services.model_registry._MODELS_PATH", settings_dir / "models.json")


@pytest.fixture
def workspace(tmp_path):
    """Minimal repo tree for path and slash-command tests."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    return root


@pytest.fixture(scope="session")
def git_repo_template(tmp_path_factory):
    if not shutil.which("git"):
        pytest.skip("git not on PATH")

    root = tmp_path_factory.mktemp("git_repo_template") / "repo"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    config = root / ".git" / "config"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "\n[user]\n\temail = test@example.com\n\tname = Test User\n",
        encoding="utf-8",
    )
    git("add", ".")
    git("commit", "-q", "-m", "initial")
    return root


@pytest.fixture
def workspace_with_extension(workspace):
    write_extension(
        workspace,
        "demo.py",
        '''
        def register(registry):
            registry.command(
                name="demo_cmd",
                description="Demo extension command",
                prompt="Run the demo workflow",
            )
        ''',
    )
    return workspace


@pytest.fixture
def cwd(workspace):
    return str(workspace)


@pytest.fixture
def workspace_with_tool(workspace):
    write_extension(
        workspace,
        "tooling.py",
        '''
        def register(registry):
            registry.tool(
                name="ping",
                description="Return pong",
                input_schema={"type": "object", "properties": {}},
                execute=lambda ctx, inputs: "pong",
                parallel_safe=True,
            )
            registry.context("Ping note", lambda ctx: "from extension")
        ''',
    )
    return workspace


@pytest.fixture
def workspace_with_broken_extension(workspace):
    write_extension(workspace, "broken.py", "def register(registry\n")
    return workspace


@pytest.fixture
def workspace_with_missing_register(workspace):
    write_extension(workspace, "noop.py", "# extension without register()\n")
    return workspace


@pytest.fixture
def git_repo(workspace, git_repo_template):
    shutil.copytree(git_repo_template, workspace, dirs_exist_ok=True)
    return workspace


@pytest.fixture
def git_repo_with_change(git_repo):
    main = git_repo / "src" / "main.py"
    main.write_text("print('changed')\n", encoding="utf-8")
    return git_repo, main
