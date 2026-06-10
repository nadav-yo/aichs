from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ShortcutBinding:
    action: str
    sequences: tuple[str, ...]


DEFAULT_SHORTCUTS: dict[str, ShortcutBinding] = {
    "command_palette": ShortcutBinding("command_palette", ("Ctrl+K", "Meta+K")),
    "file_browser": ShortcutBinding("file_browser", ("Ctrl+Shift+E", "Meta+Shift+E")),
    "file_search": ShortcutBinding("file_search", ("Ctrl+P", "Meta+P")),
    "reopen_closed_file": ShortcutBinding("reopen_closed_file", ("Ctrl+Shift+T", "Meta+Shift+T")),
    "text_search": ShortcutBinding("text_search", ("Ctrl+Shift+F", "Meta+Shift+F")),
}


def shortcut_sequences(action: str, saved: Mapping | None = None) -> tuple[str, ...]:
    shortcuts = saved.get("keyboard_shortcuts", {}) if isinstance(saved, Mapping) else {}
    custom = shortcuts.get(action) if isinstance(shortcuts, Mapping) else None
    if isinstance(custom, str):
        values = (custom,)
    elif isinstance(custom, list):
        values = tuple(str(value) for value in custom)
    else:
        binding = DEFAULT_SHORTCUTS.get(action)
        values = binding.sequences if binding else ()
    return tuple(value.strip() for value in values if str(value).strip())
