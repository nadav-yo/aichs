from pathlib import Path

import pytest

from services.tool_registry import (
    CommandContext,
    ExtensionStorage,
    ExtensionContext,
    HookContext,
    RuntimeCommandApi,
    RuntimeDirective,
    ToolContext,
    ToolRegistry,
    extension_context_snippets,
    extension_errors,
    extension_overview,
    is_extension_disabled,
    load_extensions,
    run_extension_command,
    run_extension_hooks,
    set_extension_enabled,
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


def test_tool_context_cancel_check():
    class Cancel:
        def is_set(self):
            return True

    assert ToolContext(cwd=".", cancel=Cancel()).is_cancelled() is True
    assert ToolContext(cwd=".").is_cancelled() is False


def test_registry_validation_errors():
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="invalid tool name"):
        registry.tool(name="bad-name!", description="bad", input_schema={}, execute=lambda c, i: "")
    registry.tool(name="same", description="ok", input_schema={}, execute=lambda c, i: "")
    with pytest.raises(ValueError, match="tool already registered"):
        registry.tool(name="same", description="ok", input_schema={}, execute=lambda c, i: "")
    with pytest.raises(ValueError, match="invalid command name"):
        registry.command(name="bad-name!", description="bad")
    registry.command(name="cmd", description="ok")
    with pytest.raises(ValueError, match="command already registered"):
        registry.command(name="cmd", description="ok")
    with pytest.raises(ValueError, match="context name"):
        registry.context("", lambda ctx: "")
    with pytest.raises(ValueError, match="hook event"):
        registry.hook("", lambda ctx: None)
    with pytest.raises(ValueError, match="invalid status badge"):
        registry.status_badge(name="bad-name!", provider=lambda ctx: {})
    registry.status_badge(name="badge", provider=lambda ctx: {})
    with pytest.raises(ValueError, match="status badge already registered"):
        registry.status_badge(name="badge", provider=lambda ctx: {})
    with pytest.raises(ValueError, match="invalid panel"):
        registry.panel(name="bad-name!", title="Bad", provider=lambda ctx: {})
    registry.panel(name="panel", title="Panel", provider=lambda ctx: {})
    with pytest.raises(ValueError, match="panel already registered"):
        registry.panel(name="panel", title="Panel", provider=lambda ctx: {})


def test_runtime_command_api_callbacks():
    calls = []
    api = RuntimeCommandApi(
        show_notice=lambda text: calls.append(("notice", text)),
        send_message=lambda text: calls.append(("send", text)),
        enqueue_message=lambda text: calls.append(("enqueue", text)),
        compact_now=lambda force: calls.append(("compact", force)),
        compact_and_resume=lambda prompt, force: calls.append(("continue", prompt, force)),
    )
    api.notice("n")
    api.send("s")
    api.enqueue("q")
    api.compact(force=False)
    api.continue_after_compact("r", force=True)
    assert calls == [
        ("notice", "n"),
        ("send", "s"),
        ("enqueue", "q"),
        ("compact", False),
        ("continue", "r", True),
    ]


def test_extension_storage_config_and_state(workspace):
    storage = ExtensionStorage(str(workspace), "../unsafe id", "conv/1")
    storage.save_config({"enabled": True})
    storage.save_config({"global": True}, scope="global")
    storage.save_state({"ok": True}, name="latest")
    assert storage.load_config()["enabled"] is True
    assert storage.load_config(scope="global")["global"] is True
    assert storage.load_state("latest")["ok"] is True
    with pytest.raises(ValueError, match="scope"):
        storage.load_config(scope="bad")
    with pytest.raises(ValueError, match="JSON object"):
        storage.save_state([])


def test_command_context_storage_uses_extension_id(workspace):
    ctx = CommandContext(cwd=str(workspace), extension_id="my ext", conversation_id="c1")
    ctx.storage.save_state({"x": 1})
    assert (workspace / ".aichs" / "state" / "my_ext" / "c1-state.json").exists()


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
            registry.metadata(description="Project-local ping tools.")
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
    assert by_name["tooling.py"].description == "Project-local ping tools."
    assert by_name["broken.py"].status == "Failed"
    assert any(t.name == "ping" for t in by_name["tooling.py"].tools)


def test_extension_overview_reads_static_description_for_disabled_file(workspace):
    ext = write_extension(
        workspace,
        "static_description.py",
        '''
        """Docstring fallback."""
        EXTENSION_DESCRIPTION = "Safe static description."

        def register(registry):
            raise RuntimeError("must not execute while disabled")
        ''',
    )
    cwd = str(workspace)
    set_extension_enabled(ext, False, cwd)

    overview = extension_overview(cwd)

    assert overview.files[0].status == "Disabled"
    assert overview.files[0].description == "Safe static description."


def test_extension_overview_uses_docstring_description_fallback(workspace):
    write_extension(
        workspace,
        "docstring.py",
        '''
        """Docstring description."""

        def register(registry):
            pass
        ''',
    )

    overview = extension_overview(str(workspace))

    assert overview.files[0].description == "Docstring description."


