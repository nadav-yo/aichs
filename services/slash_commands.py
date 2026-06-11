"""Built-in composer slash commands (not skills)."""

from dataclasses import dataclass

from services.tool_registry import extension_command, extension_commands
from storage.settings import DEFAULT_ARCHIVIST_PROMPT, SettingsStore, archivist_prompt


@dataclass(frozen=True)
class SlashCommand:
    name: str
    description: str
    prompt: str = ""
    tools: list[str] | None = None
    source: str = "builtin"
    executable: bool = False
    capabilities: list[str] | None = None


_ARCHIVIST_PROMPT = DEFAULT_ARCHIVIST_PROMPT
_EXECUTABLE_BUILTIN_NAMES = {"compact", "reload"}
_BUILTIN_DESCRIPTIONS = {
    "compact": "Summarize older messages to free context",
    "reload": "Reload skills and extensions",
    "archivist": "Use saved chat memory and exact dropped chat references",
}
_ARCHIVIST_TOOLS = ["search_project_chats", "read_project_chat"]


def _builtin_commands(*, load_settings: bool = True) -> list[SlashCommand]:
    archivist = _archivist_command(load_settings=load_settings)
    return [
        SlashCommand("compact", _BUILTIN_DESCRIPTIONS["compact"], executable=True),
        SlashCommand("reload", _BUILTIN_DESCRIPTIONS["reload"], executable=True),
        archivist,
    ]


def _archivist_command(*, load_settings: bool = True) -> SlashCommand:
    prompt = archivist_prompt(SettingsStore().load()) if load_settings else _ARCHIVIST_PROMPT
    return SlashCommand(
        "archivist",
        _BUILTIN_DESCRIPTIONS["archivist"],
        prompt=prompt,
        tools=list(_ARCHIVIST_TOOLS),
    )


BUILTIN_COMMANDS: list[SlashCommand] = _builtin_commands(load_settings=False)


def parse_builtin_command(text: str) -> str | None:
    t = text.strip().casefold()
    if not t.startswith("/"):
        return None
    name = t[1:].split()[0] if len(t) > 1 else ""
    if not name:
        return None
    return name if name in _EXECUTABLE_BUILTIN_NAMES else None


def parse_builtin_prompt_command(text: str) -> SlashCommand | None:
    t = text.strip()
    if not t.startswith("/"):
        return None
    name = t[1:].split()[0] if len(t) > 1 else ""
    if not name:
        return None
    if name.casefold() == "archivist":
        return _archivist_command(load_settings=True)
    return None


def load_all_commands(cwd: str | None = None) -> list[SlashCommand]:
    commands = _builtin_commands()
    commands.extend(
        SlashCommand(
            name=cmd.name,
            description=cmd.description,
            prompt=cmd.prompt,
            tools=cmd.tools,
            source=cmd.source,
            executable=cmd.executable,
            capabilities=list(cmd.capabilities),
        )
        for cmd in extension_commands(cwd)
    )
    return sorted(commands, key=lambda c: (c.source != "builtin", c.name))


def parse_extension_command(text: str, cwd: str | None = None) -> SlashCommand | None:
    t = text.strip()
    if not t.startswith("/"):
        return None
    name = t[1:].split()[0] if len(t) > 1 else ""
    if not name:
        return None
    cmd = extension_command(name, cwd)
    if cmd is None:
        return None
    return SlashCommand(
        name=cmd.name,
        description=cmd.description,
        prompt=cmd.prompt,
        tools=cmd.tools,
        source=cmd.source,
        executable=cmd.executable,
        capabilities=list(cmd.capabilities),
    )


def slash_invocation(text: str) -> tuple[str, str] | None:
    t = text.strip()
    if not t.startswith("/"):
        return None
    body = t[1:]
    if not body:
        return None
    parts = body.split(maxsplit=1)
    name = parts[0]
    rest = parts[1].strip() if len(parts) > 1 else ""
    return name, rest
