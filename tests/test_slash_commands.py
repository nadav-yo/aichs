import pytest

from services.slash_commands import (
    BUILTIN_COMMANDS,
    load_all_commands,
    parse_builtin_command,
    parse_extension_command,
    slash_invocation,
)


def test_builtin_names():
    names = {cmd.name for cmd in BUILTIN_COMMANDS}
    assert names == {"compact", "reload"}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/compact", "compact"),
        ("/RELOAD extra", "reload"),
        ("  /compact  ", "compact"),
        ("/unknown", None),
        ("not a command", None),
        ("/", None),
        ("", None),
    ],
)
def test_parse_builtin_command(text, expected):
    assert parse_builtin_command(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/demo args", ("demo", "args")),
        ("/demo", ("demo", "")),
        ("  /foo   bar  ", ("foo", "bar")),
        ("hello", None),
        ("/", None),
    ],
)
def test_slash_invocation(text, expected):
    assert slash_invocation(text) == expected


def test_load_all_commands_includes_builtins(workspace):
    names = {c.name for c in load_all_commands(str(workspace)) if c.source == "builtin"}
    assert names == {"compact", "reload"}


def test_load_all_commands_includes_extension(workspace_with_extension):
    commands = load_all_commands(str(workspace_with_extension))
    ext = next(c for c in commands if c.name == "demo_cmd")
    assert ext.source == "extension"
    assert ext.prompt == "Run the demo workflow"


def test_parse_extension_command(workspace_with_extension):
    cwd = str(workspace_with_extension)
    cmd = parse_extension_command("/demo_cmd", cwd)
    assert cmd is not None
    assert cmd.name == "demo_cmd"
    assert cmd.source == "extension"


def test_parse_extension_command_guards(workspace_with_extension):
    cwd = str(workspace_with_extension)
    assert parse_extension_command("not-a-command", cwd) is None
    assert parse_extension_command("/", cwd) is None
    assert parse_extension_command("/unknown_cmd", cwd) is None


def test_extension_commands_sorted_after_builtins(workspace_with_extension):
    commands = load_all_commands(str(workspace_with_extension))
    builtin_idx = next(i for i, c in enumerate(commands) if c.name == "compact")
    ext_idx = next(i for i, c in enumerate(commands) if c.name == "demo_cmd")
    assert builtin_idx < ext_idx
