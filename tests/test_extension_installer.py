from pathlib import Path
import shutil
import subprocess

import pytest

import config
import services.extension_installer as extension_installer
from services.extension_installer import (
    cleanup_extension_install_source,
    discover_extension_candidates,
    extension_install_root,
    ExtensionInstallSource,
    format_commit_date,
    git_source_metadata,
    GitExtensionSourceResolver,
    install_conflicts,
    install_content_matches,
    install_extension_candidates,
    lookup_existing_install,
    prepare_extension_install_source,
)
from services.tool_registry import is_extension_disabled


def _write_folder_extension(root: Path, name: str, description: str = "", *, display_name: str = "") -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    entry = folder / "extension.py"
    entry.write_text(
        f'EXTENSION_DESCRIPTION = "{description}"\n\n'
        "def register(registry):\n"
        "    pass\n",
        encoding="utf-8",
    )
    if display_name:
        import json
        (folder / "aichs-extension.json").write_text(
            json.dumps({"name": display_name, "description": description}),
            encoding="utf-8",
        )
    return folder


def _git_commit(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    config = repo / ".git" / "config"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "\n[user]\n\temail = test@example.com\n\tname = Test User\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--author", "test <test@example.com>"],
        cwd=repo,
        check=True,
        capture_output=True,
        env={
            **__import__("os").environ,
            "GIT_AUTHOR_DATE": "2026-06-11T12:00:00+00:00",
            "GIT_COMMITTER_DATE": "2026-06-11T12:00:00+00:00",
        },
    )


def test_git_source_metadata_reads_head_commit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("demo", encoding="utf-8")
    _git_commit(repo)

    commit_hash, commit_date = git_source_metadata(repo)

    assert len(commit_hash) == 12
    assert commit_date.startswith("2026-06-11")
    assert format_commit_date(commit_date) == "Jun 11, 2026"


def test_lookup_existing_install_and_conflicts(workspace):
    source = _write_folder_extension(workspace / "source", "demo")
    candidate = discover_extension_candidates(source.parent)[0]
    target = extension_install_root("local", str(workspace)) / "demo"
    target.mkdir(parents=True)
    entrypoint = target / "extension.py"
    entrypoint.write_text("old", encoding="utf-8")

    existing = lookup_existing_install("demo", scope="local", cwd=str(workspace))
    conflicts = install_conflicts([candidate], scope="local", cwd=str(workspace))

    assert existing is not None
    assert existing.path == target
    assert existing.modified_at is not None
    assert conflicts["demo"].path == target
    assert conflicts["demo"].replaces_target is True
    assert lookup_existing_install("missing", scope="local", cwd=str(workspace)) is None


def test_lookup_existing_install_is_case_insensitive(workspace):
    source = _write_folder_extension(workspace / "source", "demo")
    candidate = discover_extension_candidates(source.parent)[0]
    target = extension_install_root("local", str(workspace)) / "Demo"
    target.mkdir(parents=True)
    (target / "extension.py").write_text("old", encoding="utf-8")

    existing = lookup_existing_install("demo", scope="local", cwd=str(workspace))
    conflicts = install_conflicts([candidate], scope="local", cwd=str(workspace))

    assert existing is not None
    assert existing.path == target
    assert conflicts["demo"].path == target
    assert conflicts["demo"].replaces_target is True


def test_install_conflicts_detects_global_when_installing_local(workspace):
    import config

    _write_folder_extension(
        config.AICHS_HOME / "extensions",
        "context-resilience",
        "Stored helpers",
        display_name="Context Resilience",
    )
    source = _write_folder_extension(
        workspace / "source",
        "context-resilience",
        "Incoming helpers",
        display_name="Context Resilience",
    )
    candidate = discover_extension_candidates(source.parent)[0]

    conflicts = install_conflicts([candidate], scope="local", cwd=str(workspace))

    assert "context-resilience" in conflicts
    assert conflicts["context-resilience"].scope == "global"
    assert conflicts["context-resilience"].replaces_target is False


def test_install_conflicts_detects_local_when_installing_global(workspace):

    _write_folder_extension(
        workspace / ".aichs" / "extensions",
        "context-resilience",
        "Stored helpers",
        display_name="Context Resilience",
    )
    source = _write_folder_extension(
        workspace / "source",
        "context-resilience",
        "Incoming helpers",
        display_name="Context Resilience",
    )
    candidate = discover_extension_candidates(source.parent)[0]

    conflicts = install_conflicts([candidate], scope="global", cwd=str(workspace))

    assert "context-resilience" in conflicts
    assert conflicts["context-resilience"].scope == "local"
    assert conflicts["context-resilience"].replaces_target is False


def test_install_content_matches_detects_identical_extensions(workspace):
    import shutil

    source = _write_folder_extension(workspace / "source", "demo", "Same body")
    candidate = discover_extension_candidates(source.parent)[0]
    target = extension_install_root("local", str(workspace)) / "demo"
    shutil.copytree(source, target)

    assert install_content_matches(candidate, target) is True

    (target / "extension.py").write_text("different", encoding="utf-8")
    assert install_content_matches(candidate, target) is False


def test_install_conflicts_marks_same_content(workspace):
    import shutil

    source = _write_folder_extension(workspace / "source", "demo", "Same body")
    candidate = discover_extension_candidates(source.parent)[0]
    target = extension_install_root("local", str(workspace)) / "demo"
    shutil.copytree(source, target)

    conflict = install_conflicts([candidate], scope="local", cwd=str(workspace))["demo"]

    assert conflict.same_content is True


