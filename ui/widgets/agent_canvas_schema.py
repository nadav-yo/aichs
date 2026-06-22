import json
from dataclasses import dataclass


AICHS_CANVAS_TOKEN_MIME = "application/x-aichs-canvas-token"

NODE_STATUSES = ("idle", "queued", "thinking", "planned", "running", "paused", "changed", "review", "done", "blocked")


@dataclass(frozen=True)
class CanvasToken:
    kind: str
    title: str
    detail: str = ""


@dataclass(frozen=True)
class ComponentSpec:
    kind: str
    title: str
    detail: str
    role: str
    inputs: str
    outputs: str


@dataclass(frozen=True)
class ConnectionRule:
    source_kind: str
    target_kind: str
    kind: str
    label: str
    source_port: str = "out"
    target_port: str = "in"


@dataclass
class CanvasEdge:
    source_id: int
    target_id: int
    kind: str
    source_port: str
    target_port: str
    item: "_GraphEdge"


@dataclass(frozen=True)
class PortSpec:
    key: str
    label: str


@dataclass(frozen=True)
class CreationAction:
    source_kind: str
    source_port: str
    target_kind: str
    title: str
    detail: str
    token_title: str
    token_detail: str = ""


_CREATION_ACTIONS = (
    CreationAction("goal", "split", "goal", "Break into smaller goal", "Creates a child goal", "New Goal", "Describe the outcome"),
    CreationAction("goal", "work", "operation", "Add work action", "Creates a runnable action for this goal", "Implement", "Describe what the selected crew member should implement, decide, or prove."),
    CreationAction("goal", "context", "context", "Add goal context", "Adds constraints or background that apply to this goal", "Context", "Durable knowledge that shapes this goal"),
    CreationAction("operation", "evidence", "evidence", "Produces proof", "Adds proof output", "Proof", "Test, diff, screenshot, error"),
    CreationAction("operation", "decision", "decision", "Produces decision", "Adds a decision output contract", "Decision", "Decision to produce, criteria, and downstream guidance needed"),
    CreationAction("operation", "implement", "operation", "Then implement", "Chains another implementation action", "Next Implement", "Follow-up implementation action"),
    CreationAction("operation", "implement", "dod", "Check acceptance", "Connects action output to acceptance criteria", "DoD", "Acceptance criteria this action should satisfy"),
    CreationAction("scope", "read", "operation", "Read this scope", "Creates an action that reads this scope", "Inspect", "Read this code area"),
    CreationAction("scope", "proof", "evidence", "Produce proof", "Adds evidence from this scope", "Proof", "Result from scope"),
    CreationAction("evidence", "supports", "decision", "Supports decision", "Creates decision from proof", "Decision", "Evidence-backed conclusion"),
    CreationAction("evidence", "supports", "dod", "Satisfies DoD", "Links proof into acceptance criteria", "DoD", "Acceptance criteria that this proof satisfies"),
    CreationAction("evidence", "feedback", "operation", "Feeds back to action", "Creates follow-up action", "Fix / Follow-up", "Driven by proof"),
    CreationAction("decision", "guide", "operation", "Guides action", "Creates guided action", "Guided Action", "Driven by decision"),
    CreationAction("decision", "resolve", "goal", "Resolves goal", "Creates resolved goal marker", "Resolved Goal", "Decision closes this"),
    CreationAction("decision", "resolve", "dod", "Accepts done", "Links a decision into acceptance criteria", "DoD", "Acceptance criteria accepted by decision"),
    CreationAction("context", "context", "operation", "Informs action", "Adds a work action from context", "Context Action", ""),
    CreationAction("context", "context", "decision", "Informs decision", "Adds a decision contract shaped by this context", "Decision", "Decision contract shaped by this context"),
)


