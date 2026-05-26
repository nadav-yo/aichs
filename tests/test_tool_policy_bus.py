import threading

import pytest

from services.tool_policy import ConversationToolPolicy, ToolApprovalBus


@pytest.fixture
def bus(qapp):
    return ToolApprovalBus()


def _approve(bus, pending, **kwargs):
    bus.complete(pending, approved=True, **kwargs)


def test_read_file_never_blocks(bus, workspace):
    policy = ConversationToolPolicy()
    assert bus.check("read_file", {"path": "src/main.py"}, str(workspace), policy, lambda: False) is None


def test_edit_requires_approval_then_grants(bus, workspace):
    policy = ConversationToolPolicy()
    cwd = str(workspace)

    def on_needed(pending):
        _approve(bus, pending, grant_edit=True)

    bus.approval_needed.connect(on_needed)
    assert bus.check("edit_file", {"path": "src/main.py"}, cwd, policy, lambda: False) is None
    assert policy.edit_approved


def test_edit_denied(bus, workspace):
    policy = ConversationToolPolicy()
    cwd = str(workspace)

    def on_needed(pending):
        bus.complete(pending, approved=False, message="User denied.")

    bus.approval_needed.connect(on_needed)
    out = bus.check("edit_file", {"path": "src/main.py"}, cwd, policy, lambda: False)
    assert out == "User denied."


def test_bash_skip_after_grant(bus, workspace):
    policy = ConversationToolPolicy()
    cwd = str(workspace)

    def on_needed(pending):
        _approve(bus, pending, grant_bash_skip=True)

    bus.approval_needed.connect(on_needed)
    assert bus.check("execute", {"command": "echo hi"}, cwd, policy, lambda: False) is None
    assert policy.bash_skip_prompts
    assert bus.check("execute", {"command": "echo again"}, cwd, policy, lambda: False) is None


def test_extension_tool_approval(bus, workspace_with_tool):
    policy = ConversationToolPolicy()
    cwd = str(workspace_with_tool)

    def on_needed(pending):
        _approve(bus, pending, grant_extension_tool=True)

    bus.approval_needed.connect(on_needed)
    assert bus.check_extension_tool("ping", {}, cwd, policy, lambda: False) is None
    assert "ping" in policy.approved_extension_tools


def test_cancel_wait(bus, workspace):
    policy = ConversationToolPolicy()
    cwd = str(workspace)
    holder = {}

    def on_needed(pending):
        holder["pending"] = pending

    bus.approval_needed.connect(on_needed)

    def wait():
        out = bus.check("execute", {"command": "sleep 9"}, cwd, policy, lambda: True)
        holder["out"] = out

    t = threading.Thread(target=wait)
    t.start()
    t.join(timeout=2)
    bus.cancel_wait("stopped")
    t.join(timeout=2)
    assert holder.get("out") == "[cancelled]"
