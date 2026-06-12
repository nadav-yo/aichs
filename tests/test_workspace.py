
from services.workspace import agents_md, build_system, system_parts
from tests.conftest import write_extension


def test_agents_md_missing(workspace):
    assert agents_md(str(workspace)) is None


def test_agents_md_present(workspace):
    (workspace / "AGENTS.md").write_text("Always run tests.\n", encoding="utf-8")
    p = agents_md(str(workspace))
    assert p is not None
    assert p.name == "AGENTS.md"


def test_system_parts_includes_cwd_and_tool_guidance_without_repo_snapshot(workspace):
    base, agents, ctx, extensions = system_parts(str(workspace))
    assert "senior coding agent" in base.lower() or "coding agent" in base.lower()
    assert agents == ""
    assert extensions == ""
    assert str(workspace) in ctx
    assert "use list_files/search_files first" in ctx
    assert "File tree:" not in ctx
    assert "Git status:" not in ctx
    assert "Recent commits:" not in ctx


def test_build_system_includes_agents_section(workspace):
    (workspace / "AGENTS.md").write_text("Project rule: be careful.\n", encoding="utf-8")
    text = build_system(str(workspace))
    assert "AGENTS.md" in text
    assert "Project Instructions" in text
    assert "Project Memory" not in text
    assert "Use the following project context silently" in text
    assert "Project rule" in text
    assert "Workspace" in text


def test_build_context_includes_extension_snippet(workspace):
    write_extension(
        workspace,
        "ctx.py",
        """
        def register(registry):
            registry.context("Build note", lambda ctx: "tests are good")
        """,
    )
    _, _, ctx, extensions = system_parts(str(workspace))
    assert "Build note" not in ctx
    assert "Build note" in extensions
    assert "tests are good" in extensions
    assert "Extension Context" in build_system(str(workspace))