_COMPONENT_SPECS: dict[str, ComponentSpec] = {
    "goal": ComponentSpec(
        "goal",
        "Goal",
        "Intent, constraint, or branch",
        "Defines what good looks like. Goal -> Goal is a split, not a different node type.",
        "SPLIT, CTX, DECIDE",
        "SPLIT, WORK, CTX",
    ),
    "operation": ComponentSpec(
        "operation",
        "Action",
        "Implement, decide, or prove",
        "A runnable work action. Choose crew in the inspector; use the description to say what to implement, decide, or prove.",
        "GOAL, READ, CTX, GUIDE, FEED",
        "IMPLEMENT, DECIDE, PROOF",
    ),
    "context": ComponentSpec(
        "context",
        "Context",
        "Project knowledge",
        "Groups broad context such as docs, architecture, skills, and conventions.",
        "GOAL, PROOF, DECIDE",
        "CTX",
    ),
    "scope": ComponentSpec(
        "scope",
        "Files",
        "Type a repo path",
        "A file, folder, or symbol area that grounds work.",
        "",
        "CONTEXT, PROOF",
    ),
    "evidence": ComponentSpec(
        "evidence",
        "Proof",
        "Diff, test, error, screenshot",
        "Proof produced by a work action. Evidence can support, block, or redirect a goal.",
        "PROOF",
        "SUPPORT, FEED",
    ),
    "dod": ComponentSpec(
        "dod",
        "DoD",
        "Acceptance criteria",
        "The terminal definition of done for a goal. Evidence and decisions feed DoD; DoD is the graph sink.",
        "IMPLEMENT, PROOF, DECIDE",
        "",
    ),
    "decision": ComponentSpec(
        "decision",
        "Decision",
        "Durable reasoning",
        "A chosen result the graph should preserve and apply later, or an output contract waiting for its producer operation.",
        "WHY, PROOF",
        "GUIDE, RESOLVE",
    ),
}


_CONNECTION_RULES = (
    ConnectionRule("goal", "goal", "split", "Split into child goal", "split", "split_in"),
    ConnectionRule("goal", "operation", "requires", "Requires work action", "work", "goal"),
    ConnectionRule("goal", "context", "context", "Goal context", "context", "goal"),
    ConnectionRule("context", "operation", "informs", "Context informs action", "context", "context"),
    ConnectionRule("context", "decision", "informs", "Context informs decision", "context", "reason"),
    ConnectionRule("scope", "goal", "context", "Ground goal in scope", "read", "context"),
    ConnectionRule("scope", "operation", "reads", "Action reads scope", "read", "scope"),
    ConnectionRule("scope", "context", "context", "Scope informs context", "proof", "proof"),
    ConnectionRule("scope", "evidence", "source", "Evidence from scope", "proof", "proof"),
    ConnectionRule("operation", "goal", "decides", "Decides goal direction", "decision", "decision"),
    ConnectionRule("operation", "evidence", "produces", "Produces evidence", "evidence", "proof"),
    ConnectionRule("operation", "decision", "decides", "Produces decision", "decision", "reason"),
    ConnectionRule("operation", "context", "context", "Action decision becomes context", "decision", "decision"),
    ConnectionRule("operation", "operation", "then", "Then implement", "implement", "goal"),
    ConnectionRule("operation", "dod", "then", "Check DoD", "implement", "work"),
    ConnectionRule("evidence", "dod", "satisfies", "Satisfies DoD", "supports", "proof"),
    ConnectionRule("evidence", "operation", "feedback", "Feedback to action", "feedback", "feedback"),
    ConnectionRule("evidence", "decision", "supports", "Supports decision", "supports", "evidence"),
    ConnectionRule("decision", "goal", "resolves", "Resolves or redirects goal", "resolve", "decision"),
    ConnectionRule("decision", "dod", "accepts", "Accepts DoD", "resolve", "decision"),
    ConnectionRule("decision", "operation", "guides", "Guides action", "guide", "decision"),
    ConnectionRule("decision", "context", "context", "Promote to context", "guide", "decision"),
)


def component_spec(kind: str) -> ComponentSpec:
    return _COMPONENT_SPECS.get(kind, ComponentSpec(kind, kind.title(), "", "", "in", "out"))


def connection_rule(source: CanvasToken, target: CanvasToken, source_port: str | None = None) -> ConnectionRule | None:
    for rule in _CONNECTION_RULES:
        if rule.source_kind == source.kind and rule.target_kind == target.kind:
            if source_port is not None and rule.source_port != source_port:
                continue
            return rule
    return None


