"""Built-in composer slash commands (not skills)."""

from dataclasses import dataclass

from services.tool_registry import extension_command, extension_commands


@dataclass(frozen=True)
class SlashCommand:
    name: str
    description: str
    prompt: str = ""
    tools: list[str] | None = None
    source: str = "builtin"
    executable: bool = False
    capabilities: list[str] | None = None


BUILTIN_COMMANDS: list[SlashCommand] = [
    SlashCommand("compact", "Summarize older messages to free context"),
    SlashCommand("reload", "Reload skills and extensions"),
]


def parse_builtin_command(text: str) -> str | None:
    t = text.strip().casefold()
    if not t.startswith("/"):
        return None
    name = t[1:].split()[0] if len(t) > 1 else ""
    if not name:
        return None
    for cmd in BUILTIN_COMMANDS:
        if cmd.name == name:
            return cmd.name
    return None


def load_all_commands(cwd: str | None = None) -> list[SlashCommand]:
    commands = list(BUILTIN_COMMANDS)
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