def test_install_conflicts_match_by_display_name(workspace):
    _write_folder_extension(
        workspace / ".aichs" / "extensions",
        "context-resilience",
        "Stored helpers",
        display_name="Context Resilience",
    )
    source = _write_folder_extension(
        workspace / "source",
        "context-resilience",
        "Incoming helpers",
        display_name="Context Resilience",
    )
    candidate = discover_extension_candidates(source.parent)[0]

    assert candidate.display_name == "Context Resilience"
    conflicts = install_conflicts([candidate], scope="local", cwd=str(workspace))

    assert "context-resilience" in conflicts


def test_discover_extension_candidates_reads_display_name(tmp_path):
    _write_folder_extension(
        tmp_path,
        "context-resilience",
        "Compaction helpers",
        display_name="Context Resilience",
    )

    candidate = discover_extension_candidates(tmp_path)[0]

    assert candidate.name == "context-resilience"
    assert candidate.display_name == "Context Resilience"


def test_discover_extension_candidates_finds_folders_and_files_without_glob(tmp_path, monkeypatch):
    _write_folder_extension(tmp_path, "python-lang", "Python support")
    (tmp_path / "single.py").write_text(
        '"""Single-file extension."""\n\n'
        "def register(registry):\n"
        "    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    nested = tmp_path / "nested" / "inner"
    nested.mkdir(parents=True)
    (nested / "extension.py").write_text("def register(registry):\n    pass\n", encoding="utf-8")

    def fail_glob(self, pattern):
        raise AssertionError(f"unexpected glob({pattern})")

    monkeypatch.setattr(Path, "glob", fail_glob)

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
    assert is_extension_disabled(local_results[0].path, str(workspace))
    assert global_results[0].path == config.AICHS_HOME / "extensions" / "global-tool"
    assert (global_results[0].path / "extension.py").exists()
    assert is_extension_disabled(global_results[0].path, str(workspace))


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


def test_prepare_extension_install_source_clones_git_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_folder_extension(repo, "one", "One")
    _write_folder_extension(repo, "two", "Two")

    def fake_clone(url, checkout_path):
        assert url == str(repo)
        shutil.copytree(repo, checkout_path)

    monkeypatch.setattr(extension_installer, "_run_git_clone", fake_clone)

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


def test_prepare_extension_install_source_rejects_unsupported_url():
    with pytest.raises(ValueError, match="Only git"):
        prepare_extension_install_source("not-a-real-extension-source")


def test_git_extension_resolver_can_handle_urls_and_existing_paths(tmp_path):
    resolver = GitExtensionSourceResolver()
    local = tmp_path / "local"
    local.mkdir()

    assert not resolver.can_handle("")
    assert resolver.can_handle("https://example.test/ext.git")
    assert resolver.can_handle("git@example.test:team/ext.git")
    assert resolver.can_handle(str(local))


def test_prepare_extension_install_source_cleans_temp_dir_on_clone_failure(monkeypatch, tmp_path):
    made_temp = tmp_path / "checkout-temp"
    removed = []
    monkeypatch.setattr(extension_installer.tempfile, "mkdtemp", lambda prefix: str(made_temp))
    monkeypatch.setattr(
        extension_installer,
        "_run_git_clone",
        lambda url, checkout_path: (_ for _ in ()).throw(RuntimeError("clone failed")),
    )
    monkeypatch.setattr(extension_installer, "_rmtree", lambda path: removed.append(path))

    with pytest.raises(RuntimeError, match="clone failed"):
        prepare_extension_install_source("https://example.test/ext.git")

    assert removed == [made_temp]


def test_cleanup_extension_install_source_accepts_none_and_removes_temp(monkeypatch, tmp_path):
    removed = []
    source = ExtensionInstallSource(
        url="url",
        kind="git",
        checkout_path=tmp_path / "checkout",
        temp_dir=tmp_path / "temp",
        candidates=[],
    )
    monkeypatch.setattr(extension_installer, "_rmtree", lambda path: removed.append(path))

    cleanup_extension_install_source(None)
    cleanup_extension_install_source(source)

    assert removed == [source.temp_dir]


def test_extension_installer_private_helpers_cover_safe_names_and_errors(tmp_path, monkeypatch):
    assert extension_installer._safe_install_name("../bad name!.py") == "bad_name_.py"
    assert extension_installer._safe_install_name("...") == "extension"
    assert extension_installer._source_name_from_url("https://example.test/team/demo.git") == "demo"
    assert extension_installer._source_name_from_url("git@example.test:team/demo.git") == "demo"

    with pytest.raises(ValueError, match="escaped"):
        extension_installer._replace_path(tmp_path / "source.py", tmp_path / "outside.py", tmp_path / "root")

    monkeypatch.setattr(
        extension_installer.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )
    with pytest.raises(RuntimeError, match="git is not installed"):
        extension_installer._run_git_clone("url", tmp_path / "checkout")


def test_extension_install_root_requires_known_scope(workspace):
    with pytest.raises(ValueError, match="scope"):
        extension_install_root("team", str(workspace))
    with pytest.raises(ValueError, match="workspace"):
        extension_install_root("local", "")
