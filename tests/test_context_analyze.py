from services.context_budget import analyze_context
from services.skills import Skill


def test_analyze_context_includes_agents_and_extensions(workspace):
    (workspace / "AGENTS.md").write_text("Rule one.\n", encoding="utf-8")
    from tests.conftest import write_extension

    write_extension(
        workspace,
        "ctx.py",
        """
        def register(registry):
            registry.context("Note", lambda ctx: "extra")
        """,
    )
    budget = analyze_context("gpt-5.4-nano", str(workspace), [])
    labels = [s.label for s in budget.segments]
    assert "Rules" in labels
    assert "Extensions" in labels


def test_analyze_context_segments(workspace):
    budget = analyze_context(
        "claude-sonnet-4-6",
        str(workspace),
        [{"role": "user", "content": "Hello"}],
        active_skill=Skill(name="review", description="d", prompt="Be thorough"),
    )
    labels = [s.label for s in budget.segments]
    assert "System prompt" in labels
    assert "Workspace" in labels
    assert "Tool definitions" in labels
    assert "Messages" in labels
    assert "Skills" in labels
    assert budget.used_tokens > 0
    assert budget.window_tokens > 0
