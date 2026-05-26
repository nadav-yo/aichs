"""Host shell tool name (single name on all platforms)."""

SHELL_TOOL_NAME = "execute"


def shell_tool_name() -> str:
    return SHELL_TOOL_NAME


def is_shell_tool(name: str) -> bool:
    return name == SHELL_TOOL_NAME
