"""Built-in composer slash commands (not skills)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SlashCommand:
    name: str
    description: str


BUILTIN_COMMANDS: list[SlashCommand] = [
    SlashCommand("compact", "Summarize older messages to free context"),
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
