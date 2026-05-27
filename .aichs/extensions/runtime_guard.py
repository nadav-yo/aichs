EXTENSION_DESCRIPTION = "Detects repeated tool failures and steers the model away from retry loops."

_RECENT_HISTORY_WINDOW = 32
_RECENT_ERROR_LIMIT = 12


def register(registry):
    registry.metadata(description=EXTENSION_DESCRIPTION)
    registry.command(
        name="guard",
        description="Runtime demo: stop repeated tool-error loops",
        execute=guard_command,
        capabilities=["runtime_control"],
    )
    registry.hook("before_next_model_request", stop_repeated_tool_errors)
    registry.status_badge(name="runtime_guard", provider=guard_badge)
    registry.panel(name="runtime_guard", title="Runtime Guard", provider=guard_panel)


def guard_command(ctx, args):
    errors = _recent_tool_errors(ctx.history)
    if not errors:
        return "Runtime guard is active. No recent tool failures found."
    repeated = _repeated_error(errors)
    if repeated:
        return f"Runtime guard would block repeated tool failure: {repeated}"
    return f"Runtime guard saw {len(errors)} recent tool failure(s), no repeat yet."


def stop_repeated_tool_errors(ctx):
    repeated = _repeated_error(_recent_tool_errors(ctx.history))
    if not repeated:
        return None
    return [
        {
            "action": "show_notice",
            "text": "Runtime guard steered a repeated tool-error loop.",
        },
        {
            "action": "enqueue_message",
            "text": (
                "Runtime guard detected the same tool failure twice. "
                "Do not retry the same failing tool call. Summarize the blocker "
                "for the user and explain what would be needed to proceed.\n\n"
                f"Repeated failure: {repeated}"
            ),
        },
    ]


def guard_badge(ctx):
    repeated = _repeated_error(_recent_tool_errors(ctx.history))
    if repeated:
        return {
            "label": "Guard",
            "tooltip": "Repeated tool error detected",
            "tone": "warning",
            "panel": "runtime_guard",
        }
    return {
        "label": "Guard",
        "tooltip": "Runtime guard is watching tool-error loops",
        "tone": "",
        "panel": "runtime_guard",
    }


def guard_panel(ctx):
    errors = _recent_tool_errors(ctx.history)
    repeated = _repeated_error(errors)
    status = "Repeated tool error detected." if repeated else "No repeated tool error detected."
    return {
        "title": "Runtime Guard",
        "body": "Small runtime-control demo from .aichs/extensions/runtime_guard.py.",
        "sections": [
            {
                "heading": "Status",
                "items": [
                    {"title": status, "subtitle": repeated or ""},
                    {"title": "/guard status", "subtitle": "Explain the current guard state."},
                ],
            },
            {
                "heading": "Recent tool errors",
                "items": [{"title": error} for error in errors[-5:]] or [{"title": "None"}],
            },
        ],
    }


def _repeated_error(errors):
    if len(errors) < 2:
        return ""
    counts = {}
    for error in errors:
        counts[error] = counts.get(error, 0) + 1
        if counts[error] >= 2:
            return error
    return ""


def _recent_tool_errors(history):
    errors = []
    for msg in history[-_RECENT_HISTORY_WINDOW:]:
        for text in _tool_result_texts(msg):
            normalized = _normalize_error(text)
            if normalized:
                errors.append(normalized)
    return errors[-_RECENT_ERROR_LIMIT:]


def _tool_result_texts(msg):
    if msg.get("role") == "tool":
        return [str(msg.get("content") or "")]
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    texts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            texts.append(str(block.get("content") or ""))
    return texts


def _normalize_error(text):
    text = " ".join(str(text or "").split())
    lowered = text.lower()
    if lowered.startswith("[tool error]"):
        return text[:240]
    if _looks_like_failed_shell_command(lowered):
        return text[:240]
    return ""


def _looks_like_failed_shell_command(text):
    if "[exit " not in text:
        return False
    markers = (
        "not recognized",
        "command not found",
        "not found",
        "no such file or directory",
        "not installed",
        "cannot find",
        "could not find",
    )
    return any(marker in text for marker in markers)
