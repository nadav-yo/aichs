import config


def test_load_skill_from_project(workspace):
    skills_dir = workspace / ".aichs" / "skills"
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


def test_skill_without_frontmatter_ignored(workspace):
    skills_dir = workspace / ".aichs" / "skills"
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
