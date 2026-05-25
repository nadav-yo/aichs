import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


def write_extension(workspace: Path, filename: str, source: str) -> Path:
    ext_dir = workspace / ".aicc" / "extensions"
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
def isolate_aicc_home(monkeypatch, tmp_path):
    """Keep extension loading deterministic (ignore real ~/.aicc/extensions)."""
    home = tmp_path / "fake_home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    settings_dir = home / ".aicc"
    settings_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.SETTINGS_PATH", settings_dir / "settings.json")
    monkeypatch.setattr("config.CONV_DIR", settings_dir / "conversations")
    monkeypatch.setattr("config.AVATARS_DIR", settings_dir / "avatars")
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
def git_repo(workspace):
    if not shutil.which("git"):
        pytest.skip("git not on PATH")

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=workspace,
            check=True,
            capture_output=True,
        )

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test User")
    git("add", ".")
    git("commit", "-m", "initial")
    return workspace


@pytest.fixture
def git_repo_with_change(git_repo):
    main = git_repo / "src" / "main.py"
    main.write_text("print('changed')\n", encoding="utf-8")
    return git_repo, main