def test_disabled_extension_is_visible_but_not_loaded(workspace):
    ext = write_extension(
        workspace,
        "disabled.py",
        """
        def register(registry):
            registry.tool(
                name="disabled_ping",
                description="Should not load",
                input_schema={"type": "object", "properties": {}},
                execute=lambda ctx, inputs: "pong",
            )
            registry.command(name="disabled_cmd", description="Should not load")
            registry.context("Disabled note", lambda ctx: "hidden")
            registry.hook("turn_start", lambda ctx: setattr(ctx, "error", "hidden"))
        """,
    )
    cwd = str(workspace)

    assert not is_extension_disabled(ext, cwd)
    set_extension_enabled(ext, False, cwd)

    assert is_extension_disabled(ext, cwd)
    registry = ToolRegistry()
    load_extensions(registry, cwd)
    assert registry.get("disabled_ping") is None
    assert extension_errors(cwd) == []
    snippets, errors = extension_context_snippets(cwd)
    assert snippets == []
    assert errors == []

    overview = extension_overview(cwd)
    summary = overview.files[0]
    assert summary.status == "Disabled"
    assert summary.tools == []
    assert summary.commands == []
    assert summary.hooks == []

    set_extension_enabled(ext, True, cwd)
    assert not is_extension_disabled(ext, cwd)
    registry = ToolRegistry()
    load_extensions(registry, cwd)
    assert registry.get("disabled_ping") is not None


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


def test_hook_can_return_runtime_directive(workspace):
    write_extension(
        workspace,
        "directives.py",
        """
        def register(registry):
            registry.hook("before_next_model_request", on_next)

        def on_next(ctx):
            return {"action": "compact_and_resume", "resume_prompt": "keep going"}
        """,
    )
    cwd = str(workspace)
    ctx = HookContext(event="before_next_model_request", cwd=cwd)
    errors = run_extension_hooks(cwd, "before_next_model_request", ctx)
    assert errors == []
    assert ctx.directives[0].action == "compact_and_resume"
    assert ctx.directives[0].params["resume_prompt"] == "keep going"


def test_hook_can_return_runtime_directive_list(workspace):
    write_extension(
        workspace,
        "directive_list.py",
        """
        from services.tool_registry import RuntimeDirective

        def register(registry):
            registry.hook("before_next_model_request", on_next)

        def on_next(ctx):
            return [
                RuntimeDirective("show_notice", {"text": "one"}),
                {"action": "enqueue_message", "text": "two"},
            ]
        """,
    )
    ctx = HookContext(event="before_next_model_request", cwd=str(workspace))
    errors = run_extension_hooks(str(workspace), "before_next_model_request", ctx)
    assert errors == []
    assert [d.action for d in ctx.directives] == ["show_notice", "enqueue_message"]


def test_hook_context_helper_adds_runtime_directive():
    ctx = HookContext(event="before_next_model_request", cwd=".")
    directive = ctx.compact_and_resume(resume_prompt="resume", force=True)
    assert directive in ctx.directives
    assert directive.params["force"] is True


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


def test_executable_extension_command_runs_with_runtime_and_storage(workspace):
    write_extension(
        workspace,
        "continue_ext.py",
        """
        def register(registry):
            registry.command(
                name="continue",
                description="Continue from compacted context",
                execute=run,
                capabilities=["runtime_control", "compaction"],
            )

        def run(ctx, args):
            state = ctx.storage.load_state()
            state["last_args"] = args
            ctx.storage.save_state(state)
            ctx.runtime.notice(f"continuing: {args}")
            return {"notice": "done"}
        """,
    )
    seen = []
    result, errors = run_extension_command(
        str(workspace),
        "continue",
        "status",
        runtime=RuntimeCommandApi(show_notice=seen.append),
    )
    assert errors == []
    assert result == {"notice": "done"}
    assert seen == ["continuing: status"]
    state_path = workspace / ".aichs" / "state" / "continue_ext" / "state.json"
    assert "status" in state_path.read_text(encoding="utf-8")


def test_run_extension_command_errors(workspace_with_extension, workspace):
    result, errors = run_extension_command(str(workspace), "missing")
    assert result is None
    assert errors == ["command not found: missing"]

    result, errors = run_extension_command(str(workspace_with_extension), "demo_cmd")
    assert result is None
    assert errors == ["command is prompt-only: demo_cmd"]

    write_extension(
        workspace,
        "boom.py",
        """
        def register(registry):
            registry.command(name="boom", description="Boom", execute=lambda ctx, args: 1 / 0)
        """,
    )
    result, errors = run_extension_command(str(workspace), "boom")
    assert result is None
    assert "command boom" in errors[-1]


def test_extension_overview_marks_executable_command_capabilities(workspace):
    write_extension(
        workspace,
        "command.py",
        """
        def register(registry):
            registry.command(
                name="run_me",
                description="Executable",
                execute=lambda ctx, args: "ok",
                capabilities=["runtime_control"],
            )
        """,
    )
    overview = extension_overview(str(workspace))
    command = overview.files[0].commands[0]
    assert command.executable is True
    assert command.capabilities == ["runtime_control"]
