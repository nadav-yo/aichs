import json
import os
import urllib.error
from pathlib import Path

import pytest
from PyQt6.QtCore import QThreadPool
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QUrl
from PyQt6.QtGui import QColor, QFont, QImage, QKeyEvent, QKeySequence, QMouseEvent, QPainter, QTextCursor, QWheelEvent
from PyQt6.QtWidgets import QCheckBox, QDialog, QInputDialog, QLabel, QMenu, QMessageBox, QPushButton, QRadioButton, QSplitter, QTextEdit

from config import MODELS
from ui.main_window import MainWindow
from storage.agent_canvas import CanvasSaveRefused, canvas_path, canvas_storage_dir, load_agent_canvas, save_agent_canvas
from storage.settings import SettingsStore
from services.agent_canvas_run import GraphRunEngine
from ui.widgets.agent_canvas_file_scope import repo_path_candidates
from ui.widgets.agent_canvas import (
    AgentCanvasPanel,
    CanvasToken,
    GRAPH_AGENT_SYSTEM_PROMPT,
    GRAPH_AGENT_TOOLS,
    canvas_token_payload,
    parse_canvas_token,
)
from ui.widgets.agent_canvas_schema import input_ports, output_ports
from ui.widgets.agent_canvas_schema import _CONNECTION_RULES, _CREATION_ACTIONS
from ui.theme import agent_canvas_style
from tests.conftest import write_extension


