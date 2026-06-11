import pytest

from services.slash_commands import (
    BUILTIN_COMMANDS,
    load_all_commands,
    parse_builtin_command,
    parse_builtin_prompt_command,
    parse_extension_command,
    slash_invocation,
)
from storage.settings import ARCHIVIST_PROMPT_KEY, SettingsStore


def test_builtin_names():
    names = {cmd.name for cmd in BUILTIN_COMMANDS}
    assert names == {"archivist", "compact", "reload"}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/compact", "compact"),
        ("/RELOAD extra", "reload"),
        ("  /compact  ", "compact"),
        ("/archivist notes", None),
        ("/unknown", None),
        ("not a command", None),
        ("/", None),
        ("", None),
    ],
)
def test_parse_builtin_command(text, expected):
    assert parse_builtin_command(text) == expected


def test_parse_executable_builtin_command_does_not_load_settings(monkeypatch):
    monkeypatch.setattr(
        "services.slash_commands.SettingsStore.load",
        lambda *_args: (_ for _ in ()).throw(AssertionError("settings should not load")),
    )

    assert parse_builtin_command("/compact") == "compact"
    assert parse_builtin_command("/reload") == "reload"


def test_parse_builtin_prompt_command():
    cmd = parse_builtin_prompt_command("/archivist what did we decide?")
    assert cmd is not None
    assert cmd.name == "archivist"
    assert cmd.tools == ["search_project_chats", "read_project_chat"]
    assert parse_builtin_prompt_command("/compact") is None


def test_archivist_prompt_uses_settings_without_changing_tools():
    SettingsStore().save({ARCHIVIST_PROMPT_KEY: "Search the durable project memory."})

    cmd = parse_builtin_prompt_command("/archivist what did we decide?")

    assert cmd is not None
    assert cmd.prompt == "Search the durable project memory."
    assert cmd.tools == ["search_project_chats", "read_project_chat"]


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
    assert names == {"archivist", "compact", "reload"}


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
    assert cmd.executable is False


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


def test_executable_extension_command_metadata(workspace):
    from tests.conftest import write_extension

    write_extension(
        workspace,
        "runtime.py",
        """
        def register(registry):
            registry.command(
                name="continue",
                description="Runtime continuation",
                execute=lambda ctx, args: "ok",
                capabilities=["runtime_control"],
            )
        """,
    )
    cmd = parse_extension_command("/continue status", str(workspace))
    assert cmd is not None
    assert cmd.executable is True
    assert cmd.capabilities == ["runtime_control"]
