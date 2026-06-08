from pathlib import Path
import shutil
import subprocess

import pytest

from services.extension_installer import (
    cleanup_extension_install_source,
    discover_extension_candidates,
    extension_install_root,
    install_extension_candidates,
    prepare_extension_install_source,
)


def _write_folder_extension(root: Path, name: str, description: str = "") -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    entry = folder / "extension.py"
    entry.write_text(
        f'EXTENSION_DESCRIPTION = "{description}"\n\n'
        "def register(registry):\n"
        "    pass\n",
        encoding="utf-8",
    )
    return folder


def test_discover_extension_candidates_finds_folders_and_files(tmp_path):
    _write_folder_extension(tmp_path, "python-lang", "Python support")
    (tmp_path / "single.py").write_text(
        '"""Single-file extension."""\n\n'
        "def register(registry):\n"
        "    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")

    candidates = discover_extension_candidates(tmp_path)

    assert [candidate.name for candidate in candidates] == ["python-lang", "single.py"]
    assert candidates[0].kind == "folder"
    assert candidates[0].description == "Python support"
    assert candidates[1].kind == "file"
    assert candidates[1].description == "Single-file extension."


def test_install_extension_candidates_supports_local_and_global(workspace):
    local_source = _write_folder_extension(workspace / "src_ext", "local-tool")
    global_source = _write_folder_extension(workspace / "global_ext", "global-tool")
    local_candidate, global_candidate = (
        discover_extension_candidates(local_source.parent)[0],
        discover_extension_candidates(global_source.parent)[0],
    )

    local_results = install_extension_candidates(
        [local_candidate],
        scope="local",
        cwd=str(workspace),
    )
    global_results = install_extension_candidates(
        [global_candidate],
        scope="global",
        cwd=str(workspace),
    )

    assert local_results[0].path == workspace / ".aichs" / "extensions" / "local-tool"
    assert (local_results[0].path / "extension.py").exists()
    assert global_results[0].path == Path.home() / ".aichs" / "extensions" / "global-tool"
    assert (global_results[0].path / "extension.py").exists()


def test_install_extension_candidates_replaces_existing_extension(workspace):
    source = _write_folder_extension(workspace / "source", "demo")
    candidate = discover_extension_candidates(source.parent)[0]
    target = extension_install_root("local", str(workspace)) / "demo"
    target.mkdir(parents=True)
    (target / "old.txt").write_text("old", encoding="utf-8")

    install_extension_candidates([candidate], scope="local", cwd=str(workspace))

    assert not (target / "old.txt").exists()
    assert (target / "extension.py").exists()


def test_install_root_extension_uses_repo_name_and_skips_git_dir(workspace):
    source = workspace / "root-demo"
    source.mkdir()
    (source / "extension.py").write_text(
        "def register(registry):\n"
        "    pass\n",
        encoding="utf-8",
    )
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("private", encoding="utf-8")
    candidate = discover_extension_candidates(source)[0]

    result = install_extension_candidates([candidate], scope="local", cwd=str(workspace))[0]

    assert result.path.name == "root-demo"
    assert (result.path / "extension.py").exists()
    assert not (result.path / ".git").exists()


def test_prepare_extension_install_source_clones_git_repo(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git not on PATH")
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_folder_extension(repo, "one", "One")
    _write_folder_extension(repo, "two", "Two")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "extensions"], cwd=repo, check=True, capture_output=True)

    source = prepare_extension_install_source(str(repo))
    try:
        assert source.kind == "git"
        assert source.checkout_path.exists()
        assert source.checkout_path.name == "repo"
        assert [candidate.name for candidate in source.candidates] == ["one", "two"]
    finally:
        temp_dir = source.temp_dir
        cleanup_extension_install_source(source)
    assert not temp_dir.exists()


def test_extension_install_root_requires_known_scope(workspace):
    with pytest.raises(ValueError, match="scope"):
        extension_install_root("team", str(workspace))
    with pytest.raises(ValueError, match="workspace"):
        extension_install_root("local", "")
