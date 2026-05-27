EXTENSION_DESCRIPTION = "Demonstrates runtime continuation: compacting, validating a handoff, and resuming the active task."


def register(registry):
    registry.metadata(description=EXTENSION_DESCRIPTION)
    registry.command(
        name="continue",
        description="Runtime demo: compact and resume the active task",
        execute=continue_command,
        capabilities=["runtime_control", "compaction", "state"],
    )
    registry.hook("before_next_model_request", auto_continue_when_marked)
    registry.status_badge(name="continue_status", provider=continue_badge)
    registry.panel(name="continue_status", title="Continuation", provider=continue_panel)


def continue_command(ctx, args):
    mode = (args or "status").strip().lower()
    state = ctx.storage.load_state()
    if mode.startswith("status"):
        count = state.get("manual_runs", 0)
        return f"Continuation ready. Manual continuations requested: {count}."
    if mode.startswith("preview"):
        return (
            "Preview: /continue queue will request app-owned compaction, then "
            "queue `Continue the active task from the runtime continuation handoff.`"
        )
    if mode.startswith("queue") or mode.startswith("steer"):
        state["manual_runs"] = int(state.get("manual_runs", 0)) + 1
        state["last_mode"] = mode.split()[0]
        ctx.storage.save_state(state)
        ctx.runtime.continue_after_compact(
            "Continue the active task from the runtime continuation handoff.",
            force=True,
        )
        return "Continuation handoff requested."
    return "Use `/continue status` or `/continue queue`."


def auto_continue_when_marked(ctx):
    # Demo trigger: an extension can request safe mid-turn compaction after a
    # complete tool-result batch, before the next provider request.
    if not _has_marker(ctx.history, "[auto-continue]"):
        return None
    if _has_marker(ctx.history, _AUTO_RESUME_PROMPT):
        return None
    return {
        "action": "compact_and_resume",
        "force": False,
        "ledger": True,
        "reason": "runtime_continue marker",
        "resume_prompt": _AUTO_RESUME_PROMPT,
    }


def continue_badge(ctx):
    return {
        "label": "Continue",
        "tooltip": "Open continuation status",
        "tone": "accent",
        "panel": "continue_status",
    }


def continue_panel(ctx):
    return {
        "title": "Continuation",
        "body": "Runtime extension demo from .aichs/extensions/runtime_continue.py.",
        "sections": [
            {
                "heading": "Commands",
                "items": [
                    {"title": "/continue status", "subtitle": "Show extension state."},
                    {"title": "/continue preview", "subtitle": "Show what queue would request."},
                    {"title": "/continue queue", "subtitle": "Compact then queue a resume prompt."},
                ],
            },
            {
                "heading": "Mid-turn hook",
                "items": [
                    {
                        "title": "[auto-continue]",
                        "subtitle": "When present in chat history, the hook requests compact_and_resume at the next safe boundary.",
                    }
                ],
            },
        ],
    }


def _has_marker(history, marker):
    for msg in history:
        if marker in str(msg.get("content", "")):
            return True
    return False


_AUTO_RESUME_PROMPT = "Continue the active task from the validated continuation ledger."
