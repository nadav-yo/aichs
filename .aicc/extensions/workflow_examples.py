from pathlib import Path
import subprocess


def register(registry):
    registry.tool(
        name="workspace_summary",
        description="Return a compact summary of the current workspace.",
        input_schema={
            "type": "object",
            "properties": {},
        },
        execute=workspace_summary,
        parallel_safe=True,
    )

    registry.command(
        name="careful_review",
        description="Review changes without editing files",
        prompt=(
            "Review the current workspace changes for correctness, missing tests, "
            "and risky assumptions. Do not edit files. Give concise findings first."
        ),
        tools=["read_file", "search_files", "bash"],
    )

    registry.context("Example workflow note", workflow_context)
    registry.hook("before_tool_call", block_git_push)
    registry.hook("after_tool_result", trim_noisy_search)


def workspace_summary(ctx, inputs):
    root = Path(ctx.cwd).resolve()
    branch = _git_output(["git", "branch", "--show-current"], root) or "(no branch)"
    status = _git_output(["git", "status", "--short"], root)
    changed = len([line for line in status.splitlines() if line.strip()])
    return "\n".join([
        f"Workspace: {root.name}",
        f"Path: {root}",
        f"Branch: {branch}",
        f"Changed files: {changed}",
    ])


def workflow_context(ctx):
    return (
        "The /careful_review command is available, and git push is blocked "
        "through a before_tool_call hook."
    )


def block_git_push(ctx):
    if ctx.tool_name != "bash":
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


def _git_output(args, cwd: Path):
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()
