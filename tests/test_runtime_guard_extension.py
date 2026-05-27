import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_runtime_guard_module():
    path = Path(__file__).parents[1] / ".aichs" / "extensions" / "runtime_guard.py"
    spec = importlib.util.spec_from_file_location("aichs_test_runtime_guard", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recent_tool_errors_reads_openai_and_anthropic_shapes():
    module = _load_runtime_guard_module()
    history = [
        {"role": "tool", "content": "[tool error] nope"},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "content": "[tool error] nope",
                }
            ],
        },
    ]
    assert module._recent_tool_errors(history) == ["[tool error] nope", "[tool error] nope"]
    assert module._repeated_error(module._recent_tool_errors(history)) == "[tool error] nope"


def test_recent_tool_errors_reads_repeated_missing_shell_command():
    module = _load_runtime_guard_module()
    powershell_failure = (
        "this_command_does_not_exist: The term 'this_command_does_not_exist' "
        "is not recognized as a name of a cmdlet, function, script file, or "
        "executable program. [exit 1]"
    )
    history = [
        {"role": "tool", "content": powershell_failure},
        {"role": "tool", "content": powershell_failure},
    ]

    errors = module._recent_tool_errors(history)

    assert errors == [powershell_failure, powershell_failure]
    assert module._repeated_error(errors) == powershell_failure


def test_recent_tool_errors_reads_anthropic_missing_shell_command():
    module = _load_runtime_guard_module()
    shell_failure = "sh: nope: command not found [exit 127]"
    history = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "content": shell_failure,
                },
                {"type": "text", "text": "Active task: continue"},
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "content": shell_failure,
                },
            ],
        },
    ]

    errors = module._recent_tool_errors(history)

    assert errors == [shell_failure, shell_failure]
    assert module._repeated_error(errors) == shell_failure


def test_stop_repeated_tool_errors_blocks():
    module = _load_runtime_guard_module()
    ctx = SimpleNamespace(history=[
        {"role": "tool", "content": "[tool error] same"},
        {"role": "tool", "content": "[tool error] same"},
    ])
    directives = module.stop_repeated_tool_errors(ctx)
    assert [item["action"] for item in directives] == ["show_notice", "enqueue_message"]
    assert "Do not retry" in directives[1]["text"]
    assert "[tool error] same" in directives[1]["text"]


def test_stop_repeated_tool_errors_blocks_non_adjacent_repeat():
    module = _load_runtime_guard_module()
    ctx = SimpleNamespace(history=[
        {"role": "tool", "content": "[tool error] same"},
        {"role": "assistant", "content": "thinking"},
        {"role": "tool", "content": "[tool error] different"},
        {"role": "user", "content": "normal"},
        {"role": "tool", "content": "[tool error] same"},
    ])

    directives = module.stop_repeated_tool_errors(ctx)

    assert [item["action"] for item in directives] == ["show_notice", "enqueue_message"]
    assert "[tool error] same" in directives[1]["text"]


def test_stop_repeated_tool_errors_blocks_batch_repeat():
    module = _load_runtime_guard_module()
    ctx = SimpleNamespace(history=[
        {
            "role": "user",
            "synthetic": "tool_results",
            "content": [
                {"type": "tool_result", "content": "[tool error] same"},
                {"type": "tool_result", "content": "[tool error] same"},
            ],
        },
    ])

    directives = module.stop_repeated_tool_errors(ctx)

    assert [item["action"] for item in directives] == ["show_notice", "enqueue_message"]
    assert "[tool error] same" in directives[1]["text"]


def test_recent_tool_errors_scans_beyond_short_tail():
    module = _load_runtime_guard_module()
    history = [{"role": "tool", "content": "[tool error] same"}]
    history.extend({"role": "assistant", "content": f"filler {i}"} for i in range(10))
    history.append({"role": "tool", "content": "[tool error] same"})

    errors = module._recent_tool_errors(history)

    assert errors.count("[tool error] same") == 2
    assert module._repeated_error(errors) == "[tool error] same"


def test_guard_command_status_without_repeat():
    module = _load_runtime_guard_module()
    ctx = SimpleNamespace(history=[{"role": "tool", "content": "[tool error] one"}])
    assert "no repeat yet" in module.guard_command(ctx, "status")


def test_guard_panel_lists_errors():
    module = _load_runtime_guard_module()
    ctx = SimpleNamespace(history=[{"role": "tool", "content": "[tool error] one"}])
    panel = module.guard_panel(ctx)
    assert panel["title"] == "Runtime Guard"
    assert panel["sections"][1]["items"][0]["title"] == "[tool error] one"
