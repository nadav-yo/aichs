def register(registry):
    registry.tool(
        name="workflow_ping",
        description=(
            "Extension demo: return pong. Use only when the user asks to test "
            "workflow extensions are loaded."
        ),
        input_schema={
            "type": "object",
            "properties": {},
        },
        execute=workflow_ping,
        parallel_safe=True,
    )

    registry.command(
        name="careful_review",
        description="Review changes without editing files",
        prompt=(
            "Review the current workspace changes for correctness, missing tests, "
            "and risky assumptions. Do not edit files. Give concise findings first."
        ),
        tools=["read_file", "search_files", "execute"],
    )

    registry.context("Example workflow note", workflow_context)
    registry.hook("before_tool_call", block_git_push)
    registry.hook("after_tool_result", trim_noisy_search)


def workflow_ping(ctx, inputs):
    return "pong"


def workflow_context(ctx):
    return (
        "The /careful_review command is available, workflow_ping is a demo tool "
        "(returns pong), and git push is blocked through a before_tool_call hook."
    )


def block_git_push(ctx):
    if ctx.tool_name != "execute":
        return
    command = str(ctx.inputs.get("command") or "").lower()
    if "git push" in command:
        ctx.status = "error"
        ctx.output = "[tool error] git push is blocked by workflow_examples.py."


def trim_noisy_search(ctx):
    if ctx.tool_name != "search_files":
        return
    limit = 4000
    if len(ctx.output) > limit:
        ctx.output = ctx.output[:limit] + "\n\n[trimmed by workflow_examples.py]"