def connection_rules_for_target(target: CanvasToken, target_port: str | None = None) -> tuple[ConnectionRule, ...]:
    matches = []
    for rule in _CONNECTION_RULES:
        if rule.target_kind != target.kind:
            continue
        if target_port is not None and rule.target_port != target_port:
            continue
        matches.append(rule)
    return tuple(matches)


def default_token_for_kind(kind: str) -> CanvasToken:
    spec = component_spec(kind)
    return CanvasToken(kind, spec.title, spec.detail)


def input_ports(kind: str) -> tuple[PortSpec, ...]:
    ports = {
        "goal": (
            PortSpec("split_in", "SPLIT"),
            PortSpec("context", "CTX"),
            PortSpec("decision", "DECIDE"),
        ),
        "dod": (
            PortSpec("work", "ACTION"),
            PortSpec("proof", "PROOF"),
            PortSpec("decision", "DECIDE"),
        ),
        "scope": (),
        "operation": (
            PortSpec("goal", "GOAL"),
            PortSpec("scope", "READ"),
            PortSpec("context", "CTX"),
            PortSpec("decision", "GUIDE"),
            PortSpec("feedback", "FEED"),
        ),
    "evidence": (
            PortSpec("proof", "PROOF"),
        ),
        "decision": (
            PortSpec("reason", "WHY"),
            PortSpec("evidence", "PROOF"),
        ),
        "context": (
            PortSpec("goal", "GOAL"),
            PortSpec("proof", "PROOF"),
            PortSpec("decision", "DECIDE"),
        ),
    }
    return ports.get(kind, (PortSpec("in", "IN"),))


def output_ports(kind: str) -> tuple[PortSpec, ...]:
    ports = {
        "goal": (
            PortSpec("split", "SPLIT"),
            PortSpec("work", "WORK"),
            PortSpec("context", "CTX"),
        ),
    "scope": (
            PortSpec("read", "CONTEXT"),
            PortSpec("proof", "PROOF"),
        ),
        "operation": (
            PortSpec("implement", "NEXT"),
            PortSpec("decision", "DECIDE"),
            PortSpec("evidence", "PROOF"),
        ),
        "evidence": (
            PortSpec("supports", "SUPPORT"),
            PortSpec("feedback", "FEED"),
        ),
        "decision": (
            PortSpec("guide", "GUIDE"),
            PortSpec("resolve", "RESOLVE"),
        ),
        "context": (
            PortSpec("context", "CTX"),
        ),
        "dod": (),
    }
    return ports.get(kind, (PortSpec("out", "OUT"),))


def edge_color(kind: str, p: dict) -> str:
    colors = {
        "split": "#64d6a2",
        "requires": p["LINK"],
        "assigns": "#67e8f9",
        "owns": "#67e8f9",
        "reviews": "#34d399",
        "needs_review": "#fbbf24",
        "reads": "#fbbf24",
        "produces": "#c4b5fd",
        "supports": "#34d399",
        "decides": "#f472b6",
        "resolves": "#64d6a2",
        "guides": "#67e8f9",
        "then": p["TEXT_DIM"],
        "context": "#fbbf24",
        "scopes": "#d6a84f",
        "informs": "#d6a84f",
        "source": "#c4b5fd",
        "feedback": "#67e8f9",
        "result": "#8ab4ff",
        "defines_done": "#34d399",
        "satisfies": "#34d399",
        "accepts": "#64d6a2",
    }
    return colors.get(kind, p["LINK"])


def canvas_token_payload(token: CanvasToken) -> bytes:
    return json.dumps(
        {
            "kind": token.kind.strip(),
            "title": token.title.strip(),
            "detail": token.detail.strip(),
        },
        separators=(",", ":"),
    ).encode("utf-8")


def parse_canvas_token(raw: bytes | bytearray | memoryview) -> CanvasToken | None:
    try:
        data = json.loads(bytes(raw).decode("utf-8"))
    except (TypeError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    kind = str(data.get("kind") or "").strip()
    title = str(data.get("title") or "").strip()
    detail = str(data.get("detail") or "").strip()
    if not kind or not title:
        return None
    return CanvasToken(kind=kind, title=title, detail=detail)