@pytest.fixture(autouse=True)
def fast_app_theme(monkeypatch):
    monkeypatch.setattr("ui.main_window.apply_app_theme", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("ui.widgets.agent_canvas.AgentCanvasPanel.apply_appearance", lambda self: None)


@pytest.fixture
def quiet_file_language(monkeypatch):
    monkeypatch.setattr(
        "ui.widgets.file_viewer._TextFileTab._refresh_diagnostics",
        lambda self, delay_ms=None: None,
    )


def _settle_file_viewer_workers(qapp):
    qapp.processEvents()
    QThreadPool.globalInstance().waitForDone(1500)
    qapp.processEvents()


def test_canvas_token_payload_round_trips():
    token = CanvasToken("operation", "Inspect", "Find flow friction")

    parsed = parse_canvas_token(canvas_token_payload(token))

    assert parsed == token
    assert parse_canvas_token(b"not json") is None
    assert parse_canvas_token(b'{"kind":"operation","title":""}') is None


def test_agent_canvas_places_and_connects_graph_nodes(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        initial_nodes = panel.node_count()
        initial_edges = panel.edge_count()
        goal = panel.add_token_node(CanvasToken("goal", "Goal", "intent"), QPointF(0, 0))
        operation = panel.add_token_node(CanvasToken("operation", "Build", "Prototype screens"), QPointF(240, 0))

        assert panel.node_count() == initial_nodes + 2
        assert panel.connect_nodes(goal.node_id, operation.node_id) is True
        assert panel.edge_count() == initial_edges + 1
        assert panel.connect_nodes(goal.node_id, operation.node_id) is False
    finally:
        panel.close()


def test_agent_canvas_has_no_component_palette(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        assert not hasattr(panel, "_source_panel")
    finally:
        panel.close()


def test_agent_canvas_root_goal_has_distinct_color(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        root = panel.add_token_node(CanvasToken("goal", "Root", "intent"), QPointF(0, 0))
        child = panel.add_token_node(CanvasToken("goal", "Child", "subgoal"), QPointF(260, 0))
        scope = panel.add_token_node(CanvasToken("scope", "Files", "src/main.py"), QPointF(520, 0))

        assert panel.connect_nodes(root.node_id, child.node_id, "split") is True
        root_bg, root_border, root_accent = root._colors({})
        child_bg, child_border, child_accent = child._colors({})
        scope_bg, scope_border, scope_accent = scope._colors({})

        assert root.is_root_goal
        assert not child.is_root_goal
        assert root_accent == QColor("#64d6a2")
        assert (root_bg, root_border, root_accent) != (child_bg, child_border, child_accent)
        assert (root_bg, root_border, root_accent) != (scope_bg, scope_border, scope_accent)
    finally:
        panel.close()


def test_agent_canvas_root_goal_prefers_goal_without_inputs(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        original = panel.add_token_node(CanvasToken("goal", "Original", "intent"), QPointF(0, 0))
        upstream = panel.add_token_node(CanvasToken("goal", "Upstream", "new root"), QPointF(-280, -220))

        assert panel.connect_nodes(upstream.node_id, original.node_id, "split") is True

        assert upstream.is_root_goal
        assert not original.is_root_goal
        assert panel._root_goal_node() is upstream
    finally:
        panel.close()


def test_agent_canvas_all_goals_without_inputs_are_master_roots(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        original = panel.add_token_node(CanvasToken("goal", "Original root", "intent"), QPointF(-520, 0))
        first = panel.add_token_node(CanvasToken("goal", "First root", "intent"), QPointF(0, 0))
        second = panel.add_token_node(CanvasToken("goal", "Second root", "intent"), QPointF(260, 0))
        parent = panel.add_token_node(CanvasToken("goal", "Parent", "upstream"), QPointF(-260, 0))

        assert original.is_root_goal
        assert first.is_root_goal
        assert second.is_root_goal
        assert parent.is_root_goal
        assert {node.node_id for node in panel._root_goal_nodes()} == {
            original.node_id,
            first.node_id,
            second.node_id,
            parent.node_id,
        }

        assert panel.connect_nodes(parent.node_id, first.node_id, "split") is True
        assert parent.is_root_goal
        assert not first.is_root_goal
        assert second.is_root_goal

        edge = next(edge for edge in panel._edges if edge.source_id == parent.node_id and edge.target_id == first.node_id)
        panel._remove_edge(edge)

        assert first.is_root_goal
        assert parent.is_root_goal
        payload = panel.read_graph_tool()
        assert set(payload["graph"]["root_goal_ids"]) == {
            original.node_id,
            first.node_id,
            second.node_id,
            parent.node_id,
        }
    finally:
        panel.close()


def test_agent_canvas_context_input_does_not_replace_master_root(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        panel._clear_graph()
        goal = panel.add_token_node(CanvasToken("goal", "Statistics Tab", "intent"), QPointF(0, 0))
        context = panel.add_token_node(CanvasToken("context", "Statistics Tab Requirements", "constraints"), QPointF(-260, 0))

        assert panel._connect_with_kind(
            context.node_id,
            goal.node_id,
            "context",
            "Goal context",
            source_port="context",
            target_port="context",
        )

        assert goal.is_root_goal
        assert panel._root_goal_node() is goal

        panel._autoformat_graph()

        assert goal.pos().x() < context.pos().x()
    finally:
        panel.close()


def test_agent_canvas_header_stays_focused_on_canvas_actions(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        labels = [button.text() for button in panel.findChildren(QPushButton)]

        assert "New Goal" in labels
        assert "Fit" in labels
        assert "Open Chat" not in labels
        assert "Break Down" not in labels
        assert "+" not in labels
        assert "-" not in labels
        assert "Run" not in labels
    finally:
        panel.close()


def test_agent_canvas_starts_empty_until_user_adds_goal(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        assert panel.node_count() == 0
        assert panel.edge_count() == 0
        assert panel._selected.text() == "Selected: None"
        assert panel._inspector_lines[0].text() == "Type: Canvas"
        assert "Start with a goal" in panel._goal.text()
        assert panel._apply_edit_btn.isHidden()
        assert panel._cancel_edit_btn.isHidden()
    finally:
        panel.close()


def test_agent_canvas_new_goal_starts_without_dod(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        start_nodes = panel.node_count()
        start_edges = panel.edge_count()

        panel._add_goal()

        assert panel.node_count() == start_nodes + 1
        assert panel.edge_count() == start_edges
        assert sum(1 for node in panel._nodes.values() if node.token.kind == "dod") == 0
        assert "New goal placed" in panel._goal.text()
    finally:
        panel.close()


def test_agent_canvas_inspector_focuses_on_selected_node(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Improve graph UX", "Make it understandable"), QPointF(0, 0))
        panel._select_node(goal)

        inspector_text = "\n".join(line.text() for line in panel._inspector_lines)
        assert "Type: Goal" in inspector_text
        assert "Status: idle" in inspector_text
        assert "Activity:" in inspector_text
        assert "Role: Defines what good looks like" in inspector_text
        assert "Purpose: Intent, constraint, or branch" in inspector_text
        assert "Nodes:" not in inspector_text
        assert "Connections:" not in inspector_text
        assert "Mode:" not in inspector_text
        assert panel._selected.objectName() == "canvasInspectorSelectedRow"
        assert panel._inspector_lines[0].caption.text() == "Type"
        assert panel._inspector_lines[0].value.text() == "Goal"
        assert panel._inspector_lines[0].value.objectName() == "canvasInspectorMetaValue"
        assert panel._detail_label.text() == "Description"
        assert panel._detail_label.objectName() == "canvasInspectorFieldLabel"
        assert "acceptance signal" in panel._edit_detail.placeholderText()
    finally:
        panel.close()


def test_agent_canvas_inspector_uses_component_detail_labels(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        context = panel.add_token_node(
            CanvasToken("context", "User constraints", "Target: compact keyboard-first web app."),
            QPointF(0, 0),
        )
        panel._select_node(context)
        assert panel._detail_label.text() == "Description"
        assert "durable facts and implications" in panel._edit_detail.placeholderText()

        files = panel.add_token_node(CanvasToken("scope", "Files", "src/main.py"), QPointF(280, 0))
        panel._select_node(files)
        assert panel._detail_label.text() == "Paths"
        assert "one per line" in panel._edit_detail.placeholderText()

        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Accepted"), QPointF(560, 0))
        panel._select_node(dod)
        assert panel._detail_label.text() == "Acceptance Criteria"
    finally:
        panel.close()


def test_agent_canvas_has_inline_graph_chat(qapp, workspace):
    settings = type("Settings", (), {"load": lambda self: {"graph_agent_prompt": "Custom graph prompt."}})()
    calls = []

    def runner(prompt, tools, execute_tool):
        calls.append((prompt, [tool["name"] for tool in tools]))
        graph = execute_tool("read_graph", {})
        return f"Saw {len(graph['graph']['nodes'])} graph nodes."

    panel = AgentCanvasPanel(str(workspace), settings=settings, graph_agent_runner=runner)

    try:
        assert panel._graph_chat_input.placeholderText() == "Ask or instruct the graph agent..."
        assert "acyclic graph" in GRAPH_AGENT_SYSTEM_PROMPT
        assert panel._graph_agent_prompt() == "Custom graph prompt."
        assert "prefer 3-5 new nodes total" in panel._graph_agent_system_prompt()
        assert "Reuse existing nodes where possible" in panel._graph_agent_system_prompt()
        assert "component_playbook" in panel._graph_agent_system_prompt()
        assert "graph-native branch over a task list" in panel._graph_agent_system_prompt()
        assert "mega-feature decomposition" in panel._graph_agent_system_prompt()
        assert "Analyze -> Implement -> Verify" in panel._graph_agent_system_prompt()
        assert "Do not overcomplicate the graph" in panel._graph_agent_system_prompt()
        assert "Generation strategy: Prefer parallelism" in panel._graph_agent_system_prompt()
        assert "split non-trivial independent work" in panel._graph_agent_system_prompt()
        assert "Follow the selected generation strategy" in panel._graph_agent_system_prompt()
        assert "Avoid plain action lists with no graph signal" in panel._graph_agent_system_prompt()
        assert "Do not add fake structural nodes" in panel._graph_agent_system_prompt()
        assert "Break down by distinct responsibilities and real decision output contracts" in panel._graph_agent_system_prompt()
        assert "read_graph, web_fetch, propose_graph_patch" in panel._graph_agent_system_prompt()
        assert "Use web_fetch only for graph-planning research" in panel._graph_agent_system_prompt()
        assert '"source_port":"implement"' in panel._graph_agent_system_prompt()
        assert "Never connect implementation back into an upstream design/spec/context/evidence node" in panel._graph_agent_system_prompt()
        assert "evidence.context -> operation is invalid" in panel._graph_agent_system_prompt()
        assert "every operation add_node during Generate Steps" in panel._graph_agent_system_prompt()
        assert "coder/Coder for implementation" in panel._graph_agent_system_prompt()
        assert "Files/scope detail is repo paths only" in panel._graph_agent_system_prompt()
        assert "Context detail is synthesized durable facts" in panel._graph_agent_system_prompt()
        assert "Use ask_user only when a design/product ambiguity" in panel._graph_agent_system_prompt()
        assert "You may call ask_user multiple times" in panel._graph_agent_system_prompt()
        assert "do not ask the user to choose implementation details" in panel._graph_agent_system_prompt()
        assert "DoD" in panel._graph_agent_system_prompt()
        assert "meaningful non-empty detail" in panel._graph_agent_system_prompt()
        assert GRAPH_AGENT_TOOLS == (
            "read_graph",
            "web_fetch",
            "propose_graph_patch",
            "apply_graph_patch",
            "create_dod_fix_action",
            "ask_user",
        )
        assert panel._graph_chat_send_btn.text() == "Send"
        assert panel._graph_chat_send_btn.toolTip() == "Send this message to the graph agent."
        assert isinstance(panel._canvas_splitter, QSplitter)
        assert panel._canvas_splitter.orientation() == Qt.Orientation.Vertical
        assert panel._canvas_splitter.widget(0) is panel._graph
        assert panel._canvas_splitter.widget(1).objectName() == "canvasGraphChat"
        assert panel._graph_chat_clear_btn.text() == "Clear"
        assert not panel._graph_chat_clear_btn.isEnabled()
        assert panel._graph_chat_clear_btn.toolTip() == "No canvas chat messages to clear yet."

        panel._graph_chat_input.setText("What should I do next?")
        panel._send_graph_chat()

        transcript = panel._graph_chat_transcript.toPlainText()
        assert "You: What should I do next?" in transcript
        assert "Graph Agent: Saw" in transcript
        assert calls
        assert calls[0][1] == list(GRAPH_AGENT_TOOLS)
    finally:
        panel.close()


def test_agent_canvas_generation_strategy_comes_from_settings(qapp, workspace):
    settings = SettingsStore()
    panel = AgentCanvasPanel(str(workspace), settings=settings)

    try:
        assert "Generation strategy: Prefer parallelism" in panel._graph_agent_system_prompt()

        settings.update({"graph_generation_strategy": "atomicity"})

        assert "Generation strategy: Prefer atomicity" in panel._graph_agent_system_prompt()
        assert "prefer a long, clear sequential trail" in panel._graph_agent_system_prompt()
    finally:
        panel.close()


def test_agent_canvas_header_has_graph_agent_model_selectors(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        assert panel._provider_combo.currentText()
        assert panel._model_combo.currentText()
        assert panel._graph_agent_model() == panel._model_combo.currentText()

        header_layout = panel._add_goal_btn.parentWidget().layout()
        assert header_layout.indexOf(panel._provider_combo) < header_layout.indexOf(panel._add_goal_btn)
        assert header_layout.indexOf(panel._model_combo) < header_layout.indexOf(panel._add_goal_btn)
        assert panel._provider_combo.toolTip() == "Provider used by the canvas graph agent."
        assert panel._model_combo.toolTip() == "Model used by the canvas graph agent."
    finally:
        panel.close()


def test_agent_canvas_model_selector_uses_and_updates_default_model(qapp, workspace):
    provider = next(provider_id for provider_id, models in MODELS.items() if len(models) >= 2)
    first, second = MODELS[provider][:2]
    settings = SettingsStore()
    settings.save({"default_models": {provider: second}})
    panel = AgentCanvasPanel(str(workspace), settings=settings)

    try:
        panel._provider_combo.setCurrentText(provider)

        assert panel._model_combo.currentText() == second
        assert panel._graph_agent_model() == second

        panel._model_combo.setCurrentText(first)

        assert settings.load()["default_models"][provider] == first
        assert panel._graph_agent_model() == first
    finally:
        panel.close()


def test_agent_canvas_graph_agent_thinking_animates_until_text(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        panel._graph_agent_stream_index = panel._append_graph_chat_message("Graph Agent", "Thinking...")
        panel._start_graph_agent_thinking()

        assert panel._graph_agent_thinking_timer.isActive()
        assert "Graph Agent: Thinking..." in panel._graph_chat_transcript.toPlainText()

        panel._advance_graph_agent_thinking()

        assert "Graph Agent: Thinking." in panel._graph_chat_transcript.toPlainText()

        panel._on_graph_agent_chunk("First token")

        assert not panel._graph_agent_thinking_timer.isActive()
        assert "Graph Agent: First token" in panel._graph_chat_transcript.toPlainText()
    finally:
        panel.close()


def test_agent_canvas_graph_chat_shows_selected_component_context(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace), graph_agent_runner=lambda *_args: "Done.")

    try:
        node = panel.add_token_node(
            CanvasToken("operation", "Research graph-native UX affordances", "Find the useful branch points"),
            QPointF(0, 0),
        )
        panel._select_node(node)

        assert panel._graph_chat_meta.text() == "Context: [Research graph-native UX affordances]"
        assert panel._graph_chat_input.placeholderText() == "Ask about [Research graph-native UX affordances]..."
        assert "expand, rewrite, explain, or connect" in panel._graph_chat_input.toolTip()
    finally:
        panel.close()


def test_agent_canvas_graph_chat_prompt_is_anchored_to_selected_component(qapp, workspace):
    seen = {}

    def runner(prompt, tools, execute_tool):
        seen["prompt"] = prompt
        seen["scope"] = panel._graph_agent_scope_goal_id
        return "Expanded selected component."

    panel = AgentCanvasPanel(str(workspace), graph_agent_runner=runner)

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship graph UX", "make planning useful"), QPointF(0, 0))
        operation = panel.add_token_node(
            CanvasToken("operation", "Research graph-native UX", "Map non-linear planning interactions"),
            QPointF(280, 0),
        )
        assert panel.connect_nodes(goal.node_id, operation.node_id, source_port="work")
        panel._select_node(operation)

        panel._graph_chat_input.setText("Expand this with the missing proof")
        panel._send_graph_chat()

        assert seen["scope"] == goal.node_id
        assert "Selected component context:" in seen["prompt"]
        assert f"- id: {operation.node_id}" in seen["prompt"]
        assert "- kind: operation" in seen["prompt"]
        assert "- title: Research graph-native UX" in seen["prompt"]
        assert "anchored to the selected component" in seen["prompt"]
        transcript = panel._graph_chat_transcript.toPlainText()
        assert "[Research graph-native UX] Expand this with the missing proof" in transcript
    finally:
        panel.close()


def test_agent_canvas_graph_chat_resizes_with_splitter(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        panel._canvas_splitter.setSizes([520, 240])
        sizes = panel._canvas_splitter.sizes()

        assert panel.graph_state()["graph_chat_split"] == sizes
        assert sizes[0] > 0
        assert sizes[1] > 0
    finally:
        panel.close()


def test_agent_canvas_graph_chat_preserves_scroll_when_reading(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        panel.resize(900, 640)
        panel.show()
        qapp.processEvents()
        long_text = "\n".join(f"line {index}" for index in range(120))
        panel._append_graph_chat_message("Graph Agent", long_text)
        qapp.processEvents()

        bar = panel._graph_chat_transcript.verticalScrollBar()
        assert bar.maximum() > 0
        assert bar.value() == bar.maximum()

        bar.setValue(0)
        qapp.processEvents()
        assert panel._graph_chat_bottom_btn.isVisible()

        panel._append_graph_chat_message("Graph Agent", "new output")
        qapp.processEvents()

        assert bar.value() < bar.maximum()
        assert panel._graph_chat_bottom_btn.isVisible()

        panel._resume_graph_chat_auto_scroll()
        qapp.processEvents()
        assert bar.value() == bar.maximum()
        assert not panel._graph_chat_bottom_btn.isVisible()
    finally:
        panel.close()


def test_agent_canvas_read_graph_tool_describes_schema(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        payload = panel.read_graph_tool()

        assert payload["schema"]["tools"] == list(GRAPH_AGENT_TOOLS)
        assert "autoformat" not in payload["schema"]["patch_operations"]
        assert "autoformat" not in payload["schema"]["operation_shapes"]
        assert "web_fetch" in payload["schema"]["tool_contract"]
        assert "graph-planning research" in payload["schema"]["tool_contract"]["web_fetch"]
        assert "ask_user" in payload["schema"]["tool_contract"]
        assert "multi_select=true" in payload["schema"]["tool_contract"]["ask_user"]
        assert "non-empty detail" in payload["schema"]["tool_contract"]["propose_graph_patch"]
        assert "detail" in payload["schema"]["operation_shapes"]["add_node"]["required"]
        assert "component_playbook" in payload["schema"]
        assert payload["schema"]["run_context_model"]["handoff_recipes"]
        assert "Sibling nodes" in payload["schema"]["run_context_model"]["rule"]
        assert any(
            "source_port='implement'" in item
            for item in payload["schema"]["run_context_model"]["handoff_recipes"]
        )
        assert payload["schema"]["run_context_model"]["preferred_handoff_patch"]["source_port"] == "implement"
        assert "generation_patch_patterns" in payload["schema"]
        assert payload["schema"]["generation_patch_patterns"]["good_design_to_implementation_handoff"][3]["source_port"] == "implement"
        assert payload["schema"]["repair_patterns"]["proof_for_dod"] == [
            "goal.work -> operation",
            "operation.evidence -> evidence",
            "evidence.supports -> dod",
        ]
        assert payload["schema"]["repair_patterns"]["context_feeds_work"] == [
            "goal.context -> context",
            "context.context -> operation",
        ]
        assert any(
            "evidence.context -> operation is invalid" in item
            for item in payload["schema"]["run_context_model"]["not_handoffs"]
        )
        assert any(
            "Do not connect implementation back" in item
            for item in payload["schema"]["run_context_model"]["not_handoffs"]
        )
        assert payload["schema"]["component_playbook"]["operation"]["avoid"]
        assert payload["schema"]["available_crew"][0]["id"] == "coder"
        assert payload["schema"]["operation_agent_contract"]["default"] == {
            "agent_id": "coder",
            "agent_name": "Coder",
        }
        assert "agent_id and agent_name" in payload["schema"]["operation_agent_contract"]["rule"]
        assert payload["schema"]["operation_shapes"]["add_node"]["operation_preferred"] == [
            "agent_id",
            "agent_name",
        ]
        assert "restriction_model" in payload["schema"]
        assert payload["schema"]["restriction_model"]["hard_constraints"]
        assert any("Missing generated operation crew" in item for item in payload["schema"]["restriction_model"]["autocorrections"])
        assert any("Cycles" in item for item in payload["schema"]["restriction_model"]["do_not_autocorrect"])
        assert payload["schema"]["graph_value_contract"]["rule"].startswith("Linear is allowed")
        assert "plain action lists" in payload["schema"]["graph_value_contract"]["rule"]
        assert "fake structural" not in payload["schema"]["graph_value_contract"]["repair"]
        assert payload["schema"]["generation_quality_gate"]
        assert any(
            "agent_id and agent_name" in item
            for item in payload["schema"]["generation_quality_gate"]
        )
        assert any(
            "graph value" in item
            for item in payload["schema"]["generation_quality_gate"]
        )
        assert "generation_checklist" in payload["schema"]
        assert any("all-action branch" in item for item in payload["schema"]["generation_checklist"])
        assert any("non-empty detail" in item for item in payload["schema"]["generation_checklist"])
        assert any("mega-feature decomposition" in item for item in payload["schema"]["generation_checklist"])
        assert any("Analyze -> Implement -> Verify" in item for item in payload["schema"]["generation_checklist"])
        assert any("simple as possible" in item for item in payload["schema"]["generation_checklist"])
        assert any("Branch only for real parallel work" in item for item in payload["schema"]["generation_checklist"])
        assert any("distinct responsibility" in item for item in payload["schema"]["generation_checklist"])
        assert any("concise design questions" in item for item in payload["schema"]["generation_checklist"])
        assert any("engines, frameworks, libraries" in item for item in payload["schema"]["generation_checklist"])
        assert "goal" in payload["schema"]["node_kinds"]
        assert "operation" in payload["schema"]["node_kinds"]
        assert payload["schema"]["node_kinds"]["operation"]["title"] == "Action"
        assert payload["schema"]["node_kinds"]["scope"]["detail_contract"]["field_label"] == "Paths"
        assert "repo paths only" in payload["schema"]["node_kinds"]["scope"]["detail_contract"]["put"].lower()
        assert "Raw answer labels" in payload["schema"]["node_kinds"]["context"]["detail_contract"]["avoid"]
        assert any(
            "Context detail must synthesize" in item
            for item in payload["schema"]["generation_checklist"]
        )
        assert any(
            "Files detail must be repo paths only" in item
            for item in payload["schema"]["generation_checklist"]
        )
        assert "agent" not in payload["schema"]["node_kinds"]
        assert "dod" in payload["schema"]["node_kinds"]
        assert "DoD node" in " ".join(payload["schema"]["generation_checklist"])
        assert any(
            rule["source_kind"] == "goal" and rule["target_kind"] == "operation"
            for rule in payload["schema"]["connection_rules"]
        )
        assert any(
            rule["source_kind"] == "goal" and rule["source_port"] == "context" and rule["target_kind"] == "context"
            for rule in payload["schema"]["connection_rules"]
        )
        assert any(
            rule["source_kind"] == "evidence" and rule["target_kind"] == "dod"
            for rule in payload["schema"]["connection_rules"]
        )
        assert any(
            rule["source_kind"] == "operation"
            and rule["source_port"] == "implement"
            and rule["target_kind"] == "dod"
            for rule in payload["schema"]["connection_rules"]
        )
        assert payload["graph"]["nodes"] == []
        assert payload["graph"]["root_goal_id"] is None
        assert payload["graph"]["root_goal_ids"] == []
        assert payload["graph"]["scope"] == {"mode": "full", "goal_id": None}
        assert isinstance(payload["graph"]["unscoped_node_ids"], list)
        assert payload["cycles"] == []
    finally:
        panel.close()


def test_agent_canvas_visible_ports_are_actionable():
    kinds = ("goal", "operation", "context", "scope", "evidence", "dod", "decision")

    for kind in kinds:
        for port in output_ports(kind):
            assert any(
                action.source_kind == kind and action.source_port == port.key
                for action in _CREATION_ACTIONS
            ), f"{kind}.{port.key} output cannot create a node"
            assert any(
                rule.source_kind == kind and rule.source_port == port.key
                for rule in _CONNECTION_RULES
            ), f"{kind}.{port.key} output cannot connect to an existing node"

        for port in input_ports(kind):
            assert any(
                rule.target_kind == kind and rule.target_port == port.key
                for rule in _CONNECTION_RULES
            ), f"{kind}.{port.key} input cannot create or connect an upstream node"


def test_agent_canvas_generate_steps_prompt_asks_for_graph_native_branch(qapp, workspace):
    prompts = []

    def runner(prompt, _tools, _execute_tool):
        prompts.append(prompt)
        return "ok"

    panel = AgentCanvasPanel(str(workspace), graph_agent_runner=runner)

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Design planning graph", "avoid task lists"), QPointF(0, 0))
        panel._select_node(goal)

        panel._generate_steps_for_selected_goal()

        assert prompts
        assert "fresh goal-scoped context" in prompts[0]
        assert "not just a list of action nodes" in prompts[0]
        assert "smallest useful graph-native branch" in prompts[0]
        assert "Generation strategy: Prefer parallelism" in prompts[0]
        assert "Follow the selected generation strategy" in prompts[0]
        assert "mega-feature decomposition" in prompts[0]
        assert "not generic Analyze -> Implement -> Verify phases" in prompts[0]
        assert "Ask focused design questions" in prompts[0]
        assert "multiple calls are valid" in prompts[0]
        assert "synthesize them into durable constraints" in prompts[0]
        assert "Do not ask the user to choose engines" in prompts[0]
        assert "scope/context/evidence/decision/DoD" in prompts[0]
        assert "graph value" in prompts[0]
        assert "plain action lists" in prompts[0]
        assert "Do not create ownership-only nodes" in prompts[0]
        assert "Files are path inputs only" in prompts[0]
        assert "terminal acceptance contract" in prompts[0]
        assert "Use web_fetch only if external product/domain context" in prompts[0]
        assert "Do not stop after drafting a patch in text" in prompts[0]
        assert "canvas changes only when apply_graph_patch succeeds" in prompts[0]
        assert "Generate Steps is incomplete" in panel._graph_chat_transcript.toPlainText()
        assert "generation ended without graph changes" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_generate_steps_marks_no_patch_as_incomplete(qapp, workspace):
    def runner(prompt, _tools, execute_tool):
        assert "Do not stop after drafting a patch in text" in prompt
        execute_tool("read_graph", {})
        return "I drafted a patch but did not call apply_graph_patch."

    panel = AgentCanvasPanel(str(workspace), graph_agent_runner=runner)

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Develop Calculator App", "scientific calculator"), QPointF(0, 0))
        panel._select_node(goal)

        panel._generate_steps_for_selected_goal()

        transcript = panel._graph_chat_transcript.toPlainText()
        assert "I drafted a patch" in transcript
        assert "No graph changes were applied" in transcript
        assert "propose_graph_patch and apply_graph_patch" in transcript
        assert panel.node_count() == 1
        assert "generation ended without graph changes" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_generate_steps_reports_json_text_as_non_applicable(qapp, workspace):
    def runner(_prompt, _tools, execute_tool):
        execute_tool("read_graph", {})
        return '{"operations":[{"op":"add_node","client_id":"x","kind":"operation","title":"Ignored","detail":"for test"}]}'

    panel = AgentCanvasPanel(str(workspace), graph_agent_runner=runner)

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Develop Calculator App", "scientific calculator"), QPointF(0, 0))
        panel._select_node(goal)

        panel._generate_steps_for_selected_goal()

        transcript = panel._graph_chat_transcript.toPlainText()
        assert "A JSON patch was detected in the assistant message" in transcript
        assert "No graph changes were applied" in transcript
        assert "generation ended without graph changes" in panel._inspector_lines[2].text()
        assert panel.node_count() == 1
    finally:
        panel.close()


def test_graph_check_failures_text_avoids_summary_duplication(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        panel._record_graph_check_failure(
            {
                "summary": "weak generated graph",
                "error": (
                    "Generated steps added context that only hangs from the goal. "
                    "Connect context.context into at least one action or decision."
                ),
                "detail": "Generated steps added context that only hangs from the goal. Connect context.context into at least one action or decision.",
            }
        )

        text = panel._graph_check_failures_text()
        assert text.startswith("1 check failure stored during this graph-agent run:")
        assert "weak generated graph: weak generated graph" not in text
        assert "weak generated graph: Generated steps added context that only hangs from the goal." in text
    finally:
        panel.close()


def test_agent_canvas_web_fetch_tool_extracts_planning_context(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))
    seen = {}

    class Headers(dict):
        def get_content_charset(self):
            return "utf-8"

    class Response:
        url = "https://example.com/docs"
        headers = Headers({"content-type": "text/html; charset=utf-8"})

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit):
            seen["limit"] = limit
            return (
                b"<html><head><title>Docs &amp; Guide</title><style>bad</style></head>"
                b"<body><h1>Planning</h1><script>ignore()</script>"
                b"<p>Use this for graph context.</p></body></html>"
            )

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr("ui.widgets.agent_canvas.urllib.request.urlopen", fake_urlopen)

    try:
        result = panel._execute_graph_tool(
            "web_fetch",
            {"url": "https://example.com/docs", "max_chars": 80},
        )

        assert result["ok"] is True
        assert seen["url"] == "https://example.com/docs"
        assert seen["timeout"] == 12
        assert result["title"] == "Docs & Guide"
        assert "Planning" in result["content"]
        assert "Use this for graph context." in result["content"]
        assert "ignore()" not in result["content"]
        assert "implementation proof" in result["planning_note"]
    finally:
        panel.close()


def test_agent_canvas_web_fetch_tool_rejects_non_http_url(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        result = panel._execute_graph_tool("web_fetch", {"url": "file:///tmp/notes.md"})

        assert result["ok"] is False
        assert "http(s)" in result["error"]
    finally:
        panel.close()


def test_agent_canvas_web_fetch_tool_reports_fetch_error(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))

    def fail(_request, timeout=None):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("ui.widgets.agent_canvas.urllib.request.urlopen", fail)

    try:
        result = panel._execute_graph_tool("web_fetch", {"url": "https://example.com"})

        assert result["ok"] is False
        assert "Could not fetch URL" in result["error"]
    finally:
        panel.close()


def test_agent_canvas_generate_steps_autocorrects_missing_crew(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        goal = panel.add_token_node(CanvasToken("goal", "Create an Angry Birds-like game", "prototype"), QPointF(0, 0))
        panel._select_node(goal)
        panel._graph_agent_generation_mode = True

        result = panel._execute_graph_tool(
            "propose_graph_patch",
            {
                "operations": [
                    {"op": "add_node", "client_id": "a", "kind": "operation", "title": "Design core mechanics", "detail": "Define slingshot and scoring."},
                    {"op": "add_node", "client_id": "b", "kind": "operation", "title": "Implement core gameplay", "detail": "Build player interaction."},
                    {"op": "connect", "source": goal.node_id, "target": "a", "source_port": "work"},
                ]
            },
        )

        assert result["ok"] is True
        assert any("defaulted crew" in item for item in result["autocorrections"])
        operations = result["patch"]["operations"]
        added = [raw for raw in operations if raw["op"] == "add_node" and raw["kind"] == "operation"]
        assert [raw["agent_name"] for raw in added] == ["Architect", "Coder"]
        assert any(raw.get("op") == "connect" and raw.get("source") == "a" and raw.get("target") == "b" for raw in operations)
        assert all(node.token.title != "Design core mechanics" for node in panel._nodes.values())
    finally:
        panel.close()


def test_agent_canvas_generate_steps_rejects_plain_action_list(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        goal = panel.add_token_node(CanvasToken("goal", "Improve run UX", "make runs obvious"), QPointF(0, 0))
        panel._select_node(goal)
        panel._graph_agent_generation_mode = True

        result = panel._execute_graph_tool(
            "propose_graph_patch",
            {
                "operations": [
                    {"op": "add_node", "client_id": "a", "kind": "operation", "title": "Prepare labels", "detail": "Adjust wording.", "agent_id": "coder", "agent_name": "Coder"},
                    {"op": "add_node", "client_id": "b", "kind": "operation", "title": "Update buttons", "detail": "Adjust controls.", "agent_id": "coder", "agent_name": "Coder"},
                    {"op": "add_node", "client_id": "c", "kind": "operation", "title": "Tune spacing", "detail": "Adjust layout spacing.", "agent_id": "coder", "agent_name": "Coder"},
                    {"op": "connect", "source": goal.node_id, "target": "a", "source_port": "work"},
                    {"op": "connect", "source": "a", "target": "b", "source_port": "implement"},
                    {"op": "connect", "source": "b", "target": "c", "source_port": "implement"},
                ]
            },
        )

        assert result["ok"] is False
        assert result["summary"] == "missing graph signal"
        assert "just an action list" in result["error"]
        assert "consumed context/scope" in result["error"]
    finally:
        panel.close()


def test_agent_canvas_generate_steps_accepts_straight_crew_handoff(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        goal = panel.add_token_node(CanvasToken("goal", "Build calculator", "scientific calculator"), QPointF(0, 0))
        panel._select_node(goal)
        panel._graph_agent_generation_mode = True

        result = panel._execute_graph_tool(
            "propose_graph_patch",
            {
                "operations": [
                    {"op": "add_node", "client_id": "design", "kind": "operation", "title": "Design Calculator UX/UI", "detail": "Define interaction model and component contract.", "agent_id": "architect", "agent_name": "Architect"},
                    {"op": "add_node", "client_id": "implement", "kind": "operation", "title": "Implement Calculator UI", "detail": "Build the UI from the design contract.", "agent_id": "coder", "agent_name": "Coder"},
                    {"op": "connect", "source": goal.node_id, "target": "design", "source_port": "work"},
                    {"op": "connect", "source": "design", "target": "implement", "source_port": "implement"},
                ]
            },
        )

        assert result["ok"] is True
    finally:
        panel.close()


def test_agent_canvas_generate_steps_rejects_context_that_does_not_feed_work(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        goal = panel.add_token_node(CanvasToken("goal", "Plan graph-native workflow", "mega feature"), QPointF(0, 0))
        panel._select_node(goal)
        panel._graph_agent_generation_mode = True

        result = panel._execute_graph_tool(
            "propose_graph_patch",
            {
                "operations": [
                    {"op": "add_node", "client_id": "ctx", "kind": "context", "title": "User intent", "detail": "The workflow should feel graph-native."},
                    {"op": "add_node", "client_id": "scope", "kind": "scope", "title": "Canvas files", "detail": "ui/widgets/agent_canvas.py"},
                    {"op": "add_node", "client_id": "a", "kind": "operation", "title": "Shape UX", "detail": "Define interaction model.", "agent_id": "coder", "agent_name": "Coder"},
                    {"op": "add_node", "client_id": "b", "kind": "operation", "title": "Implement UX", "detail": "Apply the interaction model.", "agent_id": "coder", "agent_name": "Coder"},
                    {"op": "add_node", "client_id": "proof", "kind": "evidence", "title": "UX proof", "detail": "Focused tests and manual behavior notes."},
                    {"op": "add_node", "client_id": "dod", "kind": "dod", "title": "Done", "detail": "Workflow is usable."},
                    {"op": "connect", "source": goal.node_id, "target": "ctx", "source_port": "context"},
                    {"op": "connect", "source": goal.node_id, "target": "a", "source_port": "work"},
                    {"op": "connect", "source": "scope", "target": "a", "source_port": "read"},
                    {"op": "connect", "source": "a", "target": "b", "source_port": "implement"},
                    {"op": "connect", "source": "b", "target": "proof", "source_port": "evidence"},
                    {"op": "connect", "source": "proof", "target": "dod", "source_port": "supports"},
                ]
            },
        )

        assert result["ok"] is False
        assert result["summary"] == "weak generated graph"
        assert "context that only hangs from the goal" in result["error"]
    finally:
        panel.close()


def test_agent_canvas_graph_patch_rejects_files_node_without_paths(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        goal = panel.add_token_node(CanvasToken("goal", "Build calculator", "scientific calculator"), QPointF(0, 0))
        panel._select_node(goal)
        panel._graph_agent_generation_mode = True

        result = panel._execute_graph_tool(
            "propose_graph_patch",
            {
                "operations": [
                    {
                        "op": "add_node",
                        "client_id": "files",
                        "kind": "scope",
                        "title": "Files",
                        "detail": "Calculator component, UI, and test files",
                    },
                    {
                        "op": "add_node",
                        "client_id": "work",
                        "kind": "operation",
                        "title": "Implement calculator UI",
                        "detail": "Build the calculator UI from the known context.",
                        "agent_id": "coder",
                        "agent_name": "Coder",
                    },
                    {"op": "connect", "source": goal.node_id, "target": "work", "source_port": "work"},
                    {"op": "connect", "source": "files", "target": "work", "source_port": "read"},
                ]
            },
        )

        assert result["ok"] is False
        assert result["summary"] == "invalid files paths"
        assert "repo paths, one per line" in result["error"]
        assert "Calculator component" in result["error"]
    finally:
        panel.close()


def test_agent_canvas_generate_steps_autocorrects_obvious_missing_planning_handoff(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        goal = panel.add_token_node(CanvasToken("goal", "Build calculator", "scientific calculator"), QPointF(0, 0))
        panel._select_node(goal)
        panel._graph_agent_generation_mode = True

        result = panel._execute_graph_tool(
            "propose_graph_patch",
            {
                "operations": [
                    {"op": "add_node", "client_id": "ctx", "kind": "context", "title": "Calculator requirements", "detail": "Scientific calculator behavior and UX expectations."},
                    {"op": "add_node", "client_id": "design", "kind": "operation", "title": "Design Calculator UX/UI", "detail": "Create the interaction model and component plan.", "agent_id": "architect", "agent_name": "Architect"},
                    {"op": "add_node", "client_id": "engine", "kind": "operation", "title": "Implement Calculation Engine", "detail": "Build evaluator behavior from the design.", "agent_id": "coder", "agent_name": "Coder"},
                    {"op": "add_node", "client_id": "proof", "kind": "evidence", "title": "Implementation proof", "detail": "Tests and run notes."},
                    {"op": "add_node", "client_id": "dod", "kind": "dod", "title": "Calculator DoD", "detail": "Scientific calculator is usable and verified."},
                    {"op": "connect", "source": goal.node_id, "target": "ctx", "source_port": "context"},
                    {"op": "connect", "source": "ctx", "target": "design", "source_port": "context"},
                    {"op": "connect", "source": "ctx", "target": "engine", "source_port": "context"},
                    {"op": "connect", "source": "design", "target": "proof", "source_port": "evidence"},
                    {"op": "connect", "source": "engine", "target": "dod", "source_port": "implement"},
                    {"op": "connect", "source": "proof", "target": "dod", "source_port": "supports"},
                ]
            },
        )

        assert result["ok"] is True
        assert any("planning/design action" in item for item in result["autocorrections"])
        assert any(
            raw.get("op") == "connect"
            and raw.get("source") == "design"
            and raw.get("target") == "engine"
            and raw.get("source_port") == "implement"
            for raw in result["patch"]["operations"]
        )
        assert all(node.token.title != "Implement Calculation Engine" for node in panel._nodes.values())
    finally:
        panel.close()


def test_agent_canvas_generate_steps_accepts_crew_context_and_proof(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        goal = panel.add_token_node(CanvasToken("goal", "Plan graph-native workflow", "mega feature"), QPointF(0, 0))
        panel._select_node(goal)
        panel._graph_agent_generation_mode = True

        result = panel._execute_graph_tool(
            "propose_graph_patch",
            {
                "operations": [
                    {"op": "add_node", "client_id": "ctx", "kind": "context", "title": "User intent", "detail": "The workflow should feel graph-native."},
                    {"op": "add_node", "client_id": "scope", "kind": "scope", "title": "Canvas files", "detail": "ui/widgets/agent_canvas.py"},
                    {"op": "add_node", "client_id": "a", "kind": "operation", "title": "Shape UX", "detail": "Define interaction model.", "agent_id": "coder", "agent_name": "Coder"},
                    {"op": "add_node", "client_id": "b", "kind": "operation", "title": "Implement UX", "detail": "Apply the interaction model.", "agent_id": "coder", "agent_name": "Coder"},
                    {"op": "add_node", "client_id": "proof", "kind": "evidence", "title": "UX proof", "detail": "Focused tests and manual behavior notes."},
                    {"op": "add_node", "client_id": "dod", "kind": "dod", "title": "Done", "detail": "Workflow is usable."},
                    {"op": "connect", "source": goal.node_id, "target": "ctx", "source_port": "context"},
                    {"op": "connect", "source": "ctx", "target": "a", "source_port": "context"},
                    {"op": "connect", "source": goal.node_id, "target": "a", "source_port": "work"},
                    {"op": "connect", "source": "scope", "target": "a", "source_port": "read"},
                    {"op": "connect", "source": "a", "target": "b", "source_port": "implement"},
                    {"op": "connect", "source": "b", "target": "proof", "source_port": "evidence"},
                    {"op": "connect", "source": "proof", "target": "dod", "source_port": "supports"},
                ]
            },
        )

        assert result["ok"] is True
    finally:
        panel.close()


def test_agent_canvas_ask_user_tool_uses_choice_dialog(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))
    prompts = []

    def choose(question, choices, allow_free_text):
        prompts.append((question, choices, allow_free_text))
        return "Product Manager", True

    monkeypatch.setattr(panel, "_ask_user_choice_dialog", choose)

    try:
        result = panel._execute_graph_tool(
            "ask_user",
            {
                "question": "Who should own this?",
                "choices": ["Architect", "Product Manager"],
            },
        )

        assert result == {"ok": True, "cancelled": False, "answer": "Product Manager"}
        assert prompts == [("Who should own this?", ["Architect", "Product Manager"], True)]
    finally:
        panel.close()


def test_agent_canvas_ask_user_tool_can_disable_other_choice(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))
    prompts = []

    def choose(question, choices, allow_free_text):
        prompts.append((question, choices, allow_free_text))
        return "Architect", True

    monkeypatch.setattr(panel, "_ask_user_choice_dialog", choose)

    try:
        result = panel._execute_graph_tool(
            "ask_user",
            {
                "question": "Who should own this?",
                "choices": ["Architect", "Product Manager"],
                "allow_free_text": False,
            },
        )

        assert result == {"ok": True, "cancelled": False, "answer": "Architect"}
        assert prompts == [("Who should own this?", ["Architect", "Product Manager"], False)]
    finally:
        panel.close()


def test_agent_canvas_ask_user_tool_supports_multi_select(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))
    prompts = []

    def choose(question, choices, allow_free_text, *, multi_select=False):
        prompts.append((question, choices, allow_free_text, multi_select))
        return "Memory functions; History/undo support", True

    monkeypatch.setattr(panel, "_ask_user_choice_dialog", choose)

    try:
        result = panel._execute_graph_tool(
            "ask_user",
            {
                "question": "Which features are needed?",
                "choices": ["Memory functions", "History/undo support", "Unit conversions"],
                "multi_select": True,
            },
        )

        assert result == {
            "ok": True,
            "cancelled": False,
            "answer": "Memory functions; History/undo support",
            "answers": ["Memory functions", "History/undo support"],
        }
        assert prompts == [
            (
                "Which features are needed?",
                ["Memory functions", "History/undo support", "Unit conversions"],
                True,
                True,
            )
        ]
    finally:
        panel.close()


def test_agent_canvas_ask_user_choice_dialog_shows_choices_and_other(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))
    seen = {}

    def accept_with_other(dialog):
        seen["style"] = dialog.styleSheet()
        prompt = dialog.findChild(QLabel, "graphQuestionPrompt")
        assert prompt is not None
        radios = dialog.findChildren(QRadioButton)
        assert all(radio.objectName() == "graphQuestionChoice" for radio in radios)
        seen["radio_texts"] = [radio.text() for radio in radios]
        other = next(radio for radio in radios if radio.text() == "Other (specify)")
        other.setChecked(True)
        field = dialog.findChild(QTextEdit, "graphQuestionOther")
        assert field is not None
        field.setPlainText("Ask design first")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept_with_other)

    try:
        answer, ok = panel._ask_user_choice_dialog(
            "Which path?",
            ["Architect", "Product Manager"],
            True,
        )

        assert ok is True
        assert answer == "Ask design first"
        assert seen["radio_texts"] == ["Architect", "Product Manager", "Other (specify)"]
        assert "border-left-color: #67e8f9" in seen["style"]
        assert "QRadioButton#graphQuestionChoice:checked" in seen["style"]
    finally:
        panel.close()


def test_agent_canvas_ask_user_choice_dialog_shows_multi_select_checkboxes(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))
    seen = {}

    def accept_with_multiple(dialog):
        seen["style"] = dialog.styleSheet()
        checks = dialog.findChildren(QCheckBox)
        assert all(check.objectName() == "graphQuestionChoice" for check in checks)
        seen["check_texts"] = [check.text() for check in checks]
        checks[0].setChecked(True)
        checks[2].setChecked(True)
        other = next(check for check in checks if check.text() == "Other (specify)")
        other.setChecked(True)
        field = dialog.findChild(QTextEdit, "graphQuestionOther")
        assert field is not None
        assert field.isEnabled()
        field.setPlainText("Programmable constants")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept_with_multiple)

    try:
        answer, ok = panel._ask_user_choice_dialog(
            "Which features?",
            ["Memory functions", "History/undo support", "Unit conversions"],
            True,
            multi_select=True,
        )

        assert ok is True
        assert answer == "Memory functions; Unit conversions; Programmable constants"
        assert seen["check_texts"] == [
            "Memory functions",
            "History/undo support",
            "Unit conversions",
            "Other (specify)",
        ]
        assert "QCheckBox#graphQuestionChoice:checked" in seen["style"]
    finally:
        panel.close()


def test_agent_canvas_ask_user_tool_uses_text_dialog_without_choices(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))
    prompts = []

    def answer(parent, title, label, text):
        prompts.append((parent, title, label, text))
        return "Focus on review first.", True

    monkeypatch.setattr(QInputDialog, "getMultiLineText", answer)

    try:
        result = panel._execute_graph_tool("ask_user", {"question": "What should I prioritize?"})

        assert result == {"ok": True, "cancelled": False, "answer": "Focus on review first."}
        assert prompts == [(panel, "Graph Agent Question", "What should I prioritize?", "")]
    finally:
        panel.close()


def test_agent_canvas_ask_user_tool_can_cancel(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *args: ("", False))

    try:
        result = panel._execute_graph_tool("ask_user", {"question": "Continue?"})

        assert result == {"ok": False, "cancelled": True, "answer": ""}
    finally:
        panel.close()


def test_agent_canvas_marks_floating_components_unscoped(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        root = panel.add_token_node(CanvasToken("goal", "Root", "intent"), QPointF(0, 0))
        work = panel.add_token_node(CanvasToken("operation", "Floating work", "not owned yet"), QPointF(260, 0))

        assert work.is_unscoped
        assert work.node_id in panel.read_graph_tool()["graph"]["unscoped_node_ids"]
        assert "Unscoped" in work.toolTip()

        assert panel.connect_nodes(root.node_id, work.node_id, "work") is True
        assert not work.is_unscoped
        assert work.node_id not in panel.read_graph_tool()["graph"]["unscoped_node_ids"]

        panel._remove_edge(panel._edges[-1])
        assert work.is_unscoped
        assert work.node_id in panel.read_graph_tool()["graph"]["unscoped_node_ids"]
    finally:
        panel.close()


def test_agent_canvas_read_graph_scopes_to_selected_goal(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        first_goal = panel.add_token_node(CanvasToken("goal", "First goal", "one"), QPointF(0, 0))
        first_work = panel.add_token_node(CanvasToken("operation", "First work", "owned"), QPointF(280, 0))
        second_goal = panel.add_token_node(CanvasToken("goal", "Second goal", "two"), QPointF(0, 220))
        second_work = panel.add_token_node(CanvasToken("operation", "Second work", "owned"), QPointF(280, 220))

        assert panel.connect_nodes(first_goal.node_id, first_work.node_id, "work") is True
        assert panel.connect_nodes(second_goal.node_id, second_work.node_id, "work") is True
        panel._select_node(first_goal)

        payload = panel._execute_graph_tool("read_graph", {})
        titles = {node["title"] for node in payload["graph"]["nodes"]}

        assert payload["graph"]["scope"]["mode"] == "goal"
        assert payload["graph"]["scope"]["goal_id"] == first_goal.node_id
        assert "outside_node_ids" not in payload["graph"]["scope"]
        assert payload["graph"]["root_goal_id"] == first_goal.node_id
        assert payload["graph"]["root_goal_ids"] == [first_goal.node_id]
        assert set(payload["graph"]["unscoped_node_ids"]) <= {first_goal.node_id, first_work.node_id}
        assert {"First goal", "First work"} <= titles
        assert "Second goal" not in titles
        assert "Second work" not in titles
    finally:
        panel.close()


def test_agent_canvas_scoped_graph_patch_rejects_existing_outside_node(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        first_goal = panel.add_token_node(CanvasToken("goal", "First goal", "one"), QPointF(0, 0))
        first_work = panel.add_token_node(CanvasToken("operation", "First work", "owned"), QPointF(280, 0))
        second_goal = panel.add_token_node(CanvasToken("goal", "Second goal", "two"), QPointF(0, 220))
        second_work = panel.add_token_node(CanvasToken("operation", "Second work", "owned"), QPointF(280, 220))

        assert panel.connect_nodes(first_goal.node_id, first_work.node_id, "work") is True
        assert panel.connect_nodes(second_goal.node_id, second_work.node_id, "work") is True
        panel._select_node(first_goal)

        result = panel._execute_graph_tool(
            "apply_graph_patch",
            {"operations": [{"op": "update_node", "id": second_work.node_id, "title": "Changed elsewhere"}]},
        )

        assert result["ok"] is False
        assert "outside selected goal scope" in result["error"]
        assert "Active goal is First goal" in result["error"]
        assert "Second work" not in result["error"]
        assert result["summary"] == "outside selected goal"
        assert result["active_scope"]["goal_id"] == first_goal.node_id
        assert {node["title"] for node in result["active_scope"]["nodes"]} == {"First goal", "First work"}
        assert "active_scope.node_ids" in result["hint"]
        assert second_work.token.title == "Second work"
    finally:
        panel.close()


def test_agent_canvas_scoped_graph_patch_requires_new_nodes_to_join_scope(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        goal = panel.add_token_node(CanvasToken("goal", "Scoped goal", "intent"), QPointF(0, 0))
        panel._select_node(goal)

        floating = panel._execute_graph_tool(
            "apply_graph_patch",
            {"operations": [{"op": "add_node", "client_id": "loose", "kind": "operation", "title": "Loose work"}]},
        )

        assert floating["ok"] is False
        assert "New graph nodes must be connected" in floating["error"]
        assert "same patch" in floating["error"]
        assert all(node.token.title != "Loose work" for node in panel._nodes.values())

        connected = panel._execute_graph_tool(
            "apply_graph_patch",
            {
                "operations": [
                    {"op": "add_node", "client_id": "work", "kind": "operation", "title": "Scoped work"},
                    {"op": "connect", "source": goal.node_id, "target": "work", "source_port": "work"},
                ]
            },
        )

        assert connected["ok"] is True
        work = next(node for node in panel._nodes.values() if node.token.title == "Scoped work")
        assert not work.is_unscoped
    finally:
        panel.close()


def test_agent_canvas_graph_agent_cannot_delete_source_goal(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        goal = panel.add_token_node(CanvasToken("goal", "Source goal", "keep anchor"), QPointF(0, 0))
        panel._select_node(goal)

        result = panel._execute_graph_tool(
            "apply_graph_patch",
            {"operations": [{"op": "delete_node", "id": goal.node_id}]},
        )

        assert result["ok"] is False
        assert "cannot delete the source goal" in result["error"]
        assert goal.node_id in panel._nodes
    finally:
        panel.close()


def test_agent_canvas_graph_agent_can_delete_non_source_subgoal(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        goal = panel.add_token_node(CanvasToken("goal", "Source goal", "anchor"), QPointF(0, 0))
        child = panel.add_token_node(CanvasToken("goal", "Removable subgoal", "child"), QPointF(260, 0))
        assert panel.connect_nodes(goal.node_id, child.node_id, "split") is True
        panel._select_node(goal)

        result = panel._execute_graph_tool(
            "apply_graph_patch",
            {"operations": [{"op": "delete_node", "id": child.node_id}]},
        )

        assert result["ok"] is True
        assert goal.node_id in panel._nodes
        assert child.node_id not in panel._nodes
    finally:
        panel.close()


def test_agent_canvas_graph_agent_cannot_add_input_to_source_goal(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        goal = panel.add_token_node(CanvasToken("goal", "Source goal", "anchor"), QPointF(0, 0))
        panel._select_node(goal)

        result = panel._execute_graph_tool(
            "apply_graph_patch",
            {
                "operations": [
                    {
                        "op": "add_node",
                        "client_id": "files",
                        "kind": "scope",
                        "title": "Files",
                        "detail": "src/",
                    },
                    {"op": "connect", "source": "files", "target": goal.node_id, "source_port": "read"},
                ]
            },
        )

        assert result["ok"] is False
        assert "cannot add an incoming connection to the source goal" in result["error"]
        assert all(node.token.title != "Files" for node in panel._nodes.values())
    finally:
        panel.close()


def test_agent_canvas_graph_patch_accepts_string_id_as_new_node_client_alias(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        goal = panel.add_token_node(CanvasToken("goal", "Scoped goal", "intent"), QPointF(0, 0))
        panel._select_node(goal)

        result = panel._execute_graph_tool(
            "apply_graph_patch",
            {
                "operations": [
                    {"op": "add_node", "id": "op_1", "kind": "operation", "title": "Clarify workflow"},
                    {"op": "connect", "source": goal.node_id, "target": "op_1", "source_port": "work"},
                ]
            },
        )

        assert result["ok"] is True
        node = next(node for node in panel._nodes.values() if node.token.title == "Clarify workflow")
        assert node.token.detail
        assert "Clarify workflow" in node.token.detail
    finally:
        panel.close()


def test_agent_canvas_agent_apply_patch_autoformats_immediately(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        panel._clear_graph()
        goal = panel.add_token_node(CanvasToken("goal", "Goal", "intent"), QPointF(420, 180))
        panel._select_node(goal)

        result = panel._execute_graph_tool(
            "apply_graph_patch",
            {
                "operations": [
                    {"op": "add_node", "client_id": "work", "kind": "operation", "title": "Work"},
                    {"op": "connect", "source": goal.node_id, "target": "work", "source_port": "work"},
                ]
            },
        )

        assert result["ok"] is True
        goal = next(node for node in panel._nodes.values() if node.token.title == "Goal")
        work = next(node for node in panel._nodes.values() if node.token.title == "Work")
        assert goal.pos().x() < work.pos().x()
        assert panel._frames
    finally:
        panel.close()


def test_agent_canvas_agent_apply_patch_autoformat_stays_in_scope(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        panel._clear_graph()
        first_goal = panel.add_token_node(CanvasToken("goal", "First goal", "intent"), QPointF(420, 180))
        first_work = panel.add_token_node(CanvasToken("operation", "First work", "owned"), QPointF(-300, -140))
        second_goal = panel.add_token_node(CanvasToken("goal", "Second goal", "other"), QPointF(900, 620))
        second_work = panel.add_token_node(CanvasToken("operation", "Second work", "other"), QPointF(1200, 760))
        panel.connect_nodes(first_goal.node_id, first_work.node_id, "work")
        panel.connect_nodes(second_goal.node_id, second_work.node_id, "work")
        second_before = {
            "Second goal": (second_goal.pos().x(), second_goal.pos().y()),
            "Second work": (second_work.pos().x(), second_work.pos().y()),
        }
        panel._select_node(first_goal)

        result = panel._execute_graph_tool(
            "apply_graph_patch",
            {
                "operations": [
                    {"op": "add_node", "client_id": "new_work", "kind": "operation", "title": "Scoped work"},
                    {"op": "connect", "source": first_goal.node_id, "target": "new_work", "source_port": "work"},
                ]
            },
        )

        assert result["ok"] is True
        second_after = {
            node.token.title: (node.pos().x(), node.pos().y())
            for node in panel._nodes.values()
            if node.token.title in second_before
        }
        assert second_after == second_before
        first_goal = next(node for node in panel._nodes.values() if node.token.title == "First goal")
        scoped_work = next(node for node in panel._nodes.values() if node.token.title == "Scoped work")
        assert first_goal.pos().x() < scoped_work.pos().x()
        assert "autoformatted active graph" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_graph_tool_status_compacts_scope_failures(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        panel._on_graph_agent_tool_called("propose_graph_patch", {"operations": []})
        panel._on_graph_agent_tool_result(
            "propose_graph_patch",
            (
                '{"ok": false, "summary": "outside selected goal", '
                '"error": "Patch touches node outside selected goal scope: Other (id 4)."}'
            ),
        )

        transcript = panel._graph_chat_transcript.toPlainText()

        assert "Check 0/1, 1 failed" in transcript
        assert "Check failed: outside selected goal" in transcript
        assert "Patch touches node outside selected goal scope" not in transcript
    finally:
        panel.close()


def test_agent_canvas_graph_tool_check_failures_are_grouped_and_stored(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        failures = [
            {
                "ok": False,
                "summary": "invalid connection",
                "error": "connect is not valid: evidence.context -> operation. Valid for this pair: evidence.feedback -> operation.feedback.",
            },
            {
                "ok": False,
                "summary": "missing crew",
                "error": "operation nodes need agent_id and agent_name.",
            },
            {
                "ok": False,
                "summary": "invalid connection",
                "error": "connect is not valid: decision.reason -> goal.",
            },
        ]
        for failure in failures:
            panel._on_graph_agent_tool_called("propose_graph_patch", {"operations": [{"op": "connect"}]})
            panel._on_graph_agent_tool_result("propose_graph_patch", json.dumps(failure))

        transcript = panel._graph_chat_transcript.toPlainText()
        html = panel._graph_chat_transcript.toHtml()
        stored = panel.graph_state()["graph_check_failures"]

        assert "Check 0/3, 3 failed" in transcript
        assert "graph-tool:propose_graph_patch" in html
        assert len(stored) == 3
        assert not [message for message in panel.graph_state()["graph_chat"] if message["role"] == "Check Failures"]
        assert "3 check failures stored" not in transcript
        assert "connect is not valid" not in transcript

        panel._on_graph_chat_anchor_clicked(QUrl("graph-tool:propose_graph_patch"))

        expanded = panel._graph_chat_transcript.toPlainText()
        assert "failed: 3 check failures stored" in expanded
        assert "failed3 check failures stored" not in expanded
        assert "3 check failures stored" in expanded
        assert "invalid connection x2" in expanded
        assert "missing crew" in expanded
        assert "connect is not valid" in expanded
        assert expanded.count("Check failed: invalid connection") == 1

        panel._on_graph_chat_anchor_clicked(QUrl("graph-tool:propose_graph_patch"))
        assert "3 check failures stored" not in panel._graph_chat_transcript.toPlainText()
    finally:
        panel.close()


def test_agent_canvas_graph_tool_status_explains_invalid_patch(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        result = panel._execute_graph_tool("propose_graph_patch", {"operations": []})

        assert result["ok"] is False
        assert result["summary"] == "empty patch"
        assert "operations list" in result["detail"]

        panel._on_graph_agent_tool_called("propose_graph_patch", {"operations": []})
        panel._on_graph_agent_tool_result("propose_graph_patch", json.dumps(result))

        transcript = panel._graph_chat_transcript.toPlainText()
        assert "Check failed: empty patch" in transcript
        assert "operations list" not in transcript
        assert "invalid patch" not in transcript
    finally:
        panel.close()


def test_agent_canvas_graph_tool_status_explains_invalid_connection(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        goal = panel.add_token_node(CanvasToken("goal", "Goal", "intent"), QPointF(0, 0))
        proof = panel.add_token_node(CanvasToken("evidence", "Proof", "tests"), QPointF(300, 0))

        result = panel._execute_graph_tool(
            "propose_graph_patch",
            {"operations": [{"op": "connect", "source": proof.node_id, "target": goal.node_id, "source_port": "supports"}]},
        )

        assert result["ok"] is False
        assert result["summary"] == "invalid connection"
        assert "evidence.supports -> goal" in result["detail"]
        assert "Valid from source" in result["error"]
    finally:
        panel.close()


def test_agent_canvas_invalid_connection_suggests_valid_pair_port(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        proof = panel.add_token_node(CanvasToken("evidence", "Proof", "tests"), QPointF(0, 0))
        work = panel.add_token_node(CanvasToken("operation", "Follow up", "fix from proof"), QPointF(300, 0))

        result = panel._execute_graph_tool(
            "propose_graph_patch",
            {"operations": [{"op": "connect", "source": proof.node_id, "target": work.node_id, "source_port": "context"}]},
        )

        assert result["ok"] is False
        assert result["summary"] == "invalid connection"
        assert "evidence.context -> operation" in result["error"]
        assert "Valid for this pair: evidence.feedback -> operation.feedback" in result["error"]
        assert result["repair_pattern"] == "context_feeds_work"
        assert "evidence.feedback -> operation" in result["repair_hint"]
    finally:
        panel.close()


def test_agent_canvas_invalid_goal_to_evidence_connection_suggests_proof_pattern(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        goal = panel.add_token_node(CanvasToken("goal", "Goal", "intent"), QPointF(0, 0))
        proof = panel.add_token_node(CanvasToken("evidence", "Proof", "tests"), QPointF(300, 0))

        result = panel._execute_graph_tool(
            "propose_graph_patch",
            {"operations": [{"op": "connect", "source": goal.node_id, "target": proof.node_id, "source_port": "context"}]},
        )

        assert result["ok"] is False
        assert result["summary"] == "invalid connection"
        assert "goal.context -> evidence" in result["error"]
        assert result["repair_pattern"] == "proof_for_dod"
        assert "goal.work -> operation" in result["repair_hint"]
        assert "operation.evidence -> evidence" in result["repair_hint"]
        assert "evidence.supports -> dod" in result["repair_hint"]
    finally:
        panel.close()


def test_agent_canvas_weak_dod_graph_suggests_proof_pattern(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        goal = panel.add_token_node(CanvasToken("goal", "Goal", "intent"), QPointF(0, 0))
        panel._select_node(goal)
        panel._graph_agent_generation_mode = True

        result = panel._execute_graph_tool(
            "propose_graph_patch",
            {
                "operations": [
                    {"op": "add_node", "client_id": "design", "kind": "operation", "title": "Design flow", "detail": "Define the behavior contract.", "agent_id": "architect", "agent_name": "Architect"},
                    {"op": "add_node", "client_id": "build", "kind": "operation", "title": "Build flow", "detail": "Implement from the behavior contract.", "agent_id": "coder", "agent_name": "Coder"},
                    {"op": "add_node", "client_id": "dod", "kind": "dod", "title": "Accepted", "detail": "The behavior is implemented and verified."},
                    {"op": "connect", "source": goal.node_id, "target": "design", "source_port": "work"},
                    {"op": "connect", "source": "design", "target": "build", "source_port": "implement"},
                    {"op": "connect", "source": "build", "target": "dod", "source_port": "implement"},
                ]
            },
        )

        assert result["ok"] is False
        assert result["summary"] == "weak generated graph"
        assert result["repair_pattern"] == "proof_for_dod"
        assert "operation.evidence -> evidence" in result["repair_hint"]
        assert "evidence.supports -> dod" in result["repair_hint"]
    finally:
        panel.close()


def test_agent_canvas_cycle_error_explains_upstream_artifact_writeback(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        spec = panel.add_token_node(CanvasToken("evidence", "UI Design Specification", "wireframes"), QPointF(0, 0))
        implement = panel.add_token_node(CanvasToken("operation", "Implement Calculator UI", "build from spec"), QPointF(300, 0))
        assert panel.connect_nodes(spec.node_id, implement.node_id, "feedback")

        result = panel._execute_graph_tool(
            "propose_graph_patch",
            {"operations": [{"op": "connect", "source": implement.node_id, "target": spec.node_id, "source_port": "evidence"}]},
        )

        assert result["ok"] is False
        assert result["summary"] == "cycle blocked"
        assert "UI Design Specification -> Implement Calculator UI -> UI Design Specification" in result["error"]
        assert "writing back into an upstream artifact" in result["error"]
        assert "separate downstream evidence/proof" in result["repair_hint"]
    finally:
        panel.close()


def test_agent_canvas_graph_tool_rejects_autoformat_operation(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        result = panel._execute_graph_tool(
            "propose_graph_patch",
            {"operations": [{"op": "autoformat"}]},
        )

        assert result["ok"] is False
        assert result["summary"] == "unsupported operation"
        assert "autoformat" in result["detail"]
    finally:
        panel.close()


def test_agent_canvas_clear_graph_chat(qapp, workspace):
    panel = AgentCanvasPanel(
        str(workspace),
        graph_agent_runner=lambda _prompt, _tools, _execute: "Review should attach to proof/evidence.",
    )

    try:
        panel._graph_chat_input.setText("Where does review fit?")
        panel._send_graph_chat()
        assert "Where does review fit?" in panel._graph_chat_transcript.toPlainText()
        assert panel.graph_state()["graph_chat"]
        assert panel._graph_chat_clear_btn.isEnabled()
        assert panel._graph_chat_clear_btn.toolTip() == "Clear the canvas chat history."

        panel._clear_graph_chat()

        assert panel._graph_chat_messages == []
        assert panel._graph_chat_transcript.toPlainText() == ""
        assert panel.graph_state()["graph_chat"] == []
        assert not panel._graph_chat_clear_btn.isEnabled()
        assert panel._graph_chat_clear_btn.toolTip() == "No canvas chat messages to clear yet."
        assert "cleared canvas chat" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_agent_response_autoformats_graph(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        panel._clear_graph()
        goal = panel.add_token_node(CanvasToken("goal", "Plan", "intent"), QPointF(400, 250))
        work = panel.add_token_node(CanvasToken("operation", "Build", "work"), QPointF(-300, -120))
        panel.connect_nodes(goal.node_id, work.node_id, "work")
        assert panel._frames

        panel._graph_agent_stream_index = panel._append_graph_chat_message("Graph Agent", "Thinking...")
        panel._on_graph_agent_done("Done planning.")

        assert "Graph Agent: Done planning." in panel._graph_chat_transcript.toPlainText()
        assert panel._frames
        assert goal.pos().x() < work.pos().x()
    finally:
        panel.close()


def test_agent_canvas_agent_response_autoformat_stays_in_scope(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        panel._clear_graph()
        first_goal = panel.add_token_node(CanvasToken("goal", "First goal", "intent"), QPointF(420, 180))
        first_work = panel.add_token_node(CanvasToken("operation", "First work", "owned"), QPointF(-300, -140))
        second_goal = panel.add_token_node(CanvasToken("goal", "Second goal", "other"), QPointF(900, 620))
        second_work = panel.add_token_node(CanvasToken("operation", "Second work", "other"), QPointF(1200, 760))
        panel.connect_nodes(first_goal.node_id, first_work.node_id, "work")
        panel.connect_nodes(second_goal.node_id, second_work.node_id, "work")
        second_before = {
            "Second goal": (second_goal.pos().x(), second_goal.pos().y()),
            "Second work": (second_work.pos().x(), second_work.pos().y()),
        }

        panel._graph_agent_scope_goal_id = first_goal.node_id
        panel._graph_agent_stream_index = panel._append_graph_chat_message("Graph Agent", "Thinking...")
        panel._on_graph_agent_done("Done planning.")

        second_after = {
            node.token.title: (node.pos().x(), node.pos().y())
            for node in panel._nodes.values()
            if node.token.title in second_before
        }
        assert second_after == second_before
        assert first_goal.pos().x() < first_work.pos().x()
        assert "autoformatted active graph" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_graph_tool_results_do_not_fill_transcript(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        panel._graph_agent_stream_index = panel._append_graph_chat_message("Graph Agent", "Thinking...")

        panel._on_graph_agent_tool_called("read_graph", {})
        panel._on_graph_agent_tool_result(
            "read_graph",
            '{\n  "schema": {"tools": ["read_graph"]},\n  "graph": {"nodes": [{}, {}], "edges": [{}]},\n  "cycles": []\n}',
        )
        for _ in range(4):
            panel._on_graph_agent_tool_called("propose_graph_patch", {"operations": [{"op": "set_active"}]})
            panel._on_graph_agent_tool_result(
                "propose_graph_patch",
                '{"ok": true, "patch": {"operations": [{"op": "set_active"}]}}',
            )
        panel._on_graph_agent_tool_called("apply_graph_patch", {"operations": [{"op": "set_active"}]})
        panel._on_graph_agent_tool_result(
            "apply_graph_patch",
            '{"ok": true, "applied_operations": 1, "nodes": 2, "edges": 1}',
        )
        panel._on_graph_agent_tool_called("ask_user", {"question": "Which path?"})
        panel._on_graph_agent_tool_result(
            "ask_user",
            '{"ok": true, "cancelled": false, "answer": "Review first"}',
        )

        transcript = panel._graph_chat_transcript.toPlainText()
        assert transcript.count("Tools:") == 1
        assert "Read 1/1" in transcript
        assert "Check 4/4" in transcript
        assert "Apply 1/1" in transcript
        assert "Ask 1/1" in transcript
        assert "Answered: Review first" in transcript
        html = panel._graph_chat_transcript.toHtml()
        assert "graph-tool:read_graph" in html
        assert "graph-tool:propose_graph_patch" in html
        assert "graph-tool:apply_graph_patch" in html
        assert "graph-tool:ask_user" in html
        assert "apply_graph_patch finished" not in transcript
        assert "propose_graph_patch" not in transcript
        assert "Tool Result:" not in transcript
        assert "\"schema\"" not in transcript

        panel._on_graph_chat_anchor_clicked(QUrl("graph-tool:read_graph"))
        expanded = panel._graph_chat_transcript.toPlainText()
        assert "Expanded tool" in expanded
        assert "Read activity" in expanded
        assert "Reading graph" in expanded
        assert "Read 2 nodes, 1 links" in expanded
        assert "\"schema\"" not in expanded

        panel._on_graph_chat_anchor_clicked(QUrl("graph-tool:apply_graph_patch"))
        switched = panel._graph_chat_transcript.toPlainText()
        assert "Apply activity" in switched
        assert "Applied 1 ops" in switched
        assert "Read activity" not in switched
        assert "graph agent finished ask_user" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_graph_chat_colors_internal_messages(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        panel._append_graph_chat_message("You", "Plan <flow>")
        panel._append_graph_chat_message("Tools", "Read 1/1 | Apply 1/1")
        panel._append_graph_chat_message("Graph Agent", "Done.")

        transcript = panel._graph_chat_transcript.toPlainText()
        html = panel._graph_chat_transcript.toHtml().lower()

        assert "You: Plan <flow>" in transcript
        assert "Tools: Read 1/1 | Apply 1/1" in transcript
        assert "Graph Agent: Done." in transcript
        assert "#f6c744" in html
        assert "#8ab4ff" in html
        assert "#67e8f9" in html
    finally:
        panel.close()


def test_agent_canvas_graph_agent_busy_does_not_append_duplicate_message(qapp, workspace):
    class RunningThread:
        def isRunning(self):
            return True

        def cancel(self):
            pass

    panel = AgentCanvasPanel(str(workspace))
    try:
        panel._graph_agent_thread = RunningThread()
        panel._graph_chat_input.setText("Generate steps again")

        panel._send_graph_chat()

        assert panel._graph_chat_input.text() == "Generate steps again"
        transcript = panel._graph_chat_transcript.toPlainText()
        assert "A graph-agent run is already active" not in transcript
        assert "Generate steps again" not in transcript
        assert "graph agent is already running" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_generate_steps_disabled_while_graph_agent_runs(qapp, workspace):
    class RunningThread:
        def isRunning(self):
            return True

        def cancel(self):
            pass

    panel = AgentCanvasPanel(str(workspace))
    try:
        goal = panel.add_token_node(CanvasToken("goal", "Improve graph runs", "make running obvious"), QPointF(0, 0))
        panel._select_node(goal)
        assert panel._generate_steps_btn.isEnabled()

        panel._graph_agent_thread = RunningThread()
        panel._sync_graph_agent_controls()

        assert not panel._generate_steps_btn.isEnabled()
        assert panel._graph_chat_send_btn.isEnabled()
        assert panel._graph_chat_send_btn.text() == "Stop"
        assert not panel._graph_chat_input.isEnabled()
        assert not panel._graph_chat_clear_btn.isEnabled()
        assert panel._graph_chat_send_btn.toolTip() == "Stop the running graph agent."
        assert panel._graph_chat_clear_btn.toolTip() == "Wait for the graph agent to finish before clearing the chat."

        before = panel.graph_state()
        panel._generate_steps_for_selected_goal()

        assert panel.graph_state()["nodes"] == before["nodes"]
        assert "graph agent is already running" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_graph_chat_action_stops_running_agent(qapp, workspace):
    class RunningThread:
        def __init__(self):
            self.cancelled = False

        def isRunning(self):
            return True

        def cancel(self):
            self.cancelled = True

    panel = AgentCanvasPanel(str(workspace))
    try:
        thread = RunningThread()
        panel._graph_agent_thread = thread
        panel._graph_agent_stream_index = panel._append_graph_chat_message("Graph Agent", "Thinking...")
        panel._sync_graph_agent_controls()

        panel._on_graph_chat_action()

        assert thread.cancelled is True
        assert panel._graph_agent_stop_requested is True
        assert panel._graph_chat_send_btn.text() == "Stopping"
        assert not panel._graph_chat_send_btn.isEnabled()
        assert "stopping graph agent" in panel._inspector_lines[2].text()

        panel._on_graph_agent_done("[cancelled]")

        assert "Stopped by user." in panel._graph_chat_transcript.toPlainText()
    finally:
        panel.close()


def test_agent_canvas_generate_steps_uses_frozen_goal_scope(qapp, workspace):
    calls = []

    def runner(_prompt, _tools, execute_tool):
        panel._select_node(second_goal)
        graph = execute_tool("read_graph", {})
        calls.append(graph)
        titles = {node["title"] for node in graph["graph"]["nodes"]}
        assert graph["graph"]["scope"]["goal_id"] == first_goal.node_id
        assert "First goal" in titles
        assert "Second goal" not in titles

        result = execute_tool(
            "apply_graph_patch",
            {"operations": [{"op": "update_node", "id": second_work.node_id, "title": "Leaked edit"}]},
        )
        assert result["ok"] is False
        assert result["summary"] == "outside selected goal"
        assert "Second work" not in result["error"]
        assert result["active_scope"]["goal_id"] == first_goal.node_id
        return "Scoped."

    panel = AgentCanvasPanel(str(workspace), graph_agent_runner=runner)
    try:
        first_goal = panel.add_token_node(CanvasToken("goal", "First goal", "one"), QPointF(0, 0))
        first_work = panel.add_token_node(CanvasToken("operation", "First work", "owned"), QPointF(280, 0))
        second_goal = panel.add_token_node(CanvasToken("goal", "Second goal", "two"), QPointF(0, 220))
        second_work = panel.add_token_node(CanvasToken("operation", "Second work", "owned"), QPointF(280, 220))
        panel.connect_nodes(second_goal.node_id, second_work.node_id, "work")
        panel._select_node(first_goal)
        second_before = {
            "Second goal": (second_goal.pos().x(), second_goal.pos().y()),
            "Second work": (second_work.pos().x(), second_work.pos().y()),
        }

        panel._generate_steps_for_selected_goal()

        assert calls
        assert second_work.token.title == "Second work"
        second_after = {
            node.token.title: (node.pos().x(), node.pos().y())
            for node in panel._nodes.values()
            if node.token.title in second_before
        }
        assert second_after == second_before
        assert panel._graph_agent_scope_goal_id is None
    finally:
        panel.close()


def test_agent_canvas_graph_agent_uses_canvas_extensions(qapp, workspace, monkeypatch):
    write_extension(
        workspace,
        "canvas_agent_ext.py",
        """
        def register(registry):
            registry.canvas_context("Canvas facts", canvas_facts)
            registry.canvas_tool(
                name="canvas_note",
                description="Return a canvas note.",
                input_schema={"type": "object", "properties": {}},
                execute=lambda ctx, inputs: "note",
            )

        def canvas_facts(ctx):
            return f"canvas kind={ctx.canvas.get('kind')} nodes={len(ctx.canvas.get('graph', {}).get('nodes', []))}"
        """,
    )
    captured = {}

    class Signal:
        def connect(self, callback):
            return None

    class FakeThread:
        chunk = Signal()
        tool_called = Signal()
        tool_result = Signal()
        done = Signal()
        error = Signal()
        finished = Signal()

        def __init__(self, model, history, system, cwd, **kwargs):
            captured["model"] = model
            captured["history"] = history
            captured["system"] = system
            captured["cwd"] = cwd
            captured["kwargs"] = kwargs

        def start(self):
            captured["started"] = True

        def isRunning(self):
            return False

    monkeypatch.setattr("ui.widgets.agent_canvas.ChatThread", FakeThread)
    panel = AgentCanvasPanel(str(workspace))
    try:
        assert panel._start_graph_agent("Use the canvas extension.") is True

        system = captured["system"]()
        assert captured["kwargs"]["tool_surface"] == "canvas"
        assert "canvas_note" in captured["kwargs"]["allowed_tools"]
        assert "## Canvas Extension Context" in system
        assert "canvas kind=graph" in system

        graph = panel.read_graph_tool()
        assert graph["canvas_extension_context"] == [
            {"name": "Canvas facts", "text": "canvas kind=graph nodes=0"}
        ]
    finally:
        panel.close()


def test_agent_canvas_chat_scope_uses_last_selected_goal(qapp, workspace):
    calls = []

    def runner(_prompt, _tools, execute_tool):
        graph = execute_tool("read_graph", {})
        calls.append(graph)
        return "Scoped."

    panel = AgentCanvasPanel(str(workspace), graph_agent_runner=runner)
    try:
        first_goal = panel.add_token_node(CanvasToken("goal", "First goal", "one"), QPointF(0, 0))
        panel.add_token_node(CanvasToken("operation", "First work", "owned"), QPointF(280, 0))
        second_goal = panel.add_token_node(CanvasToken("goal", "Second goal", "two"), QPointF(0, 220))
        second_work = panel.add_token_node(CanvasToken("operation", "Second work", "owned"), QPointF(280, 220))
        panel.connect_nodes(second_goal.node_id, second_work.node_id, "work")
        panel._select_node(second_goal)
        second_goal.setSelected(False)

        panel._graph_chat_input.setText("What graph is in scope?")
        panel._send_graph_chat()

        assert calls
        titles = {node["title"] for node in calls[0]["graph"]["nodes"]}
        assert calls[0]["graph"]["scope"]["goal_id"] == second_goal.node_id
        assert "Second goal" in titles
        assert "Second work" in titles
        assert "First goal" not in titles
        assert first_goal.node_id not in calls[0]["graph"]["root_goal_ids"]
    finally:
        panel.close()


def test_agent_canvas_apply_graph_patch_adds_nodes_and_connections(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        root = panel.add_token_node(CanvasToken("goal", "Root", "intent"), QPointF(0, 0))
        root_id = panel.read_graph_tool()["graph"]["root_goal_id"]
        assert root_id == root.node_id

        result = panel.apply_graph_patch(
            {
                "operations": [
                    {"op": "add_node", "client_id": "work", "kind": "operation", "title": "Plan graph agent tools"},
                    {"op": "connect", "source": root_id, "target": "work", "source_port": "work"},
                    {"op": "set_active", "id": "work", "status": "running", "status_note": "planning"},
                ]
            }
        )

        assert result["ok"] is True
        assert result["applied_operations"] == 3
        payload = panel.read_graph_tool()
        work = next(node for node in payload["graph"]["nodes"] if node["title"] == "Plan graph agent tools")
        assert payload["graph"]["active_node_id"] == work["id"]
        assert any(edge["source_id"] == root_id and edge["target_id"] == work["id"] for edge in payload["graph"]["edges"])
    finally:
        panel.close()


def test_agent_canvas_graph_patch_places_connected_new_nodes(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        root = panel.add_token_node(CanvasToken("goal", "Root", "intent"), QPointF(0, 0))
        root_pos = root.pos()

        result = panel.apply_graph_patch(
            {
                "operations": [
                    {"op": "add_node", "client_id": "a", "kind": "operation", "title": "Map flow"},
                    {"op": "add_node", "client_id": "b", "kind": "operation", "title": "Prototype change"},
                    {"op": "add_node", "client_id": "c", "kind": "operation", "title": "Review outcome"},
                    {"op": "connect", "source": root.node_id, "target": "a", "source_port": "work"},
                    {"op": "connect", "source": root.node_id, "target": "b", "source_port": "work"},
                    {"op": "connect", "source": root.node_id, "target": "c", "source_port": "work"},
                ]
            }
        )

        assert result["ok"] is True
        created = [
            node
            for node in panel._nodes.values()
            if node.token.title in {"Map flow", "Prototype change", "Review outcome"}
        ]
        assert len(created) == 3
        assert all(node.pos().x() > root_pos.x() for node in created)
        assert sorted(round(node.pos().y() - root_pos.y()) for node in created) == [-180, 0, 180]
    finally:
        panel.close()


def test_agent_canvas_apply_graph_patch_is_atomic_when_invalid(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        root = panel.add_token_node(CanvasToken("goal", "Root", "intent"), QPointF(0, 0))
        before = panel.graph_state()
        root_id = panel.read_graph_tool()["graph"]["root_goal_id"]
        assert root_id == root.node_id

        result = panel.apply_graph_patch(
            {
                "operations": [
                    {"op": "add_node", "client_id": "work", "kind": "operation", "title": "Temporary Work"},
                    {"op": "connect", "source": root_id, "target": "work", "source_port": "work"},
                    {"op": "connect", "source": "work", "target": root_id, "source_port": "decision"},
                ]
            }
        )

        assert result["ok"] is False
        assert "cycle" in result["error"]
        assert panel.graph_state()["nodes"] == before["nodes"]
        assert panel.graph_state()["edges"] == before["edges"]
    finally:
        panel.close()


def test_agent_canvas_graph_chat_round_trips(qapp, workspace):
    source = AgentCanvasPanel(
        str(workspace),
        graph_agent_runner=lambda _prompt, _tools, _execute: "Review should attach to proof/evidence.",
    )

    try:
        source._graph_chat_input.setText("Where does review fit?")
        source._send_graph_chat()
        source._canvas_splitter.setSizes([480, 260])
        state = source.graph_state()
    finally:
        source.close()

    restored = AgentCanvasPanel(str(workspace))
    try:
        warning = restored.restore_graph_state(state)

        assert warning == ""
        transcript = restored._graph_chat_transcript.toPlainText()
        assert "Where does review fit?" in transcript
        assert "Review should attach to proof/evidence" in transcript
        assert restored._canvas_splitter.sizes() == state["graph_chat_split"]
    finally:
        restored.close()


def test_agent_canvas_stylesheet_is_balanced():
    style = agent_canvas_style()

    assert style.count("{") == style.count("}")
    assert "}}" not in style


def test_agent_canvas_delete_selected_removes_nodes_and_connections(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    try:
        source = panel.add_token_node(CanvasToken("goal", "Goal", "intent"), QPointF(0, 0))
        target = panel.add_token_node(CanvasToken("operation", "Builder", "work"), QPointF(240, 0))
        assert panel.connect_nodes(source.node_id, target.node_id) is True
        start_nodes = panel.node_count()
        start_edges = panel.edge_count()

        panel._scene.clearSelection()
        source.setSelected(True)
        panel.delete_selected()

        assert panel.node_count() == start_nodes - 1
        assert panel.edge_count() == start_edges - 1
        assert source.node_id not in panel._nodes
        assert (
            panel._delete_shortcut.key().matches(QKeySequence("Del"))
            == QKeySequence.SequenceMatch.ExactMatch
        )
    finally:
        panel.close()


def test_agent_canvas_delete_selected_removes_multiple_nodes(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    try:
        first = panel.add_token_node(CanvasToken("operation", "First", "delete me"), QPointF(0, 0))
        second = panel.add_token_node(CanvasToken("operation", "Second", "delete me too"), QPointF(260, 0))
        third = panel.add_token_node(CanvasToken("operation", "Third", "keep me"), QPointF(520, 0))
        start_nodes = panel.node_count()

        panel._scene.clearSelection()
        first.setSelected(True)
        second.setSelected(True)

        assert {node.node_id for node in panel._selected_nodes()} == {first.node_id, second.node_id}

        panel.delete_selected()

        assert panel.node_count() == start_nodes - 2
        assert first.node_id not in panel._nodes
        assert second.node_id not in panel._nodes
        assert third.node_id in panel._nodes
        assert "deleted selection" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_context_menu_delete_removes_node(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))
    node = panel.add_token_node(CanvasToken("operation", "Temp", "delete me"), QPointF(0, 0))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    def choose_delete(menu, _pos):
        return next(action for action in menu.actions() if action.text() == "Delete")

    monkeypatch.setattr(QMenu, "exec", choose_delete)

    try:
        count = panel.node_count()
        panel._show_node_menu(node, QPoint(0, 0))

        assert panel.node_count() == count - 1
        assert node.node_id not in panel._nodes
    finally:
        panel.close()


def test_agent_canvas_context_menu_delete_removes_multi_selection(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))
    first = panel.add_token_node(CanvasToken("operation", "First", "delete me"), QPointF(0, 0))
    second = panel.add_token_node(CanvasToken("operation", "Second", "delete me too"), QPointF(260, 0))
    keep = panel.add_token_node(CanvasToken("operation", "Keep", "not selected"), QPointF(520, 0))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    def choose_delete(menu, _pos):
        return next(action for action in menu.actions() if action.text() == "Delete")

    monkeypatch.setattr(QMenu, "exec", choose_delete)

    try:
        start_nodes = panel.node_count()
        panel._scene.clearSelection()
        first.setSelected(True)
        second.setSelected(True)

        panel._show_node_menu(first, QPoint(0, 0))

        assert panel.node_count() == start_nodes - 2
        assert first.node_id not in panel._nodes
        assert second.node_id not in panel._nodes
        assert keep.node_id in panel._nodes
        assert "deleted 2 nodes" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_inspector_updates_node_fields(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    node = panel.add_token_node(CanvasToken("goal", "Old", "before"), QPointF(0, 0))

    try:
        panel.edit_node(node)
        panel._edit_title.setText("New outcome")
        panel._edit_detail.setPlainText("New acceptance signal")
        panel._apply_inspector_edits()

        assert node.token == CanvasToken("goal", "New outcome", "New acceptance signal")
        assert "New outcome" in panel._selected.text()
    finally:
        panel.close()


def test_agent_canvas_node_inspector_wins_over_selected_frame(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        panel.add_token_node(CanvasToken("goal", "Root", "intent"), QPointF(0, 0))
        panel._autoformat_graph()
        frame = next(iter(panel._frames.values()))
        node = next(node for node in panel._nodes.values() if node.token.kind == "goal")

        panel._select_node(node)
        panel._selection_guard = True
        try:
            frame.setSelected(True)
        finally:
            panel._selection_guard = False

        assert frame.isSelected()
        assert node.isSelected()
        assert not panel._edit_detail.isHidden()

        panel._edit_title.setText("Editable node")
        panel._edit_detail.setPlainText("Description still updates")
        assert panel._apply_inspector_edits() is True

        assert node.token.title == "Editable node"
        assert node.token.detail == "Description still updates"
        assert frame.title != "Editable node"
        assert not panel._edit_detail.isHidden()
        assert panel._frame_color_field.isHidden()
    finally:
        panel.close()


def test_agent_canvas_frame_selection_does_not_hijack_multi_select(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        node = panel.add_token_node(CanvasToken("goal", "Root", "intent"), QPointF(0, 0))
        panel._autoformat_graph()
        frame = next(iter(panel._frames.values()))

        panel._scene.clearSelection()
        node.setSelected(True)
        frame.setSelected(True)

        assert node.isSelected()
        assert frame.isSelected()
        assert panel._selected_node() is node
        assert panel._selected_frame() is None
        assert panel._frame_color_field.isHidden()
        assert not panel._edit_detail.isHidden()
    finally:
        panel.close()


def test_agent_canvas_delete_selected_ignores_graph_frames(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    try:
        node = panel.add_token_node(CanvasToken("goal", "Root", "intent"), QPointF(0, 0))
        work = panel.add_token_node(CanvasToken("operation", "Work", "step"), QPointF(240, 0))
        panel.connect_nodes(node.node_id, work.node_id, "work")
        panel._autoformat_graph()
        frame = next(iter(panel._frames.values()))
        frame_id = frame.frame_id

        panel._scene.clearSelection()
        frame.setSelected(True)
        panel.delete_selected()

        assert frame_id in panel._frames
        assert node.node_id in panel._nodes
        assert "graph frame is layout" in panel._inspector_lines[2].text()

        work.setSelected(True)
        frame.setSelected(True)
        panel.delete_selected()

        assert frame_id in panel._frames
        assert node.node_id in panel._nodes
        assert work.node_id not in panel._nodes
    finally:
        panel.close()


def test_agent_canvas_selection_auto_persists_inspector_edits(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    first = panel.add_token_node(CanvasToken("goal", "First", "before"), QPointF(0, 0))
    second = panel.add_token_node(CanvasToken("operation", "Second", "work"), QPointF(260, 0))

    try:
        panel._select_node(first)
        panel._edit_title.setText("Persisted title")

        assert panel._select_node(second) is True
        assert panel._selected_node() is second
        assert first.token.title == "Persisted title"
        assert panel._edit_title.text() == "Second"
    finally:
        panel.close()


def test_agent_canvas_invalid_auto_persist_blocks_selection_switch(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    first = panel.add_token_node(CanvasToken("goal", "First", "before"), QPointF(0, 0))
    second = panel.add_token_node(CanvasToken("operation", "Second", "work"), QPointF(260, 0))

    try:
        panel._select_node(first)
        panel._edit_title.clear()

        assert panel._select_node(second) is False
        assert panel._selected_node() is first
        assert first.token.title == "First"
        assert "title is required" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_debounced_inspector_edit_persists(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    node = panel.add_token_node(CanvasToken("goal", "First", "before"), QPointF(0, 0))

    try:
        panel._select_node(node)
        panel._edit_title.setText("Auto saved")
        panel._flush_inspector_auto_apply()

        assert node.token.title == "Auto saved"
    finally:
        panel.close()


def test_agent_canvas_agent_action_inspector_assigns_crew(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    goal = panel.add_token_node(CanvasToken("goal", "Ship research", "make it usable"), QPointF(-280, 0))
    node = panel.add_token_node(CanvasToken("operation", "Map system", "design flow"), QPointF(0, 0))
    dod = panel.add_token_node(CanvasToken("dod", "DoD", "Research accepted"), QPointF(280, 0))
    panel.connect_nodes(goal.node_id, node.node_id, "work")
    panel.connect_nodes(node.node_id, dod.node_id, "implement")

    try:
        panel._select_node(node)
        assert panel._agent_combo.findData("coder") >= 0
        idx = panel._agent_combo.findData("architect")
        assert idx >= 0
        panel._agent_combo.setCurrentIndex(idx)
        panel._apply_inspector_edits()

        assert node.agent_id == "architect"
        assert node.agent_name == "Architect"
        goal._run_requested(goal)
        assert "Architect working" in node._status_note
    finally:
        panel.close()


def test_agent_canvas_action_inspector_selects_default_coder(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    node = panel.add_token_node(CanvasToken("operation", "Worker", ""), QPointF(0, 0))

    try:
        panel._select_node(node)
        idx = panel._agent_combo.findData("coder")
        assert idx >= 0
        panel._edit_title.setText("Worker")
        panel._agent_combo.setCurrentIndex(idx)
        panel._apply_inspector_edits()

        assert node.agent_id == "coder"
        assert node.agent_name == "Coder"
    finally:
        panel.close()


def test_agent_canvas_action_inspector_selects_crew(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    node = panel.add_token_node(CanvasToken("operation", "Worker", "generic"), QPointF(0, 0))

    try:
        panel._select_node(node)
        idx = panel._agent_combo.findData("scout")
        assert idx >= 0
        panel._edit_title.setText("Research UX for canvas")
        panel._edit_detail.setPlainText("Map rough interaction edges")
        panel._agent_combo.setCurrentIndex(idx)
        panel._apply_inspector_edits()

        assert node.agent_id == "scout"
        assert node.agent_name == "Scout"
        assert node.token == CanvasToken("operation", "Research UX for canvas", "Map rough interaction edges")
    finally:
        panel.close()


def test_agent_canvas_goal_creates_action_node_not_agent_node(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship", "intent"), QPointF(0, 0))
        creation = next(
            action
            for action in panel._all_creation_actions("goal")
            if action.title == "Add work action"
        )

        assert panel._create_connected_node(goal, creation, QPointF(280, 0))

        action = panel._nodes[panel._edges[-1].target_id]
        assert action.token.kind == "operation"
        assert action.token.title == "Implement"
        assert not any(node.token.kind == "agent" for node in panel._nodes.values())
    finally:
        panel.close()


def test_agent_canvas_scope_inspector_autocompletes_paths_editor(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    node = panel.add_token_node(CanvasToken("scope", "Files", ""), QPointF(0, 0))

    try:
        panel._select_node(node)
        assert panel._scope_path_field.isHidden()
        assert not panel._edit_detail.isHidden()
        completer = panel._edit_detail.completer()
        completer.setCompletionPrefix("main")
        assert any("src/main.py" == completer.model().index(row, 0).data() for row in range(completer.model().rowCount()))

        panel._edit_detail.setPlainText("src/main.py")
        panel._edit_title.clear()
        panel._apply_inspector_edits()

        assert node.token == CanvasToken("scope", "main.py", "src/main.py")
    finally:
        panel.close()


def test_agent_canvas_scope_paths_editor_pops_completion_while_typing(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    node = panel.add_token_node(CanvasToken("scope", "Files", ""), QPointF(0, 0))

    try:
        panel._select_node(node)
        field = panel._edit_detail
        field.resize(300, 160)
        field.setPlainText("main")
        field.moveCursor(QTextCursor.MoveOperation.End)

        assert field._show_current_path_completions() is True
        assert field.completer().completionCount() >= 1
        assert field.popup().geometry().x() == field.POPUP_MARGIN
        assert field.popup().geometry().width() == field.viewport().width() - field.POPUP_MARGIN * 2

        field._insert_completion("src/main.py")

        assert field.toPlainText() == "src/main.py"
    finally:
        panel.close()


def test_agent_canvas_scope_paths_editor_hides_completion_for_exact_path(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    node = panel.add_token_node(CanvasToken("scope", "Files", ""), QPointF(0, 0))

    try:
        panel._select_node(node)
        field = panel._edit_detail
        field.setPlainText("src/main.py")
        field.moveCursor(QTextCursor.MoveOperation.End)

        assert field._show_current_path_completions() is False
        assert field.popup().isHidden()
    finally:
        panel.close()


def test_agent_canvas_open_path_uses_live_scope_editor_text(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    opened = []
    panel.open_file_requested.connect(opened.append)
    node = panel.add_token_node(CanvasToken("scope", "Files", ""), QPointF(0, 0))

    try:
        panel._select_node(node)
        panel._edit_detail.setPlainText("src/main.py")

        panel._open_selected_scope()

        assert opened == [str((workspace / "src" / "main.py").resolve())]
        assert node.token.detail == ""
    finally:
        panel.close()


def test_agent_canvas_node_title_wraps_and_elides(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    node = panel.add_token_node(
        CanvasToken(
            "goal",
            "Design the completely new intent graph workflow for complex product systems",
            "intent",
        ),
        QPointF(0, 0),
    )
    font = QFont()
    font.setBold(True)
    font.setPointSize(10)

    try:
        lines = node._wrapped_elided_lines(node.token.title, font, node._title_rect().width(), 2)

        assert len(lines) == 2
        assert lines[-1].endswith("...")
        assert node._detail_rect().top() >= node._title_rect().bottom()
    finally:
        panel.close()


def test_agent_canvas_status_badge_does_not_cover_output_ports(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    node = panel.add_token_node(CanvasToken("operation", "Implement State Management", "Add history stack"), QPointF(0, 0))

    try:
        node.set_status("running", "working")
        status_rect = node._status_rect()
        output_rects = [
            node._port_rect(node._port_center(output_ports(node.token.kind), port.key, left=False))
            for port in output_ports(node.token.kind)
        ]

        assert status_rect.width() <= 30
        assert all(not status_rect.intersects(rect) for rect in output_rects)
        assert min(rect.top() for rect in output_rects) > status_rect.bottom()
    finally:
        panel.close()


def test_agent_canvas_output_drag_connects_to_target_input(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        source = panel.add_token_node(CanvasToken("goal", "Source", "out"), QPointF(0, 0))
        target = panel.add_token_node(CanvasToken("operation", "Target", "in"), QPointF(260, 0))
        start_edges = panel.edge_count()

        panel._begin_output_drag(source, source.output_port_scene_pos("work"), "work")
        panel._move_output_drag(source, target.input_port_scene_pos("goal"), "work")
        panel._finish_output_drag(source, target.center_scene_pos(), "work")

        assert panel.edge_count() == start_edges + 1
        edge = panel._edges[-1]
        assert (edge.source_id, edge.target_id) == (source.node_id, target.node_id)
        assert edge.kind == "requires"
        assert (edge.source_port, edge.target_port) == ("work", "goal")
    finally:
        panel.close()


def test_agent_canvas_input_drag_connects_existing_source(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        source = panel.add_token_node(CanvasToken("goal", "Goal", "intent"), QPointF(0, 0))
        target = panel.add_token_node(CanvasToken("operation", "Work", "do it"), QPointF(280, 0))
        start_edges = panel.edge_count()

        panel._begin_input_drag(target, target.input_port_scene_pos("goal"), "goal")
        panel._move_input_drag(target, source.output_port_scene_pos("work"), "goal")
        panel._finish_input_drag(target, source.center_scene_pos(), "goal")

        assert panel.edge_count() == start_edges + 1
        edge = panel._edges[-1]
        assert (edge.source_id, edge.target_id) == (source.node_id, target.node_id)
        assert (edge.kind, edge.source_port, edge.target_port) == ("requires", "work", "goal")
    finally:
        panel.close()


def test_agent_canvas_input_drag_uses_target_port_for_existing_source(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        work = panel.add_token_node(CanvasToken("operation", "Architect", "work"), QPointF(0, 0))
        goal = panel.add_token_node(CanvasToken("goal", "Subgoal", "child"), QPointF(280, 0))

        panel._begin_input_drag(goal, goal.input_port_scene_pos("decision"), "decision")
        panel._finish_input_drag(goal, work.center_scene_pos(), "decision")

        edge = panel._edges[-1]
        assert (edge.source_id, edge.target_id) == (work.node_id, goal.node_id)
        assert (edge.kind, edge.source_port, edge.target_port) == ("decides", "decision", "decision")
    finally:
        panel.close()


def test_agent_canvas_drag_to_empty_creates_work_node(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))

    def choose_work_action(menu, _pos):
        return next(action for action in menu.actions() if action.text() == "Add work action")

    monkeypatch.setattr(QMenu, "exec", choose_work_action)

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Source", "out"), QPointF(0, 0))
        start_nodes = panel.node_count()
        start_edges = panel.edge_count()

        panel._begin_output_drag(goal, goal.output_port_scene_pos("work"), "work")
        panel._finish_output_drag(goal, QPointF(320, 40), "work")

        assert panel.node_count() == start_nodes + 1
        assert panel.edge_count() == start_edges + 1
        created = panel._edges[-1]
        assert created.kind == "requires"
        assert panel._nodes[created.target_id].token.kind == "operation"
    finally:
        panel.close()


def test_agent_canvas_input_drag_to_empty_creates_upstream_node(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))

    def choose_goal(menu, _pos):
        return next(action for action in menu.actions() if action.text() == "Create: Goal")

    monkeypatch.setattr(QMenu, "exec", choose_goal)

    try:
        work = panel.add_token_node(CanvasToken("operation", "Work", "needs goal"), QPointF(260, 0))
        start_nodes = panel.node_count()
        start_edges = panel.edge_count()

        panel._begin_input_drag(work, work.input_port_scene_pos("goal"), "goal")
        panel._finish_input_drag(work, QPointF(720, 520), "goal")

        assert panel.node_count() == start_nodes + 1
        assert panel.edge_count() == start_edges + 1
        edge = panel._edges[-1]
        assert panel._nodes[edge.source_id].token.kind == "goal"
        assert edge.target_id == work.node_id
        assert (edge.kind, edge.source_port, edge.target_port) == ("requires", "work", "goal")
    finally:
        panel.close()


def test_agent_canvas_connection_drag_cancel_clears_temporary_edge(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        source = panel.add_token_node(CanvasToken("operation", "Edit", "work"), QPointF(0, 0))

        panel._begin_output_drag(source, source.output_port_scene_pos("implement"), "implement")
        assert panel._drag_edge is not None

        panel.cancel_connection_drag()

        assert panel._drag_edge is None
        assert panel._connect_anchor is None
    finally:
        panel.close()


def test_agent_canvas_named_output_ports_constrain_connections(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        operation = panel.add_token_node(CanvasToken("operation", "Edit", "work"), QPointF(0, 0))
        decision = panel.add_token_node(CanvasToken("decision", "Choice", "pick path"), QPointF(260, 0))

        assert panel.connect_nodes(operation.node_id, decision.node_id, "evidence") is False
        assert panel.connect_nodes(operation.node_id, decision.node_id, "decision") is True
        edge = panel._edges[-1]
        assert (edge.kind, edge.source_port, edge.target_port) == ("decides", "decision", "reason")
    finally:
        panel.close()


def test_agent_canvas_scope_can_context_operation(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        scope = panel.add_token_node(CanvasToken("scope", "main.py", "src/main.py"), QPointF(0, 0))
        operation = panel.add_token_node(CanvasToken("operation", "Coder", "Default coding agent"), QPointF(260, 0))

        assert [port.label for port in input_ports("operation")] == ["GOAL", "READ", "CTX", "GUIDE", "FEED"]
        assert panel.connect_nodes(scope.node_id, operation.node_id, "read") is True
        edge = panel._edges[-1]
        assert (edge.kind, edge.source_port, edge.target_port) == ("reads", "read", "scope")
    finally:
        panel.close()


def test_agent_canvas_scope_can_create_agent_action(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        scope = panel.add_token_node(CanvasToken("scope", "main.py", "src/main.py"), QPointF(0, 0))
        creation = next(
            action
            for action in panel._all_creation_actions("scope")
            if action.title == "Read this scope"
        )

        assert panel._create_connected_node(scope, creation, QPointF(260, 0))

        edge = panel._edges[-1]
        action = panel._nodes[edge.target_id]
        assert action.token.kind == "operation"
        assert (edge.kind, edge.source_port, edge.target_port) == ("reads", "read", "scope")
    finally:
        panel.close()


def test_agent_canvas_work_decision_can_connect_to_subgoal(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Goal", "intent"), QPointF(0, 0))
        subgoal = panel.add_token_node(CanvasToken("goal", "Subgoal 1", "child"), QPointF(260, -100))
        work = panel.add_token_node(CanvasToken("operation", "Design", "architect work"), QPointF(260, 100))
        work.set_agent("architect", "Architect")

        assert panel.connect_nodes(goal.node_id, subgoal.node_id, "split") is True
        assert panel.connect_nodes(goal.node_id, work.node_id, "work") is True
        assert panel.connect_nodes(work.node_id, subgoal.node_id, "decision") is True

        edge = panel._edges[-1]
        assert (edge.kind, edge.source_port, edge.target_port) == ("decides", "decision", "decision")
    finally:
        panel.close()


def test_agent_canvas_work_decision_can_connect_to_context(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        work = panel.add_token_node(CanvasToken("operation", "Decide approach", "choose path"), QPointF(0, 0))
        context = panel.add_token_node(CanvasToken("context", "Durable approach", "remember choice"), QPointF(270, 0))

        assert panel.connect_nodes(work.node_id, context.node_id, "decision") is True

        edge = panel._edges[-1]
        assert (edge.kind, edge.source_port, edge.target_port) == ("context", "decision", "decision")
    finally:
        panel.close()


def test_agent_canvas_operation_decision_can_connect_to_existing_goal(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        operation = panel.add_token_node(CanvasToken("operation", "Architect", "System Design"), QPointF(0, 0))
        goal = panel.add_token_node(CanvasToken("goal", "Subgoal", "child"), QPointF(270, 0))
        operation.set_agent("architect", "Architect")

        panel._begin_output_drag(operation, operation.output_port_scene_pos("decision"), "decision")
        panel._finish_output_drag(operation, goal.input_port_scene_pos("decision") + QPointF(-24, 0), "decision")

        edge = panel._edges[-1]
        assert (edge.source_id, edge.target_id) == (operation.node_id, goal.node_id)
        assert (edge.kind, edge.source_port, edge.target_port) == ("decides", "decision", "decision")
    finally:
        panel.close()


def test_agent_canvas_approved_operation_produces_decision_contract(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Calculator", "Build calculator"), QPointF(0, 0))
        choose = panel.add_token_node(
            CanvasToken("operation", "Choose parser", "Select the expression parsing approach."),
            QPointF(260, 0),
        )
        decision = panel.add_token_node(
            CanvasToken("decision", "Parser approach", "Choose parser based on precedence, functions, and dependency risk."),
            QPointF(520, 0),
        )
        implement = panel.add_token_node(
            CanvasToken("operation", "Implement engine", "Build evaluator from the accepted parser decision."),
            QPointF(780, 0),
        )
        dod = panel.add_token_node(CanvasToken("dod", "Done", "Engine behavior is accepted."), QPointF(1040, 0))

        assert panel.connect_nodes(goal.node_id, choose.node_id, "work") is True
        assert panel.connect_nodes(choose.node_id, decision.node_id, "decision") is True
        assert panel.connect_nodes(decision.node_id, implement.node_id, "guide") is True
        assert panel.connect_nodes(implement.node_id, dod.node_id, "implement") is True

        engine = GraphRunEngine()
        plan = engine.compile(panel.graph_state(), goal.node_id)
        assert choose.node_id in engine.ready_operation_ids(panel.graph_state(), plan)
        assert implement.node_id not in engine.ready_operation_ids(panel.graph_state(), plan)

        choose.set_status("review", "awaiting acceptance")
        panel._node_run_history[choose.node_id] = [
            {
                "id": "run-choice",
                "kind": "operation",
                "role": "Architect",
                "status": "review",
                "started_at": "2026-06-21 10:00",
                "prompt": "Choose parser",
                "content": "Choice: use shunting-yard. Reason: it handles precedence, parentheses, and functions without a dependency.",
                "artifact_ref": ".aichs/canvas/default/artifacts/parser.md",
                "artifact_title": "Parser choice",
                "tools": [],
                "touched_files": [],
            }
        ]
        panel._select_node(choose)
        panel._accept_selected_run_node()

        assert choose.status == "done"
        assert decision.status == "done"
        assert "Decision contract:" in decision.token.detail
        assert "Choose parser based on precedence" in decision.token.detail
        assert "use shunting-yard" in panel._last_run_output(decision.node_id)
        assert implement.node_id in engine.ready_operation_ids(panel.graph_state(), plan)
    finally:
        panel.close()


def test_agent_canvas_graph_state_round_trips(qapp, workspace):
    source = AgentCanvasPanel(str(workspace))

    try:
        source_goal = source.add_token_node(CanvasToken("goal", "Persist this", "design"), QPointF(0, 0))
        work = source.add_token_node(CanvasToken("operation", "Build", "implement"), QPointF(300, 20))
        work.set_agent("architect", "Architect")
        source.connect_nodes(source_goal.node_id, work.node_id, "work")
        source._set_active_node(work, "running", "Architect working")
        state = source.graph_state()
    finally:
        source.close()

    restored = AgentCanvasPanel(str(workspace))
    try:
        warning = restored.restore_graph_state(state)

        assert warning == ""
        assert any(token.title == "Persist this" for token in restored.graph_items())
        assert any(node.agent_name == "Architect" for node in restored._nodes.values())
        assert restored.edge_count() == 1
        assert {edge.kind for edge in restored._edges} >= {"requires"}
        active = restored._nodes[restored._active_node_id]
        assert active.token.title == "Build"
        assert active.status == "running"
    finally:
        restored.close()


def test_agent_canvas_empty_graph_state_round_trips(qapp, workspace):
    source = AgentCanvasPanel(str(workspace))

    try:
        state = source.graph_state()
    finally:
        source.close()

    restored = AgentCanvasPanel(str(workspace))
    try:
        warning = restored.restore_graph_state(state)

        assert warning == ""
        assert restored.node_count() == 0
        assert restored.edge_count() == 0
        assert restored._selected.text() == "Selected: None"
    finally:
        restored.close()


def test_agent_canvas_undo_redo_restores_graph_nodes(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        panel.add_token_node(CanvasToken("goal", "Undo me", "temporary"), QPointF(0, 0))
        assert any(token.title == "Undo me" for token in panel.graph_items())

        panel.undo_graph_change()
        assert not any(token.title == "Undo me" for token in panel.graph_items())

        panel.redo_graph_change()
        assert any(token.title == "Undo me" for token in panel.graph_items())
    finally:
        panel.close()


def test_agent_canvas_graph_patch_is_undoable(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        result = panel.apply_graph_patch(
            {
                "operations": [
                    {"op": "add_node", "client_id": "goal", "kind": "goal", "title": "Patch goal", "detail": "undoable"}
                ]
            }
        )
        assert result["ok"] is True
        assert any(token.title == "Patch goal" for token in panel.graph_items())

        panel.undo_graph_change()
        assert not any(token.title == "Patch goal" for token in panel.graph_items())
    finally:
        panel.close()


def test_agent_canvas_blocks_connections_that_create_cycle(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Goal", "intent"), QPointF(0, 0))
        first = panel.add_token_node(CanvasToken("operation", "First Work", "implementation"), QPointF(280, 0))
        second = panel.add_token_node(CanvasToken("operation", "Second Work", "follow-up"), QPointF(560, 0))

        assert panel.connect_nodes(goal.node_id, first.node_id, "work") is True
        assert panel.connect_nodes(first.node_id, second.node_id, "implement") is True
        assert panel._cycle_warning.isHidden()
        start_edges = panel.edge_count()

        assert panel.connect_nodes(second.node_id, first.node_id, "implement") is False

        assert panel.edge_count() == start_edges
        assert panel._cycle_warning.isHidden()
        assert "blocked cycle" in panel._inspector_lines[2].text()
        assert "Second Work" in panel._inspector_lines[2].text()
        assert "First Work" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_autoformat_lays_out_directed_flow(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Goal", "intent"), QPointF(600, 120))
        work = panel.add_token_node(CanvasToken("operation", "Work", "implementation"), QPointF(-200, -80))
        proof = panel.add_token_node(CanvasToken("evidence", "Proof", "tests"), QPointF(180, 360))
        panel.connect_nodes(goal.node_id, work.node_id, "work")
        panel.connect_nodes(work.node_id, proof.node_id, "evidence")

        panel._autoformat_graph()

        assert panel._autoformat_btn.text() == "Autoformat"
        assert goal.pos().x() < work.pos().x() < proof.pos().x()
        assert "autoformatted graph" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_autoformat_frames_graph_islands(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        root = panel.add_token_node(CanvasToken("goal", "Root", "intent"), QPointF(0, 0))

        panel._autoformat_graph()

        assert len(panel._frames) == 1
        frame = next(iter(panel._frames.values()))
        assert frame.title == root.token.title
        assert frame.root_id == root.node_id
        assert root.node_id in frame.node_ids
        assert frame.scene_rect().contains(root.sceneBoundingRect())
        assert "autoformatted graph" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_frame_inspector_edits_persist(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        panel.add_token_node(CanvasToken("goal", "Root", "intent"), QPointF(0, 0))
        panel._autoformat_graph()
        frame = next(iter(panel._frames.values()))

        panel._select_frame(frame)
        assert panel._edit_detail.isHidden()
        assert not panel._frame_color_field.isHidden()
        panel._edit_title.setText("Run UX Map")
        panel._frame_color_field.setText("#123456")
        assert panel._apply_inspector_edits() is True

        state = panel.graph_state()
        assert state["frames"][0]["title"] == "Run UX Map"
        assert state["frames"][0]["color"] == "#123456"
        assert state["selected_frame_id"] == frame.frame_id
    finally:
        panel.close()

    restored = AgentCanvasPanel(str(workspace))
    try:
        warning = restored.restore_graph_state(state)
        assert warning == ""
        restored_frame = next(iter(restored._frames.values()))
        assert restored_frame.title == "Run UX Map"
        assert restored_frame.color == "#123456"
        assert restored.graph_state()["selected_frame_id"] == restored_frame.frame_id
    finally:
        restored.close()


def test_agent_canvas_nested_goal_frame_is_selectable_above_parent(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        root = panel.add_token_node(CanvasToken("goal", "Root", "intent"), QPointF(0, 0))
        subgoal = panel.add_token_node(CanvasToken("goal", "Subgoal", "intent"), QPointF(300, 0))
        work = panel.add_token_node(CanvasToken("operation", "Work", "step"), QPointF(600, 0))
        panel.connect_nodes(root.node_id, subgoal.node_id, "split")
        panel.connect_nodes(subgoal.node_id, work.node_id, "work")

        panel._autoformat_graph()

        parent = next(frame for frame in panel._frames.values() if frame.root_id == root.node_id)
        child = next(frame for frame in panel._frames.values() if frame.root_id == subgoal.node_id)
        assert child.zValue() > parent.zValue()
        frame_hits = [item for item in panel._scene.items(child.scene_rect().center()) if isinstance(item, type(child))]
        assert frame_hits[0] is child
        assert parent in frame_hits
    finally:
        panel.close()


def test_agent_canvas_autoformat_places_loose_files_after_root_goal(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        root = panel.add_token_node(CanvasToken("goal", "Root", "intent"), QPointF(0, 0))
        first_file = panel.add_token_node(CanvasToken("scope", "Files A", "src/a.py"), QPointF(-500, 0))
        second_file = panel.add_token_node(CanvasToken("scope", "Files B", "src/b.py"), QPointF(-300, 200))

        panel._autoformat_graph()

        assert root.pos().x() < first_file.pos().x()
        assert root.pos().x() < second_file.pos().x()
        assert first_file.pos().x() == second_file.pos().x()
    finally:
        panel.close()


def test_agent_canvas_autoformat_centers_fanout_around_parent(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        panel._clear_graph()
        root = panel.add_token_node(CanvasToken("goal", "Root", "intent"), QPointF(0, 0))
        first = panel.add_token_node(CanvasToken("operation", "First", "work"), QPointF(0, 240))
        second = panel.add_token_node(CanvasToken("operation", "Second", "work"), QPointF(0, 360))
        third = panel.add_token_node(CanvasToken("operation", "Third", "work"), QPointF(0, 480))
        panel.connect_nodes(root.node_id, first.node_id, "work")
        panel.connect_nodes(root.node_id, second.node_id, "work")
        panel.connect_nodes(root.node_id, third.node_id, "work")

        panel._autoformat_graph()

        child_ys = sorted([first.pos().y(), second.pos().y(), third.pos().y()])
        assert child_ys[0] < root.pos().y()
        assert child_ys[1] == root.pos().y()
        assert child_ys[2] > root.pos().y()
    finally:
        panel.close()


def test_agent_canvas_blocks_evidence_to_goal_shortcut(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Goal", "intent"), QPointF(400, 100))
        work = panel.add_token_node(CanvasToken("operation", "Work", "implementation"), QPointF(-120, -80))
        proof = panel.add_token_node(CanvasToken("evidence", "Proof", "tests"), QPointF(80, 280))
        panel.connect_nodes(goal.node_id, work.node_id, "work")
        panel.connect_nodes(work.node_id, proof.node_id, "evidence")
        start_edges = panel.edge_count()

        assert panel.connect_nodes(proof.node_id, goal.node_id, "supports") is False

        assert panel.edge_count() == start_edges
        assert panel._cycle_warning.isHidden()
        assert "cannot connect evidence -> goal" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_autoformat_pushes_node_behind_deeper_inputs(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Goal", "intent"), QPointF(500, 200))
        work = panel.add_token_node(CanvasToken("operation", "Work", "implementation"), QPointF(-240, -100))
        proof = panel.add_token_node(CanvasToken("evidence", "Proof", "tests"), QPointF(140, 320))
        review = panel.add_token_node(CanvasToken("operation", "Review", "follow-up"), QPointF(-100, 80))
        panel.connect_nodes(goal.node_id, work.node_id, "work")
        panel.connect_nodes(goal.node_id, review.node_id, "work")
        panel.connect_nodes(work.node_id, proof.node_id, "evidence")
        panel.connect_nodes(proof.node_id, review.node_id, "feedback")

        panel._autoformat_graph()

        assert goal.pos().x() < work.pos().x() < proof.pos().x() < review.pos().x()
    finally:
        panel.close()


def test_agent_canvas_invalid_graph_state_warns_and_resets(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        warning = panel.restore_graph_state({
            "format": "aichs-agent-canvas/v1",
            "nodes": [
                {"id": 1, "kind": "goal", "title": "Goal", "x": 0, "y": 0},
                {"id": 2, "kind": "scope", "title": "Files", "x": 220, "y": 0},
            ],
            "edges": [
                {
                    "source_id": 1,
                    "target_id": 2,
                    "kind": "scopes",
                    "source_port": "scope",
                    "target_port": "scope_in",
                }
            ],
        })

        assert "no longer supports" in warning
        assert panel.node_count() == 0
        assert panel.edge_count() == 0
        assert panel._selected.text() == "Selected: None"
    finally:
        panel.close()


def test_agent_canvas_cyclic_graph_state_warns_and_resets(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        warning = panel.restore_graph_state({
            "format": "aichs-agent-canvas/v1",
            "nodes": [
                {"id": 1, "kind": "goal", "title": "Goal", "x": 0, "y": 0},
                {"id": 2, "kind": "operation", "title": "Work", "x": 260, "y": 0},
                {"id": 3, "kind": "decision", "title": "Decision", "x": 520, "y": 0},
            ],
            "edges": [
                {
                    "source_id": 1,
                    "target_id": 2,
                    "kind": "requires",
                    "source_port": "work",
                    "target_port": "goal",
                },
                {
                    "source_id": 2,
                    "target_id": 3,
                    "kind": "decides",
                    "source_port": "decision",
                    "target_port": "reason",
                },
                {
                    "source_id": 3,
                    "target_id": 2,
                    "kind": "guides",
                    "source_port": "guide",
                    "target_port": "decision",
                },
            ],
        })

        assert "contains a cycle" in warning
        assert panel.node_count() == 0
        assert panel.edge_count() == 0
    finally:
        panel.close()


def test_agent_canvas_goal_outputs_do_not_include_scope(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Goal", "intent"), QPointF(0, 0))
        scope = panel.add_token_node(CanvasToken("scope", "main.py", "src/main.py"), QPointF(270, 0))

        assert [port.key for port in output_ports("goal")] == ["split", "work", "context"]
        assert panel.connect_nodes(goal.node_id, scope.node_id, "scope") is False
    finally:
        panel.close()


def test_agent_canvas_agent_action_implement_can_close_to_dod(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        work = panel.add_token_node(CanvasToken("operation", "Work", "do it"), QPointF(0, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Accepted"), QPointF(260, 0))

        assert [port.key for port in output_ports("operation")] == ["implement", "decision", "evidence"]
        assert [port.key for port in input_ports("dod")] == ["work", "proof", "decision"]
        assert panel.connect_nodes(work.node_id, dod.node_id, "implement") is True

        edge = panel._edges[-1]
        assert (edge.kind, edge.source_port, edge.target_port) == ("then", "implement", "work")
    finally:
        panel.close()


def test_agent_canvas_goal_closes_through_dod(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Goal", "intent"), QPointF(0, 0))
        work = panel.add_token_node(CanvasToken("operation", "Work", "do it"), QPointF(260, 0))
        proof = panel.add_token_node(CanvasToken("evidence", "Proof", "tests"), QPointF(520, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Tests pass"), QPointF(780, 0))

        assert panel.connect_nodes(goal.node_id, work.node_id, "work") is True
        assert panel.connect_nodes(work.node_id, dod.node_id, "implement") is True
        assert panel.connect_nodes(work.node_id, proof.node_id, "evidence") is True
        assert panel.connect_nodes(proof.node_id, dod.node_id, "supports") is True
        assert panel.connect_nodes(proof.node_id, goal.node_id, "supports") is False

        closing_edge = panel._edges[-1]
        assert (closing_edge.kind, closing_edge.source_port, closing_edge.target_port) == (
            "satisfies",
            "supports",
            "proof",
        )
        assert output_ports("dod") == ()
    finally:
        panel.close()


def test_main_window_persists_agent_canvas_graph(qapp, workspace, quiet_file_language):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    first = MainWindow(startup_workspace=str(workspace))
    try:
        first._agent_canvas.add_token_node(CanvasToken("goal", "Saved design", "restore me"), QPointF(120, 80))
        first._save_agent_canvas()
    finally:
        first.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)

    state, warning = load_agent_canvas(workspace)
    assert warning == ""
    assert state is not None
    assert canvas_path(workspace).exists()

    second = MainWindow(startup_workspace=str(workspace))
    try:
        assert any(token.title == "Saved design" for token in second._agent_canvas.graph_items())
    finally:
        second.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_agent_canvas_breaks_selected_goal_into_split_goal_edges(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Improve onboarding", "intent"), QPointF(0, 0))
        goal.setSelected(True)
        start_nodes = panel.node_count()
        start_edges = panel.edge_count()

        panel._break_down_selected()

        assert panel.node_count() == start_nodes + 3
        assert panel.edge_count() == start_edges + 3
        assert [token.kind for token in panel.graph_items()].count("goal") >= 4
        assert [edge.kind for edge in panel._edges].count("split") >= 3
    finally:
        panel.close()


def test_agent_canvas_rejects_invalid_connection(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        first = panel.add_token_node(CanvasToken("scope", "one.py", "one.py"), QPointF(0, 0))
        second = panel.add_token_node(CanvasToken("scope", "two.py", "two.py"), QPointF(260, 0))
        start_edges = panel.edge_count()

        assert panel.connect_nodes(first.node_id, second.node_id) is False
        assert panel.edge_count() == start_edges
        assert "cannot connect scope -> scope" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_scope_nodes_open_existing_editor(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    opened = []
    panel.open_file_requested.connect(opened.append)

    try:
        panel._add_file_nodes(["src/main.py"], QPointF(12, 34))

        assert opened == [str((workspace / "src" / "main.py").resolve())]
        assert any(token.kind == "scope" and token.detail == "src/main.py" for token in panel.graph_items())
    finally:
        panel.close()


def test_agent_canvas_scope_context_menu_prioritizes_open_path(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))
    opened = []
    panel.open_file_requested.connect(opened.append)

    def choose_first(menu, _pos):
        visible_actions = [action for action in menu.actions() if not action.isSeparator()]
        assert visible_actions[0].text() == "Open Path"
        return visible_actions[0]

    monkeypatch.setattr(QMenu, "exec", choose_first)

    try:
        node = panel.add_token_node(CanvasToken("scope", "main.py", "src/main.py"), QPointF(0, 0))
        panel._show_node_menu(node, QPoint(0, 0))

        assert opened == [str((workspace / "src" / "main.py").resolve())]
        assert "opened src/main.py" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_scope_node_path_behaves_like_link(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    opened = []
    panel.open_file_requested.connect(opened.append)

    try:
        node = panel.add_token_node(CanvasToken("scope", "main.py", "src/main.py"), QPointF(0, 0))

        assert node._file_link_at(node._detail_rect().center())
        assert "Click the path to open" in node.toolTip()

        node._file_open_requested(node)

        assert opened == [str((workspace / "src" / "main.py").resolve())]
    finally:
        panel.close()


def test_agent_canvas_keyboard_enter_opens_selected_scope(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    opened = []
    panel.open_file_requested.connect(opened.append)

    try:
        node = panel.add_token_node(CanvasToken("scope", "main.py", "src/main.py"), QPointF(0, 0))
        node.setSelected(True)
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)

        panel._graph.keyPressEvent(event)

        assert event.isAccepted()
        assert opened == [str((workspace / "src" / "main.py").resolve())]
    finally:
        panel.close()


def test_agent_canvas_record_file_activity_adds_scope_to_active_operation(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        operation = panel.add_token_node(CanvasToken("operation", "Edit", "active work"), QPointF(0, 0))
        operation.setSelected(True)
        start_nodes = panel.node_count()
        start_edges = panel.edge_count()

        scope = panel.record_file_activity(str(workspace / "src" / "main.py"))

        assert panel.node_count() == start_nodes + 1
        assert panel.edge_count() == start_edges
        assert scope.token == CanvasToken("scope", "main.py", "src/main.py")
        assert operation.status == "running"
        assert scope.status == "changed"
        assert panel._active_node_id == operation.node_id
        assert not any(edge.source_id == operation.node_id and edge.target_id == scope.node_id for edge in panel._edges)
    finally:
        panel.close()


def test_agent_canvas_run_file_activity_groups_files_and_autoformats(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace), run_agent_runner=lambda *_args: None)

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship flow", "make it usable"), QPointF(0, 0))
        operation = panel.add_token_node(CanvasToken("operation", "Build flow", "make it real"), QPointF(280, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Flow accepted"), QPointF(560, 0))
        panel.connect_nodes(goal.node_id, operation.node_id, "work")
        panel.connect_nodes(operation.node_id, dod.node_id, "implement")
        panel._run_node(goal)
        start_nodes = panel.node_count()

        panel._record_run_file_activity(operation.node_id, str(workspace / "src" / "main.py"))
        panel._record_run_file_activity(operation.node_id, str(workspace / "README.md"))

        read_scopes = [
            panel._nodes[edge.source_id]
            for edge in panel._edges
            if edge.target_id == operation.node_id
            and edge.source_id in panel._nodes
            and panel._nodes[edge.source_id].token.kind == "scope"
            and edge.source_port == "read"
        ]
        assert panel.node_count() == start_nodes + 1
        assert len(read_scopes) == 1
        assert read_scopes[0].token.title == "2 changed files"
        assert panel._scope_refs(read_scopes[0].token.detail) == ["src/main.py", "README.md"]
        assert read_scopes[0].status == "changed"
        assert "autoformatted active graph" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_run_file_activity_autoformat_preserves_view(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace), run_agent_runner=lambda *_args: None)

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship flow", "make it usable"), QPointF(0, 0))
        operation = panel.add_token_node(CanvasToken("operation", "Build flow", "make it real"), QPointF(280, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Flow accepted"), QPointF(560, 0))
        panel.connect_nodes(goal.node_id, operation.node_id, "work")
        panel.connect_nodes(operation.node_id, dod.node_id, "implement")
        panel._run_node(goal)
        panel._graph.zoom_by(1.18)
        panel._graph.centerOn(QPointF(420, 180))
        before_zoom = panel._graph._zoom
        before_center = panel._graph.mapToScene(panel._graph.viewport().rect().center())

        panel._record_run_file_activity(operation.node_id, str(workspace / "src" / "main.py"))

        after_center = panel._graph.mapToScene(panel._graph.viewport().rect().center())
        assert panel._graph._zoom == before_zoom
        assert abs(after_center.x() - before_center.x()) < 1.0
        assert abs(after_center.y() - before_center.y()) < 1.0
        assert "autoformatted active graph" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_run_selected_goal_marks_goal_active(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace), run_agent_runner=lambda *_args: None)

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship flow", "make it usable"), QPointF(0, 0))
        work = panel.add_token_node(CanvasToken("operation", "Build flow", "make it real"), QPointF(280, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Flow accepted"), QPointF(560, 0))
        panel.connect_nodes(goal.node_id, work.node_id, "work")
        panel.connect_nodes(work.node_id, dod.node_id, "implement")
        start_nodes = panel.node_count()
        goal.setSelected(True)
        work.setSelected(False)

        panel._run_selected()

        assert panel.node_count() == start_nodes
        assert panel._active_node_id == work.node_id
        assert goal.status == "running"
        assert work.status == "running"
        assert "Active: Build flow" in panel._goal.text()
    finally:
        panel.close()


def test_agent_canvas_goal_has_inner_run_button(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace), run_agent_runner=lambda *_args: None)

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship flow", "make it usable"), QPointF(0, 0))
        work = panel.add_token_node(CanvasToken("operation", "Build flow", "make it real"), QPointF(280, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Flow accepted"), QPointF(560, 0))
        panel.connect_nodes(goal.node_id, work.node_id, "work")
        panel.connect_nodes(work.node_id, dod.node_id, "implement")
        start_nodes = panel.node_count()

        assert goal._run_button_at(goal._run_button_rect().center())
        goal._run_requested(goal)

        assert panel.node_count() == start_nodes
        assert panel._active_node_id == work.node_id
        assert goal.status == "running"
        assert work.status == "running"
    finally:
        panel.close()


def test_agent_canvas_work_node_has_no_run_button(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship flow", "make it usable"), QPointF(0, 0))
        work = panel.add_token_node(CanvasToken("operation", "Build flow", "make it real"), QPointF(280, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Flow accepted"), QPointF(560, 0))
        panel.connect_nodes(goal.node_id, work.node_id, "work")
        panel.connect_nodes(work.node_id, dod.node_id, "implement")

        assert goal._run_button_at(goal._run_button_rect().center())
        assert not work._run_button_at(work._run_button_rect().center())

        work.setSelected(True)
        goal.setSelected(False)
        panel._run_selected()

        assert work.status == "idle"
        assert "runs start from goals" in panel._inspector_lines[2].text()

        work.set_status("running", "Coder working")
        panel._sync_run_controls()

        assert not work._run_button_at(work._run_button_rect().center())
    finally:
        panel.close()


def test_agent_canvas_goal_without_dod_is_incomplete_for_run(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship flow", "make it usable"), QPointF(0, 0))
        work = panel.add_token_node(CanvasToken("operation", "Build flow", "make it real"), QPointF(280, 0))
        panel.connect_nodes(goal.node_id, work.node_id, "work")

        assert not goal._run_button_at(goal._run_button_rect().center())
        panel._run_node(goal)
        assert goal.status == "idle"
        assert work.status == "idle"
        assert "no DoD acceptance node" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_naked_goal_has_no_run_button(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Explore", "not executable yet"), QPointF(0, 0))

        assert not goal._run_button_at(goal._run_button_rect().center())
        panel._run_node(goal)
        assert goal.status == "idle"
        assert "no runnable action" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_generate_steps_creates_runnable_goal_branch(qapp, workspace):
    def runner(_prompt, _tools, execute_tool):
        assert "workflow design only" in _prompt
        assert "Do not research the repo" in _prompt
        assert "Prefer 3-5 useful new nodes total" in _prompt
        assert "Reuse existing nodes where possible" in _prompt
        graph = execute_tool("read_graph", {})
        goal_id = graph["graph"]["selected_node_id"]
        result = execute_tool(
            "apply_graph_patch",
            {
                "operations": [
                    {"op": "add_node", "client_id": "map", "kind": "operation", "title": "Map run UX", "agent_id": "coder", "agent_name": "Coder"},
                    {"op": "add_node", "client_id": "bridge", "kind": "operation", "title": "Wire graph agent", "agent_id": "coder", "agent_name": "Coder"},
                    {"op": "add_node", "client_id": "proof", "kind": "evidence", "title": "Run UX proof", "detail": "Focused test and manual behavior proof."},
                    {"op": "add_node", "client_id": "dod", "kind": "dod", "title": "DoD", "detail": "Run UX accepted"},
                    {"op": "connect", "source": goal_id, "target": "map", "source_port": "work"},
                    {"op": "connect", "source": "map", "target": "bridge", "source_port": "implement"},
                    {"op": "connect", "source": "bridge", "target": "proof", "source_port": "evidence"},
                    {"op": "connect", "source": "proof", "target": "dod", "source_port": "supports"},
                ]
            },
        )
        assert result["ok"] is True
        return "Generated graph-agent steps."

    panel = AgentCanvasPanel(str(workspace), graph_agent_runner=runner)

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Improve graph runs", "make running obvious"), QPointF(0, 0))
        assert not panel._generate_steps_btn.isHidden()
        assert panel._generate_steps_btn.text() == "Generate Steps"
        assert not goal._run_button_at(goal._run_button_rect().center())
        panel._append_graph_chat_message("Graph Agent", "Old design output.")

        panel._generate_steps_for_selected_goal()

        operations = [node for node in panel._nodes.values() if node.token.kind == "operation"]
        assert [node.token.title for node in operations] == [
            "Map run UX",
            "Wire graph agent",
        ]
        assert any(node.token.title == "Run UX proof" for node in panel._nodes.values())
        dod = next(node for node in panel._nodes.values() if node.token.title == "DoD")
        assert any(edge.target_id == dod.node_id for edge in panel._edges)
        goal = next(node for node in panel._nodes.values() if node.token.title == "Improve graph runs")
        assert goal._run_button_at(goal._run_button_rect().center())
        transcript = panel._graph_chat_transcript.toPlainText()
        assert "Old design output." not in transcript
        assert "Generated graph-agent steps." in transcript
    finally:
        panel.close()


def test_agent_canvas_generate_steps_does_not_duplicate_existing_branch(qapp, workspace):
    calls = 0

    def runner(_prompt, _tools, execute_tool):
        nonlocal calls
        calls += 1
        graph = execute_tool("read_graph", {})
        goal_id = graph["graph"]["selected_node_id"]
        result = execute_tool(
            "apply_graph_patch",
            {
                "operations": [
                    {"op": "add_node", "client_id": "map", "kind": "operation", "title": "Map run UX", "agent_id": "coder", "agent_name": "Coder"},
                    {"op": "add_node", "client_id": "dod", "kind": "dod", "title": "DoD", "detail": "Run UX accepted"},
                    {"op": "connect", "source": goal_id, "target": "map", "source_port": "work"},
                    {"op": "connect", "source": "map", "target": "dod", "source_port": "implement"},
                ]
            },
        )
        assert result["ok"] is True
        return "Generated."

    panel = AgentCanvasPanel(str(workspace), graph_agent_runner=runner)

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Improve graph runs", "make running obvious"), QPointF(0, 0))
        panel._generate_steps_for_selected_goal()
        count = panel.node_count()
        edge_count = panel.edge_count()
        goal = next(node for node in panel._nodes.values() if node.token.title == "Improve graph runs")
        panel._select_node(goal)

        assert panel._generate_steps_btn.text() == "Show Steps"
        panel._generate_steps_for_selected_goal()

        assert panel.node_count() == count
        assert panel.edge_count() == edge_count
        assert calls == 1
        assert panel._selected_node().token.kind == "operation"
    finally:
        panel.close()


def test_agent_canvas_done_agent_action_triggers_next_ready_action(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace), run_agent_runner=lambda node, _prompt, kind: f"{kind} finished: {node.token.title}")

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship flow", "make it usable"), QPointF(0, 0))
        first = panel.add_token_node(CanvasToken("operation", "Build flow", "make it real"), QPointF(280, 0))
        second = panel.add_token_node(CanvasToken("operation", "Verify flow", "prove it"), QPointF(560, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Flow accepted"), QPointF(840, 0))
        panel.connect_nodes(goal.node_id, first.node_id, "work")
        panel.connect_nodes(first.node_id, second.node_id, "implement")
        panel.connect_nodes(second.node_id, dod.node_id, "implement")

        panel._run_node(goal)
        assert first.status == "review"
        assert second.status == "idle"
        assert panel._node_run_history[first.node_id]
        assert panel._selected_node() is first

        panel._select_node(first)
        panel._accept_selected_run_node()
        assert first.status == "done"
        assert second.status == "review"
        assert panel._active_node_id == second.node_id
        assert panel._selected_node() is second

        panel._select_node(second)
        panel._accept_selected_run_node()
        assert second.status == "done"
        assert goal.status == "running"
        assert panel._run_session is not None
        assert dod.status == "review"

        panel._select_node(dod)
        panel._accept_selected_run_node()
        assert goal.status == "done"
        assert panel._run_session is None
    finally:
        panel.close()


def test_agent_canvas_parallel_run_starts_multiple_ready_actions(qapp, workspace):
    settings = SettingsStore()
    settings.save({"canvas_run_mode": "parallel", "canvas_parallel_limit": 2})
    panel = AgentCanvasPanel(
        str(workspace),
        settings=settings,
        run_agent_runner=lambda node, _prompt, kind: f"{kind} finished: {node.token.title}",
    )

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship flow", "make it usable"), QPointF(0, 0))
        first = panel.add_token_node(CanvasToken("operation", "Build left", "make branch one"), QPointF(280, -80))
        second = panel.add_token_node(CanvasToken("operation", "Build right", "make branch two"), QPointF(280, 80))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Flow accepted"), QPointF(560, 0))
        panel.connect_nodes(goal.node_id, first.node_id, "work")
        panel.connect_nodes(goal.node_id, second.node_id, "work")
        panel.connect_nodes(first.node_id, dod.node_id, "implement")
        panel.connect_nodes(second.node_id, dod.node_id, "implement")

        panel._run_node(goal)

        assert first.status == "review"
        assert second.status == "review"
        assert panel._node_run_history[first.node_id]
        assert panel._node_run_history[second.node_id]
        assert panel._run_session is not None
    finally:
        panel.close()


def test_agent_canvas_auto_approve_coder_actions_but_not_dod(qapp, workspace):
    settings = SettingsStore()
    settings.save({"canvas_action_auto_approve": "coder"})
    panel = AgentCanvasPanel(
        str(workspace),
        settings=settings,
        run_agent_runner=lambda node, _prompt, kind: f"{kind} finished: {node.token.title}",
    )

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship flow", "make it usable"), QPointF(0, 0))
        work = panel.add_token_node(CanvasToken("operation", "Build flow", "make it real"), QPointF(280, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Flow accepted"), QPointF(560, 0))
        work.set_agent("coder", "Coder")
        panel.connect_nodes(goal.node_id, work.node_id, "work")
        panel.connect_nodes(work.node_id, dod.node_id, "implement")

        panel._run_node(goal)

        assert work.status == "done"
        assert dod.status == "review"
        assert goal.status == "running"
        assert panel._run_session is not None
    finally:
        panel.close()


def test_agent_canvas_run_chunks_render_on_timer(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    try:
        work = panel.add_token_node(CanvasToken("operation", "Stream output", "produce text"), QPointF(0, 0))
        work.set_status("running", "Coder working")
        panel._select_node(work)
        attempt = panel._new_run_attempt(work, "operation")

        panel._render_graph_chat()
        assert "chunk one" not in panel._graph_chat_transcript.toPlainText()

        panel._on_run_agent_chunk(work.node_id, attempt["id"], "chunk one")

        assert panel._run_chat_render_pending
        assert "chunk one" not in panel._graph_chat_transcript.toPlainText()

        panel._flush_run_chat_render()

        assert not panel._run_chat_render_pending
        assert "chunk one" in panel._graph_chat_transcript.toPlainText()
    finally:
        panel.close()


def test_agent_canvas_provider_failure_selects_retryable_step(qapp, workspace):
    def failing_runner(_node, _prompt, _kind):
        raise RuntimeError("Chat completion aborted. Please check the server console.")

    panel = AgentCanvasPanel(str(workspace), run_agent_runner=failing_runner)

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship flow", "make it usable"), QPointF(0, 0))
        work = panel.add_token_node(CanvasToken("operation", "Build flow", "make it real"), QPointF(280, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Flow accepted"), QPointF(560, 0))
        panel.connect_nodes(goal.node_id, work.node_id, "work")
        panel.connect_nodes(work.node_id, dod.node_id, "implement")

        panel._run_node(goal)

        assert goal.status == "blocked"
        assert work.status == "blocked"
        assert panel._selected_node() is work
        assert panel._latest_run_attempt_status(work.node_id) == "error"
        assert not panel._run_rerun_btn.isHidden()
        assert panel._run_rerun_btn.text() == "Retry step"
        assert "Attempt 1" in panel._run_rerun_btn.toolTip()
        assert "error" in panel._run_rerun_btn.toolTip()
        assert "needs retry or guidance" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_rerunning_goal_after_provider_failure_retries_failed_step(qapp, workspace):
    calls = []

    def runner(node, prompt, kind):
        calls.append((node.token.title, prompt, kind))
        if len(calls) == 1:
            raise RuntimeError("Chat completion aborted. Please check the server console.")
        return "retry finished"

    panel = AgentCanvasPanel(str(workspace), run_agent_runner=runner)

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship flow", "make it usable"), QPointF(0, 0))
        work = panel.add_token_node(CanvasToken("operation", "Build flow", "make it real"), QPointF(280, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Flow accepted"), QPointF(560, 0))
        panel.connect_nodes(goal.node_id, work.node_id, "work")
        panel.connect_nodes(work.node_id, dod.node_id, "implement")

        panel._run_node(goal)
        assert work.status == "blocked"

        panel._run_node(goal)

        assert len(calls) == 2
        assert calls[1][0] == "Build flow"
        assert work.status == "review"
        assert goal.status == "running"
        assert panel._run_session is not None
    finally:
        panel.close()


def test_agent_canvas_retry_after_provider_failure_uses_compact_prompt(qapp, workspace):
    calls = []

    def runner(node, prompt, kind):
        calls.append((node.token.title, prompt, kind))
        if len(calls) == 1:
            raise RuntimeError("Chat completion aborted. Please check the server console.")
        return "retry finished"

    panel = AgentCanvasPanel(str(workspace), run_agent_runner=runner)

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship flow", "make it usable"), QPointF(0, 0))
        work = panel.add_token_node(CanvasToken("operation", "Build flow", "make it real"), QPointF(280, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Flow accepted"), QPointF(560, 0))
        panel.connect_nodes(goal.node_id, work.node_id, "work")
        panel.connect_nodes(work.node_id, dod.node_id, "implement")

        panel._run_node(goal)
        panel._rerun_selected_run_node()

        assert len(calls) == 2
        assert "Retry this graph operation with minimal context" in calls[1][1]
        assert "ask the user one specific question" in calls[1][1]
        assert panel._node_run_history[work.node_id][-1]["compact_retry"] is True
    finally:
        panel.close()


def test_agent_canvas_guidance_after_failed_step_adds_context_and_resets_for_resume(qapp, workspace, monkeypatch):
    def failing_runner(_node, _prompt, _kind):
        raise RuntimeError("Chat completion aborted. Please check the server console.")

    panel = AgentCanvasPanel(str(workspace), run_agent_runner=failing_runner)
    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        lambda *args, **kwargs: ("Use the smaller local model context.", True),
    )

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship flow", "make it usable"), QPointF(0, 0))
        work = panel.add_token_node(CanvasToken("operation", "Build flow", "make it real"), QPointF(280, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Flow accepted"), QPointF(560, 0))
        panel.connect_nodes(goal.node_id, work.node_id, "work")
        panel.connect_nodes(work.node_id, dod.node_id, "implement")

        panel._run_node(goal)
        assert work.status == "blocked"
        assert goal.status == "blocked"
        assert not panel._run_guidance_btn.isHidden()

        panel._add_guidance_to_selected_run_node()

        guidance = next(node for node in panel._nodes.values() if node.token.title == "Guidance for Build flow")
        assert guidance.token.kind == "context"
        assert guidance.token.detail == "Use the smaller local model context."
        assert work.status == "idle"
        assert goal.status == "idle"
        assert any(
            edge.source_id == guidance.node_id
            and edge.target_id == work.node_id
            and edge.kind == "informs"
            for edge in panel._edges
        )
        assert "needs changes added for Build flow" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_guidance_after_dod_failure_adds_decision(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace), run_agent_runner=lambda *_args: None)
    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        lambda *args, **kwargs: ("Accept only after screenshot proof.", True),
    )

    try:
        work = panel.add_token_node(CanvasToken("operation", "Build flow", "make it real"), QPointF(0, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Flow accepted"), QPointF(280, 0))
        panel.connect_nodes(work.node_id, dod.node_id, "implement")
        panel._node_run_history[dod.node_id] = [{"id": "a", "status": "error", "content": "Need guidance"}]
        dod.set_status("blocked", "provider error; retry or add guidance")
        panel._select_node(dod)

        panel._add_guidance_to_selected_run_node()

        guidance = next(node for node in panel._nodes.values() if node.token.title == "Address: Accept only after screenshot proof.")
        assert guidance.token.kind == "operation"
        assert "Accept only after screenshot proof." in guidance.token.detail
        assert "Flow accepted" in guidance.token.detail
        assert any(
            edge.source_id == work.node_id
            and edge.target_id == guidance.node_id
            and edge.source_port == "implement"
            for edge in panel._edges
        )
        assert any(
            edge.source_id == guidance.node_id
            and edge.target_id == dod.node_id
            and edge.source_port == "implement"
            for edge in panel._edges
        )
    finally:
        panel.close()


def test_agent_canvas_run_history_round_trips_with_graph_state(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace), run_agent_runner=lambda node, _prompt, kind: f"{kind} finished: {node.token.title}")

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship flow", "make it usable"), QPointF(0, 0))
        work = panel.add_token_node(CanvasToken("operation", "Build flow", "make it real"), QPointF(280, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Flow accepted"), QPointF(560, 0))
        panel.connect_nodes(goal.node_id, work.node_id, "work")
        panel.connect_nodes(work.node_id, dod.node_id, "implement")

        panel._run_node(goal)
        panel._select_node(work)
        assert "operation finished: Build flow" in panel._graph_chat_transcript.toPlainText()

        state = panel.graph_state()
    finally:
        panel.close()

    restored = AgentCanvasPanel(str(workspace))
    try:
        warning = restored.restore_graph_state(state)
        assert warning == ""
        work_restored = next(node for node in restored._nodes.values() if node.token.title == "Build flow")
        restored._select_node(work_restored)
        assert "operation finished: Build flow" in restored._graph_chat_transcript.toPlainText()
    finally:
        restored.close()


def test_agent_canvas_run_history_renders_markdown(qapp, workspace):
    panel = AgentCanvasPanel(
        str(workspace),
        run_agent_runner=lambda _node, _prompt, _kind: "**Done**\n\n```python\nprint('ok')\n```",
    )

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship flow", "make it usable"), QPointF(0, 0))
        work = panel.add_token_node(CanvasToken("operation", "Build flow", "make it real"), QPointF(280, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Flow accepted"), QPointF(560, 0))
        panel.connect_nodes(goal.node_id, work.node_id, "work")
        panel.connect_nodes(work.node_id, dod.node_id, "implement")

        panel._run_node(goal)
        panel._select_node(work)

        html = panel._graph_chat_transcript.toHtml().lower()
        assert "<table" in html
        assert "print" in panel._graph_chat_transcript.toPlainText()
    finally:
        panel.close()


def test_agent_canvas_run_history_renders_structured_prompt_without_clipping(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        work = panel.add_token_node(
            CanvasToken(
                "operation",
                "Implement Calculator App",
                "Build the calculator with scientific functions, keyboard navigation, and responsive UI.",
            ),
            QPointF(0, 0),
        )
        prompt = (
            "Run this graph operation.\n\n"
            "Operation: Implement Calculator App\n"
            "Description:\nBuild the calculator with scientific functions, keyboard navigation, and responsive UI.\n\n"
            "Crew: Coder\n\n"
            "Inputs:\n"
            "- guides: decision 'Scientific Function Set' [idle] - Select which scientific functions to include and document precision requirements for trigonometry, logarithms, exponentials, history, keyboard behavior, and edge-case display.\n\n"
            "Expected downstream consumers:\n"
            "- implement: dod 'Acceptance' [idle] - Calculator is usable.\n\n"
            "Execute only this operation. Use tools as needed. Do not mark the graph done yourself."
        )
        panel._node_run_history[work.node_id] = [
            {
                "id": "attempt-1",
                "role": "Coder",
                "status": "running",
                "started_at": "2026-06-18 21:30",
                "prompt": prompt,
                "content": "",
                "tools": [],
            }
        ]
        work.set_status("running", "running")
        panel._select_node(work)

        text = panel._graph_chat_transcript.toPlainText()
        html = panel._graph_chat_transcript.toHtml().lower()
        assert "Run prompt" in text
        assert "Operation" in text
        assert "Implement Calculator App" in text
        assert "precision requirements for trigonometry" in text
        assert "Expected downstream consumers" in text
        assert "#9fd9ff" in html
    finally:
        panel.close()


def test_agent_canvas_run_history_styles_tool_activity_separately(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        work = panel.add_token_node(CanvasToken("operation", "Design UI", "create document"), QPointF(0, 0))
        panel._node_run_history[work.node_id] = [
            {
                "id": "attempt-1",
                "role": "Coder",
                "status": "review",
                "started_at": "2026-06-18 21:30",
                "prompt": "Create the UX/UI design document.",
                "content": "I'll create the UX/UI Design document for the scientific calculator.",
                "tools": [
                    {
                        "name": "read_file",
                        "status": "failed",
                        "summary": r"C:\Users\nadav\source\repos\aichs-example\UX_UI_DESIGN.md",
                    }
                ],
            }
        ]
        work.set_status("review", "awaiting acceptance")
        panel._select_node(work)

        text = panel._graph_chat_transcript.toPlainText()
        html = panel._graph_chat_transcript.toHtml().lower()
        assert "Tool activity" in text
        assert "read_file" in text
        assert "failed" in text
        assert "I'll create the UX/UI Design document" in text
        assert "#ff8a8a" in html
        assert "tool read_file: failed" not in text.lower()
    finally:
        panel.close()


def test_agent_canvas_run_history_expands_tool_execution_details(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        work = panel.add_token_node(CanvasToken("operation", "Run tests", "execute test command"), QPointF(0, 0))
        attempt = {
            "id": "attempt-1",
            "role": "Coder",
            "status": "running",
            "started_at": "2026-06-18 21:30",
            "prompt": "Run this graph operation.",
            "content": "",
            "tools": [],
        }
        panel._node_run_history[work.node_id] = [attempt]
        panel._on_run_agent_tool_called(work.node_id, "attempt-1", "execute", {"command": "npm test"})
        panel._on_run_agent_tool_result(work.node_id, "attempt-1", "execute", "first line\nsecond line")
        work.set_status("review", "awaiting acceptance")
        panel._select_node(work)

        collapsed = panel._graph_chat_transcript.toPlainText()
        collapsed_html = panel._graph_chat_transcript.toHtml()
        assert "Tool activity" in collapsed
        assert "execute" in collapsed
        assert "▸" in collapsed
        assert "run-tool:attempt-1:0" in collapsed_html
        assert "Output" not in collapsed

        panel._on_graph_chat_anchor_clicked(QUrl("run-tool:attempt-1:0"))

        expanded = panel._graph_chat_transcript.toPlainText()
        html = panel._graph_chat_transcript.toHtml()
        assert "▾" in expanded
        assert "Tool activity" in expanded
        assert "Inputs" in expanded
        assert "Output" in expanded
        assert "second line" in expanded
        assert "&quot;command&quot;" in html or '"command"' in html

        panel._on_graph_chat_anchor_clicked(QUrl("run-tool:attempt-1:0"))

        assert "Output" not in panel._graph_chat_transcript.toPlainText()
    finally:
        panel.close()


def test_agent_canvas_run_history_keeps_tool_error_reason_and_collapses_repeats(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        work = panel.add_token_node(CanvasToken("operation", "Design UI", "create document"), QPointF(0, 0))
        attempt = {
            "id": "attempt-1",
            "role": "Coder",
            "status": "running",
            "started_at": "2026-06-18 21:30",
            "prompt": "Create the UX/UI design document.",
            "content": "",
            "tools": [],
        }
        panel._node_run_history[work.node_id] = [attempt]

        for _ in range(3):
            panel._on_run_agent_tool_called(
                work.node_id,
                "attempt-1",
                "read_file",
                {"path": r"C:\Users\nadav\source\repos\aichs-example\UX_UI_DESIGN.md"},
            )
            panel._on_run_agent_tool_result(
                work.node_id,
                "attempt-1",
                "read_file",
                r"[tool error] File does not exist: C:\Users\nadav\source\repos\aichs-example\UX_UI_DESIGN.md",
            )
        work.set_status("review", "awaiting acceptance")
        panel._select_node(work)

        text = panel._graph_chat_transcript.toPlainText()
        assert text.count("read_file") == 1
        assert "x3" in text
        assert "File does not exist" in text
        assert "Tool read_file: failed" not in text
        assert all(tool["summary"].startswith("File does not exist") for tool in attempt["tools"])
    finally:
        panel.close()


def test_agent_canvas_failed_edit_file_does_not_record_file_activity(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        work = panel.add_token_node(CanvasToken("operation", "Create design doc", "write document"), QPointF(0, 0))
        attempt = {
            "id": "attempt-1",
            "role": "Coder",
            "status": "running",
            "started_at": "2026-06-18 21:30",
            "prompt": "Create the design document.",
            "content": "",
            "tools": [],
        }
        panel._node_run_history[work.node_id] = [attempt]

        panel._on_run_agent_tool_called(
            work.node_id,
            "attempt-1",
            "edit_file",
            {"path": "src/main.py", "content": {"text": "bad"}},
        )
        assert panel._run_last_edit_path == "src/main.py"
        start_nodes = panel.node_count()

        panel._on_run_agent_tool_result(
            work.node_id,
            "attempt-1",
            "edit_file",
            "[tool error] edit_file content must be a string, got dict. Retry with content set directly to the full file text.",
        )

        assert panel._run_last_edit_path == ""
        assert panel.node_count() == start_nodes
        assert attempt["tools"][-1]["status"] == "failed"
        assert "content must be a string" in attempt["tools"][-1]["summary"]
    finally:
        panel.close()


def test_agent_canvas_run_prompt_warns_not_to_retry_missing_files(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        prompt = panel._run_system_suffix("Base.", "operation")
        assert "do not retry that same path" in prompt
        assert "use edit_file with content" in prompt
        assert "content must be a plain string" in prompt
        assert "fix the tool arguments once" in prompt
    finally:
        panel.close()


def test_agent_canvas_run_prompt_includes_done_upstream_result(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        architect = panel.add_token_node(
            CanvasToken("operation", "Design Calculator UX/UI", "Define the interaction model."),
            QPointF(0, 0),
        )
        implement = panel.add_token_node(
            CanvasToken("operation", "Implement Calculation Engine", "Build from the accepted design."),
            QPointF(280, 0),
        )
        assert panel.connect_nodes(architect.node_id, implement.node_id, "implement")
        architect.set_status("done", "accepted")
        panel._node_run_history[architect.node_id] = [
            {
                "id": "architect-attempt",
                "status": "done",
                "content": "Use a visible expression buffer, degree/radian toggle, and recoverable error states.",
                "artifact_ref": ".aichs/canvas/default/artifacts/node_1_architect-attempt.md",
            }
        ]

        prompt = panel._run_agent_prompt(implement, "operation")

        assert "Design Calculator UX/UI" in prompt
        assert "Result: Use a visible expression buffer" in prompt
        assert "Artifact: .aichs/canvas/default/artifacts/node_1_architect-attempt.md" in prompt
    finally:
        panel.close()


def test_agent_canvas_run_completion_writes_markdown_artifact(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        architect = panel.add_token_node(
            CanvasToken("operation", "Design Calculator UX/UI", "Create the interaction plan."),
            QPointF(0, 0),
        )
        architect.set_agent("architect", "Architect")
        attempt = panel._new_run_attempt(architect, "operation")

        panel._finish_node_attempt(
            architect.node_id,
            attempt["id"],
            "# Calculator Plan\n\nUse a persistent expression buffer.",
        )

        artifact_ref = attempt["artifact_ref"]
        artifact_path = workspace / artifact_ref
        assert artifact_path.exists()
        text = artifact_path.read_text(encoding="utf-8")
        assert "# Design Calculator UX/UI" in text
        assert "Role: Architect" in text
        assert "# Calculator Plan" in text

        panel._select_node(architect)
        assert "Artifact:" in panel._graph_chat_transcript.toPlainText()
        assert artifact_ref in panel._graph_chat_transcript.toPlainText()
    finally:
        panel.close()


def test_agent_canvas_dod_acceptance_saves_evidence_artifact(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        dod = panel.add_token_node(CanvasToken("dod", "Feature completion", "Flow is acceptable"), QPointF(0, 0))
        attempt = panel._new_run_attempt(dod, "dod_review")
        attempt["status"] = "review"
        attempt["content"] = "Review passed: all acceptance checks succeeded."
        dod.set_status("review", "awaiting acceptance")
        panel._select_node(dod)

        panel._accept_selected_run_node()

        attempt = panel._node_run_history[dod.node_id][-1]
        artifact_ref = attempt.get("artifact_ref")
        assert artifact_ref
        artifact_path = workspace / artifact_ref
        assert artifact_path.exists()
        text = artifact_path.read_text(encoding="utf-8")
        assert "# Feature completion" in text
        assert "## DoD acceptance" in text
        assert "Decision: approved" in text
        assert "Review passed: all acceptance checks succeeded." in text
        assert attempt["artifact_title"] == "DoD acceptance evidence"
        assert dod.status == "done"
        assert "project evidence saved" in dod._status_note
        assert artifact_ref in panel._graph_chat_transcript.toPlainText()
    finally:
        panel.close()


def test_agent_canvas_run_artifact_ref_round_trips(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        work = panel.add_token_node(CanvasToken("operation", "Plan feature", "Create durable plan"), QPointF(0, 0))
        attempt = panel._new_run_attempt(work, "operation")
        panel._finish_node_attempt(work.node_id, attempt["id"], "Plan output.")
        state = panel.graph_state()
    finally:
        panel.close()

    save_agent_canvas(workspace, state)
    loaded, warning = load_agent_canvas(workspace)

    assert warning == ""
    loaded_work = next(node for node in loaded["nodes"] if node["title"] == "Plan feature")
    loaded_attempt = loaded_work["run_history"][0]
    assert loaded_attempt["artifact_ref"].startswith(".aichs/canvas/default/artifacts/")
    assert (workspace / loaded_attempt["artifact_ref"]).exists()


def test_agent_canvas_storage_splits_heavy_history(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace), run_agent_runner=lambda node, _prompt, kind: f"{kind} finished: {node.token.title}")

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship flow", "make it usable"), QPointF(0, 0))
        work = panel.add_token_node(CanvasToken("operation", "Build flow", "make it real"), QPointF(280, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Flow accepted"), QPointF(560, 0))
        panel.connect_nodes(goal.node_id, work.node_id, "work")
        panel.connect_nodes(work.node_id, dod.node_id, "implement")
        panel._append_graph_chat_message("Graph Agent", "Planner transcript")
        panel._run_node(goal)
        panel._node_run_history[work.node_id][0]["tools"] = [
            {
                "name": "execute",
                "status": "failed",
                "summary": "npm test",
                "inputs": '{\n  "command": "npm test"\n}',
                "output": "'charmap' codec can't decode byte 0x8f",
            }
        ]
        state = panel.graph_state()
    finally:
        panel.close()

    save_agent_canvas(workspace, state)

    manifest = json.loads(canvas_path(workspace).read_text(encoding="utf-8"))
    assert manifest["storage"]["mode"] == "split"
    assert "graph_chat" not in manifest
    work_manifest = next(node for node in manifest["nodes"] if node["title"] == "Build flow")
    assert "run_history" not in work_manifest
    assert work_manifest["run_history_ref"].endswith(f"node_{work.node_id}.jsonl")
    assert (canvas_storage_dir(workspace) / "graph_chat.jsonl").exists()
    assert (canvas_storage_dir(workspace) / "runs" / f"node_{work.node_id}.jsonl").exists()

    loaded, warning = load_agent_canvas(workspace)
    assert warning == ""
    assert loaded is not None
    assert loaded["graph_chat"][0]["text"] == "Planner transcript"


def test_agent_canvas_storage_uses_project_canvas_manifest(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        panel.add_token_node(CanvasToken("goal", "Project canvas", "persist in project .aichs/canvas"), QPointF(0, 0))
        state = panel.graph_state()
    finally:
        panel.close()

    save_agent_canvas(workspace, state)

    assert canvas_path(workspace) == workspace / ".aichs" / "canvas" / "agent_canvas.json"
    assert canvas_path(workspace).exists()
    assert canvas_storage_dir(workspace) == workspace / ".aichs" / "canvas" / "default"
    assert not (workspace / ".aichs" / "agent_canvas.json").exists()


def test_agent_canvas_storage_loads_project_canvas_manifest(qapp, workspace):
    manifest = workspace / ".aichs" / "canvas" / "agent_canvas.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "format": "aichs-agent-canvas/v1",
                "version": 1,
                "next_node_id": 2,
                "next_frame_id": 1,
                "active_node_id": None,
                "selected_node_id": 1,
                "selected_frame_id": None,
                "view": {"zoom": 1.0, "center": {"x": 0, "y": 0}},
                "nodes": [
                    {
                        "id": 1,
                        "kind": "goal",
                        "title": "Project canvas graph",
                        "detail": "from project .aichs/canvas",
                        "x": 0,
                        "y": 0,
                        "status": "idle",
                        "status_note": "",
                        "agent_id": "",
                        "agent_name": "",
                    }
                ],
                "edges": [],
                "frames": [],
                "graph_chat": [],
            }
        ),
        encoding="utf-8",
    )

    loaded, warning = load_agent_canvas(workspace)

    assert warning == ""
    assert loaded is not None
    assert loaded["nodes"][0]["title"] == "Project canvas graph"


def test_agent_canvas_storage_refuses_empty_overwrite(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        panel.add_token_node(CanvasToken("goal", "Do not erase", "non-empty"), QPointF(0, 0))
        state = panel.graph_state()
    finally:
        panel.close()

    save_agent_canvas(workspace, state)
    empty = dict(state)
    empty["nodes"] = []
    empty["edges"] = []
    empty["frames"] = []

    with pytest.raises(CanvasSaveRefused):
        save_agent_canvas(workspace, empty)

    loaded, warning = load_agent_canvas(workspace)
    assert warning == ""
    assert any(node["title"] == "Do not erase" for node in loaded["nodes"])


def test_agent_canvas_storage_recovers_graph_from_sidecar(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Recoverable graph", "sidecar"), QPointF(0, 0))
        work = panel.add_token_node(CanvasToken("operation", "Build flow", "make it real"), QPointF(280, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Flow accepted"), QPointF(560, 0))
        panel.connect_nodes(goal.node_id, work.node_id, "work")
        panel.connect_nodes(work.node_id, dod.node_id, "implement")
        panel._node_run_history[work.node_id] = [
            {
                "id": "attempt-1",
                "kind": "operation",
                "role": "Coder",
                "status": "review",
                "started_at": "2026-06-18 21:30",
                "prompt": "Run this graph operation.",
                "content": "operation finished: Build flow",
                "tools": [
                    {
                        "name": "execute",
                        "status": "failed",
                        "summary": "npm test",
                        "inputs": '{\n  "command": "npm test"\n}',
                        "output": "'charmap' codec can't decode byte 0x8f",
                    }
                ],
                "touched_files": [],
            }
        ]
        state = panel.graph_state()
    finally:
        panel.close()

    save_agent_canvas(workspace, state)
    manifest_path = canvas_path(workspace)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["nodes"] = []
    manifest["edges"] = []
    manifest["frames"] = []
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    loaded, warning = load_agent_canvas(workspace)
    assert warning == ""
    assert any(node["title"] == "Recoverable graph" for node in loaded["nodes"])
    loaded_work = next(node for node in loaded["nodes"] if node["title"] == "Build flow")
    assert loaded_work["run_history"][0]["content"] == "operation finished: Build flow"
    loaded_tool = loaded_work["run_history"][0]["tools"][0]
    assert loaded_tool["inputs"] == '{\n  "command": "npm test"\n}'
    assert "charmap" in loaded_tool["output"]


def test_agent_canvas_delete_confirmation_can_cancel(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))
    node = panel.add_token_node(CanvasToken("operation", "Temp", "keep me"), QPointF(0, 0))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)

    try:
        count = panel.node_count()
        node.setSelected(True)
        panel.delete_selected()

        assert panel.node_count() == count
        assert node.node_id in panel._nodes
        assert "delete cancelled" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_goal_waits_for_dod_before_done(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace), run_agent_runner=lambda node, _prompt, kind: f"{kind} finished: {node.token.title}")

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship flow", "make it usable"), QPointF(0, 0))
        work = panel.add_token_node(CanvasToken("operation", "Build flow", "make it real"), QPointF(280, 0))
        proof = panel.add_token_node(CanvasToken("evidence", "Proof", "test output"), QPointF(560, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Proof accepted"), QPointF(840, 0))
        panel.connect_nodes(goal.node_id, work.node_id, "work")
        panel.connect_nodes(work.node_id, proof.node_id, "evidence")
        panel.connect_nodes(work.node_id, dod.node_id, "implement")
        panel.connect_nodes(proof.node_id, dod.node_id, "supports")

        panel._run_node(goal)
        assert work.status == "review"

        panel._select_node(work)
        panel._accept_selected_run_node()
        assert panel._nodes[goal.node_id].status == "running"
        assert panel._run_session is not None
        assert panel._nodes[dod.node_id].status == "review"

        panel._select_node(dod)
        panel._accept_selected_run_node()
        assert panel._nodes[goal.node_id].status == "done"
        assert panel._run_session is None
    finally:
        panel.close()


def test_agent_canvas_context_menu_pauses_running_goal(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace), run_agent_runner=lambda *_args: None)
    goal = panel.add_token_node(CanvasToken("goal", "Review flow", "check state"), QPointF(-280, 0))
    node = panel.add_token_node(CanvasToken("operation", "Review", "check state"), QPointF(0, 0))
    dod = panel.add_token_node(CanvasToken("dod", "DoD", "Review accepted"), QPointF(280, 0))
    panel.connect_nodes(goal.node_id, node.node_id, "work")
    panel.connect_nodes(node.node_id, dod.node_id, "implement")

    def choose_pause(menu, _pos):
        action_texts = [action.text() for action in menu.actions() if not action.isSeparator()]
        assert "Mark Running" not in action_texts
        assert "Mark Done" not in action_texts
        assert "Mark Blocked" not in action_texts
        assert "Clear Status" not in action_texts
        assert "Stop" in action_texts
        return next(action for action in menu.actions() if action.text() == "Pause")

    monkeypatch.setattr(QMenu, "exec", choose_pause)

    try:
        panel._run_node(goal)
        panel._show_node_menu(goal, QPoint(0, 0))

        assert goal.status == "paused"
        assert node.status == "paused"
        assert panel._active_node_id is None
        assert "paused Review flow" in panel._inspector_lines[2].text()
    finally:
        panel.close()


def test_agent_canvas_context_menu_has_no_status_controls_when_idle(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))
    node = panel.add_token_node(CanvasToken("operation", "Idle", "check menu"), QPointF(0, 0))

    def inspect_menu(menu, _pos):
        action_texts = [action.text() for action in menu.actions() if not action.isSeparator()]
        assert "Pause" not in action_texts
        assert "Stop" not in action_texts
        assert "Mark Running" not in action_texts
        assert "Mark Done" not in action_texts
        assert "Mark Blocked" not in action_texts
        assert "Clear Status" not in action_texts
        return None

    monkeypatch.setattr(QMenu, "exec", inspect_menu)

    try:
        panel._show_node_menu(node, QPoint(0, 0))
    finally:
        panel.close()


def test_agent_canvas_context_menu_needs_changes_adds_guidance(qapp, workspace, monkeypatch):
    def failing_runner(_node, _prompt, _kind):
        raise RuntimeError("Chat completion aborted. Please check the server console.")

    panel = AgentCanvasPanel(str(workspace), run_agent_runner=failing_runner)
    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        lambda *args, **kwargs: ("Retry with smaller context and clearer API contract.", True),
    )

    def choose_needs_changes(menu, _pos):
        return next(action for action in menu.actions() if action.text() == "Needs changes")

    monkeypatch.setattr(QMenu, "exec", choose_needs_changes)

    try:
        goal = panel.add_token_node(CanvasToken("goal", "Ship flow", "make it usable"), QPointF(0, 0))
        work = panel.add_token_node(CanvasToken("operation", "Build flow", "make it real"), QPointF(280, 0))
        dod = panel.add_token_node(CanvasToken("dod", "DoD", "Flow accepted"), QPointF(560, 0))
        panel.connect_nodes(goal.node_id, work.node_id, "work")
        panel.connect_nodes(work.node_id, dod.node_id, "implement")

        panel._run_node(goal)

        panel._select_node(work)
        assert work.status == "blocked"
        panel._show_node_menu(work, QPoint(0, 0))

        guidance = next(node for node in panel._nodes.values() if node.token.title == "Guidance for Build flow")
        assert guidance.token.kind == "context"
        assert guidance.token.detail == "Retry with smaller context and clearer API contract."
        assert work.status == "idle"
        assert any(
            edge.source_id == guidance.node_id
            and edge.target_id == work.node_id
            and edge.kind == "informs"
            for edge in panel._edges
        )
    finally:
        panel.close()


def test_agent_canvas_record_file_activity_reuses_existing_scope(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        panel.record_file_activity(str(workspace / "src" / "main.py"))
        start_nodes = panel.node_count()
        start_edges = panel.edge_count()

        panel.record_file_activity(str(workspace / "src" / "main.py"))

        assert panel.node_count() == start_nodes
        assert panel.edge_count() == start_edges
    finally:
        panel.close()


def test_agent_canvas_repo_path_candidates_include_files_and_folders(workspace):
    candidates = repo_path_candidates(str(workspace))

    assert "src/" in candidates
    assert "src/main.py" in candidates


def test_agent_canvas_record_file_activity_reuses_scope_with_multiple_paths(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        operation = panel.add_token_node(CanvasToken("operation", "Edit", "active work"), QPointF(-260, 0))
        operation.setSelected(True)
        panel.add_token_node(CanvasToken("scope", "2 paths", "README.md\nsrc/main.py"), QPointF(0, 0))
        start_nodes = panel.node_count()

        scope = panel.record_file_activity(str(workspace / "src" / "main.py"))

        assert panel.node_count() == start_nodes
        assert scope.token.title == "2 paths"
    finally:
        panel.close()


def test_agent_canvas_graph_delete_key_removes_selected_after_move(qapp, workspace, monkeypatch):
    panel = AgentCanvasPanel(str(workspace))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    try:
        node = panel.add_token_node(CanvasToken("operation", "Moved", "delete after move"), QPointF(0, 0))
        start_nodes = panel.node_count()
        node.setSelected(True)
        node.setPos(QPointF(80, 40))
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)

        panel._graph.keyPressEvent(event)

        assert event.isAccepted()
        assert panel.node_count() == start_nodes - 1
        assert node.node_id not in panel._nodes
    finally:
        panel.close()


def test_agent_canvas_graph_items_cover_ports_and_drag_repaints(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        operation = panel.add_token_node(CanvasToken("operation", "Move", "source"), QPointF(0, 0))
        scope = panel.add_token_node(CanvasToken("scope", "main.py", "src/main.py"), QPointF(320, 30))

        assert operation.boundingRect().left() < 0
        assert operation.boundingRect().right() > operation.WIDTH
        assert panel._graph.viewportUpdateMode() == panel._graph.ViewportUpdateMode.FullViewportUpdate

        assert panel.connect_nodes(scope.node_id, operation.node_id, "read")
        edge = panel._edges[-1].item
        assert edge.boundingRect().contains(edge.path().boundingRect())
    finally:
        panel.close()


def test_agent_canvas_graph_background_paints_with_float_rect(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    image = QImage(320, 220, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)

    try:
        panel._graph.drawBackground(painter, QRectF(-160.5, -100.25, 320.75, 220.5))
    finally:
        painter.end()
        panel.close()

    assert not image.isNull()


def test_agent_canvas_scene_expands_for_far_nodes(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        node = panel.add_token_node(CanvasToken("goal", "Far Goal", "large design"), QPointF(7600, -5200))
        rect = panel._graph.sceneRect()

        assert rect.contains(node.sceneBoundingRect())
        assert rect.right() > 7600
        assert rect.top() < -5200
    finally:
        panel.close()


def test_agent_canvas_wheel_zooms_without_modifier(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    event = QWheelEvent(
        QPointF(120, 90),
        QPointF(120, 90),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )

    try:
        before = panel._graph._zoom
        panel._graph.wheelEvent(event)

        assert event.isAccepted()
        assert panel._graph._zoom > before
    finally:
        panel.close()


def test_agent_canvas_zoom_out_supports_large_overview(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        for _ in range(40):
            panel._graph.zoom_out()

        assert panel._graph._zoom == panel._graph.MIN_ZOOM
        assert panel._graph._zoom < 0.2
    finally:
        panel.close()


def test_agent_canvas_restore_view_keeps_deep_zoom_out(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))

    try:
        panel._restore_view({"zoom": 0.14, "center_x": 0, "center_y": 0})

        assert panel._graph._zoom == 0.14
    finally:
        panel.close()


def test_agent_canvas_middle_drag_expands_near_scene_edge(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    graph = panel._graph

    try:
        graph.resize(500, 360)
        graph.centerOn(QPointF(graph.sceneRect().right(), 0))
        before = graph.sceneRect().right()
        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(250, 180),
            QPointF(250, 180),
            Qt.MouseButton.MiddleButton,
            Qt.MouseButton.MiddleButton,
            Qt.KeyboardModifier.NoModifier,
        )
        move = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(210, 180),
            QPointF(210, 180),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.MiddleButton,
            Qt.KeyboardModifier.NoModifier,
        )

        graph.mousePressEvent(press)
        graph.mouseMoveEvent(move)

        assert graph.sceneRect().right() > before
    finally:
        panel.close()


def test_agent_canvas_middle_drag_pans_graph(qapp, workspace):
    panel = AgentCanvasPanel(str(workspace))
    graph = panel._graph
    graph.horizontalScrollBar().setRange(0, 1000)
    graph.verticalScrollBar().setRange(0, 1000)
    graph.horizontalScrollBar().setValue(500)
    graph.verticalScrollBar().setValue(500)
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(50, 50),
        QPointF(50, 50),
        Qt.MouseButton.MiddleButton,
        Qt.MouseButton.MiddleButton,
        Qt.KeyboardModifier.NoModifier,
    )
    move = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(70, 85),
        QPointF(70, 85),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.MiddleButton,
        Qt.KeyboardModifier.NoModifier,
    )
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(70, 85),
        QPointF(70, 85),
        Qt.MouseButton.MiddleButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )

    try:
        graph.mousePressEvent(press)
        graph.mouseMoveEvent(move)
        graph.mouseReleaseEvent(release)

        assert press.isAccepted()
        assert move.isAccepted()
        assert release.isAccepted()
        assert graph.horizontalScrollBar().value() == 480
        assert graph.verticalScrollBar().value() == 465
        assert graph._middle_pan_active is False
    finally:
        panel.close()


def test_main_window_canvas_rail_keeps_file_editor_on_right(
    qapp,
    workspace,
    quiet_file_language,
):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    window = MainWindow(startup_workspace=str(workspace))
    opened = workspace / "src" / "main.py"
    try:
        window._left._activity_buttons["canvas"].click()

        assert window._left.active_activity() == "canvas"
        assert window._workbench_left.currentWidget() is window._agent_canvas
        assert window._center_stack.currentWidget() is window._workbench
        assert window._context_shell.isHidden()

        window._agent_canvas.open_file_requested.emit(str(opened))
        _settle_file_viewer_workers(qapp)

        assert not window._viewer.isHidden()
        assert window._workbench_left.currentWidget() is window._agent_canvas
        assert window._left.active_activity() == "canvas"

        start_nodes = window._agent_canvas.node_count()
        window._chat.file_write_completed.emit(str(opened))
        assert window._agent_canvas.node_count() == start_nodes
        assert all(token.title != "Current Work" for token in window._agent_canvas.graph_items())
    finally:
        _settle_file_viewer_workers(qapp)
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_main_window_restore_canvas_activity_shows_canvas(
    qapp,
    workspace,
    quiet_file_language,
):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    window = MainWindow(startup_workspace=str(workspace))
    try:
        window._restore_layout({"active_activity": "canvas", "activity_collapsed": True})

        assert window._left.active_activity() == "canvas"
        assert window._center_stack.currentWidget() is window._workbench
        assert window._workbench_left.currentWidget() is window._agent_canvas
    finally:
        _settle_file_viewer_workers(qapp)
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)
