from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.chat import ChatThread, _serialize_anthropic
from services.crew import ASK_CREW_TOOL_NAME
from services.tool_policy import ConversationToolPolicy, ToolApprovalBus


def test_serialize_anthropic_blocks():
    blocks = [
        SimpleNamespace(type="text", text="hello"),
        SimpleNamespace(type="tool_use", id="tu_1", name="read_file", input={"path": "a.py"}),
    ]
    out = _serialize_anthropic(blocks)
    assert out[0] == {"type": "text", "text": "hello"}
    assert out[1]["type"] == "tool_use"
    assert out[1]["name"] == "read_file"


def test_chat_thread_filters_tools(workspace, qapp):
    thread = ChatThread(
        "claude-sonnet-4-6",
        [],
        "system",
        str(workspace),
        allowed_tools=["read_file"],
    )
    names = {t["name"] for t in thread._tools_anthropic()}
    assert names == {"read_file"}


def test_chat_thread_exposes_ask_crew_tool_by_default(workspace, qapp):
    thread = ChatThread("claude-sonnet-4-6", [], "system", str(workspace))
    names = {t["name"] for t in thread._tools_anthropic()}
    assert ASK_CREW_TOOL_NAME in names

    crew_thread = ChatThread(
        "claude-sonnet-4-6",
        [],
        "system",
        str(workspace),
        enable_crew_tool=False,
    )
    names = {t["name"] for t in crew_thread._tools_anthropic()}
    assert ASK_CREW_TOOL_NAME not in names


def test_emit_chunk_buffering(qapp, workspace):
    thread = ChatThread("claude-sonnet-4-6", [], "sys", str(workspace))
    chunks = []
    thread.chunk.connect(chunks.append)
    thread._emit_chunk("a" * 600, force=True)
    assert len(chunks) == 1
    assert len(chunks[0]) >= 600


def test_check_tool_gate_extension_once(workspace, qapp):
    from tests.conftest import write_extension

    write_extension(
        workspace,
        "gated.py",
        """
        def register(registry):
            registry.tool(
                name="gated",
                description="needs approval",
                input_schema={"type": "object", "properties": {}},
                execute=lambda ctx, inputs: "ok",
                approval="once",
            )
        """,
    )
    bus = ToolApprovalBus()
    policy = ConversationToolPolicy()
    thread = ChatThread(
        "claude-sonnet-4-6",
        [],
        "sys",
        str(workspace),
        tool_policy=policy,
        approval_bus=bus,
    )

    def on_needed(pending):
        bus.complete(pending, approved=True, grant_extension_tool=True)

    bus.approval_needed.connect(on_needed)
    blocked = thread._check_tool_gate("gated", {})
    assert blocked is None
    assert "gated" in policy.approved_extension_tools


def test_execute_tools_parallel_reads(workspace, qapp):
    thread = ChatThread("claude-sonnet-4-6", [], "sys", str(workspace))
    tools = [
        ("id1", "read_file", {"path": "src/main.py"}),
        ("id2", "read_file", {"path": "src/main.py"}),
    ]
    results = thread._execute_tools(tools)
    assert len(results) == 2
    assert all("print" in r[2] for r in results)


def test_write_scope_limits_edit_file_to_tests(workspace, qapp):
    thread = ChatThread(
        "claude-sonnet-4-6",
        [],
        "sys",
        str(workspace),
        write_roots=["tests"],
    )
    blocked = thread._check_tool_scope("edit_file", {"path": "src/main.py"})
    allowed = thread._check_tool_scope("edit_file", {"path": "tests/test_x.py"})
    assert "limited to: tests" in blocked
    assert allowed is None


def test_execute_ask_crew_runs_nested_member(workspace, qapp):
    thread = ChatThread("claude-sonnet-4-6", [], "sys", str(workspace))
    started = []
    done = []
    thread.crew_started.connect(started.append)
    thread.crew_done.connect(lambda meta, text: done.append((meta, text)))

    with patch.object(ChatThread, "_loop_anthropic", return_value="found evidence"):
        output = thread._execute_ask_crew({
            "member": "scout",
            "task": "check extension loading",
            "reason": "need evidence",
        })

    assert output == "Scout: found evidence"
    assert started[0]["id"] == "scout"
    assert done[0][1] == "found evidence"


def test_execute_ask_crew_validates_inputs(workspace, qapp):
    thread = ChatThread("claude-sonnet-4-6", [], "sys", str(workspace))
    assert "Unknown crew" in thread._execute_ask_crew({"member": "nope", "task": "x"})
    assert "requires a focused task" in thread._execute_ask_crew({"member": "scout"})


def test_execute_ask_crew_respects_disabled_member(workspace, qapp):
    thread = ChatThread(
        "claude-sonnet-4-6",
        [],
        "sys",
        str(workspace),
        crew_settings={"crew": {"scout": {"enabled": False}}},
    )
    out = thread._execute_ask_crew({"member": "scout", "task": "check"})
    assert "Scout is disabled" in out
