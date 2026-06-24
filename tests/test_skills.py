from contextlib import contextmanager
from pathlib import Path

import config
import services.skills as skills_service


def test_load_skill_from_project(workspace):
    skills_dir = workspace / ".agents" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "review.md").write_text(
        "---\nname: review\ndescription: Code review\ntools: read_file, search_files\n---\n"
        "Review carefully.\n",
        encoding="utf-8",
    )
    from services.skills import load_all

    skills = load_all(str(workspace))
    assert len(skills) == 1
    assert skills[0].name == "review"
    assert skills[0].tools == ["read_file", "search_files"]


def test_legacy_project_aichs_skills_are_not_loaded(workspace):
    skills_dir = workspace / ".aichs" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "legacy.md").write_text("---\nname: legacy\n---\nLegacy prompt.\n", encoding="utf-8")

    from services.skills import load_all

    assert load_all(str(workspace)) == []


def test_skill_without_frontmatter_ignored(workspace):
    skills_dir = workspace / ".agents" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "bad.md").write_text("no frontmatter\n", encoding="utf-8")
    from services.skills import load_all

    assert load_all(str(workspace)) == []


def test_load_skill_from_configured_user_home(workspace):
    skills_dir = config.AICHS_HOME / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "global.md").write_text(
        "---\nname: global\ndescription: Global skill\n---\nGlobal prompt.\n",
        encoding="utf-8",
    )
    from services.skills import load_all

    skills = load_all(str(workspace))

    assert [skill.name for skill in skills] == ["global"]


def test_load_skills_uses_top_level_iterdir_and_skips_hidden(workspace, monkeypatch):
    skills_dir = workspace / ".agents" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / ".hidden.md").write_text("---\nname: hidden\n---\nHidden prompt.\n", encoding="utf-8")
    (skills_dir / "review.md").write_text("---\nname: review\n---\nReview prompt.\n", encoding="utf-8")
    (skills_dir / "notes.txt").write_text("---\nname: notes\n---\nNotes prompt.\n", encoding="utf-8")
    nested = skills_dir / "nested.md"
    nested.mkdir()
    monkeypatch.setattr(
        Path,
        "glob",
        lambda self, pattern: (_ for _ in ()).throw(
            AssertionError("skill discovery should not use glob")
        ),
    )

    loaded = skills_service.load_all(str(workspace))

    assert [skill.name for skill in loaded] == ["review"]


def test_load_skills_records_operation(workspace, monkeypatch):
    skills_dir = workspace / ".agents" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "review.md").write_text("---\nname: review\n---\nReview prompt.\n", encoding="utf-8")
    operations = []

    @contextmanager
    def fake_time_operation(operation, *, detail="", slow_ms=100.0):
        operations.append((operation, detail, slow_ms))
        yield

    monkeypatch.setattr(skills_service, "time_operation", fake_time_operation)

    loaded = skills_service.load_all(str(workspace))

    assert [skill.name for skill in loaded] == ["review"]
    assert operations == [("skills.load", f"cwd={workspace}", 100.0)]
