from pathlib import Path

import pytest

from services.tool_registry import (
    ExtensionContext,
    HookContext,
    ToolRegistry,
    extension_context_snippets,
    extension_errors,
    extension_overview,
    load_extensions,
    run_extension_hooks,
)
from tests.conftest import write_extension


def test_load_extension_tool_and_context(workspace_with_tool):
    cwd = str(workspace_with_tool)
    registry = ToolRegistry()
    load_extensions(registry, cwd)

    tool = registry.get("ping")
    assert tool is not None
    assert tool.source == "extension"
    assert tool.parallel_safe is True

    ctx = ExtensionContext(cwd=cwd)
    snippets, errors = extension_context_snippets(cwd)
    assert errors == []
    assert ("Ping note", "from extension") in snippets


def test_broken_extension_recorded(workspace_with_broken_extension):
    cwd = str(workspace_with_broken_extension)
    errors = extension_errors(cwd)
    assert len(errors) == 1
    assert "broken.py" in errors[0]


def test_missing_register_recorded(workspace_with_missing_register):
    cwd = str(workspace_with_missing_register)
    errors = extension_errors(cwd)
    assert len(errors) == 1
    assert "missing register(registry)" in errors[0]


def test_extension_overview(workspace_with_tool, workspace_with_broken_extension):
    cwd = str(workspace_with_broken_extension)
    write_extension(
        workspace_with_broken_extension,
        "tooling.py",
        """
        def register(registry):
            registry.tool(
                name="ping",
                description="Return pong",
                input_schema={"type": "object", "properties": {}},
                execute=lambda ctx, inputs: "pong",
            )
        """,
    )
    overview = extension_overview(cwd)
    assert overview.error_count == 1
    by_name = {Path(f.path).name: f for f in overview.files}
    assert by_name["tooling.py"].status == "Loaded"
    assert by_name["broken.py"].status == "Failed"
    assert any(t.name == "ping" for t in by_name["tooling.py"].tools)


def test_run_hooks_on_event(workspace):
    write_extension(
        workspace,
        "hooks.py",
        """
        def register(registry):
            registry.hook("before_tool_call", on_before)

        def on_before(ctx):
            if ctx.tool_name == "read_file":
                ctx.status = "error"
                ctx.error = "blocked by extension"
        """,
    )
    cwd = str(workspace)
    ctx = HookContext(
        event="before_tool_call",
        cwd=cwd,
        tool_name="read_file",
        inputs={"path": "src/main.py"},
    )
    errors = run_extension_hooks(cwd, "before_tool_call", ctx)
    assert errors == []
    assert ctx.status == "error"
    assert ctx.error == "blocked by extension"


def test_hook_handler_exception_becomes_error(workspace):
    write_extension(
        workspace,
        "bad_hook.py",
        """
        def register(registry):
            registry.hook("turn_start", boom)

        def boom(ctx):
            raise RuntimeError("hook failed")
        """,
    )
    cwd = str(workspace)
    errors = run_extension_hooks(cwd, "turn_start", HookContext(event="turn_start", cwd=cwd))
    assert len(errors) == 1
    assert "hook turn_start" in errors[0]
    assert "hook failed" in errors[0]
