from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.chat import ChatThread, _active_task_preview, _serialize_anthropic
from services.crew import ASK_CREW_TOOL_NAME, get_crew_member
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


def test_active_task_preview_skips_tool_result_turns():
    history = [
        {"role": "user", "content": "summarize docs and compare skills"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "1"}]},
        {
            "role": "user",
            "synthetic": "tool_results",
            "content": [
                {"type": "tool_result", "tool_use_id": "1", "content": "docs"},
                {"type": "text", "text": "Continue the active user task."},
            ],
        },
    ]
    assert _active_task_preview(history) == "summarize docs and compare skills"


def test_active_task_preview_skips_runtime_synthetic_messages():
    history = [
        {"role": "user", "content": "read missing file"},
        {"role": "user", "content": "guard instruction", "synthetic": "extension"},
        {"role": "user", "content": "resume", "synthetic": "extension_resume"},
        {"role": "user", "content": "anchor", "synthetic": "active_task"},
    ]
    assert _active_task_preview(history) == "read missing file"


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
    assert "search_project_chats" not in names
    assert "read_project_chat" not in names

    crew_thread = ChatThread(
        "claude-sonnet-4-6",
        [],
        "system",
        str(workspace),
        enable_crew_tool=False,
    )
    names = {t["name"] for t in crew_thread._tools_anthropic()}
    assert ASK_CREW_TOOL_NAME not in names


def test_archivist_gets_project_chat_memory_tool(workspace, qapp):
    archivist = get_crew_member("archivist")
    thread = ChatThread(
        "claude-sonnet-4-6",
        [],
        "system",
        str(workspace),
        allowed_tools=list(archivist.tools),
        enable_crew_tool=False,
    )
    names = {t["name"] for t in thread._tools_anthropic()}
    assert "search_project_chats" in names
    assert "read_project_chat" in names


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


def test_execute_ask_crew_sends_small_context_to_scout(workspace, qapp):
    history = [{"role": "user", "content": f"old {i}"} for i in range(12)]
    thread = ChatThread("claude-sonnet-4-6", history, "sys", str(workspace))
    seen = []

    def fake_loop(nested):
        seen.append(list(nested.history))
        return "ok"

    with patch.object(ChatThread, "_loop_anthropic", fake_loop):
        thread._execute_ask_crew({"member": "scout", "task": "check"})

    assert len(seen[0]) < len(history)
    assert seen[0][-1]["content"].startswith("Scout, answer this focused crew request")


def test_execute_ask_crew_archivist_uses_memory_lookup_without_model(workspace, qapp):
    thread = ChatThread("claude-sonnet-4-6", [], "sys", str(workspace))
    tool_calls = []
    done = []
    thread.tool_called.connect(lambda name, inputs: tool_calls.append((name, inputs)))
    thread.crew_done.connect(lambda meta, text: done.append((meta, text)))

    with patch.object(ChatThread, "_loop_anthropic", side_effect=AssertionError("no model")):
        output = thread._execute_ask_crew({"member": "archivist", "task": "playwright"})

    assert output.startswith("Archivist:")
    assert tool_calls == [("search_project_chats", {"query": "playwright"})]
    assert done[0][0]["id"] == "archivist"


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


def test_execute_ask_crew_limits_calls_per_turn(workspace, qapp):
    thread = ChatThread("claude-sonnet-4-6", [], "sys", str(workspace))
    with patch.object(ChatThread, "_loop_anthropic", return_value="ok"):
        assert thread._execute_ask_crew({"member": "scout", "task": "one"}).startswith("Scout:")
        assert thread._execute_ask_crew({"member": "archivist", "task": "two"}).startswith("Archivist:")
        assert "limited to two" in thread._execute_ask_crew({"member": "scout", "task": "three"})
