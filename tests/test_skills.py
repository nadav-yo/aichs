def test_load_skill_from_project(workspace):
    skills_dir = workspace / ".aicc" / "skills"
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
    skills_dir = workspace / ".aicc" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "bad.md").write_text("no frontmatter\n", encoding="utf-8")
    from services.skills import load_all

    assert load_all(str(workspace)) == []
