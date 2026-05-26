import json
from dataclasses import dataclass

from services.compaction import compaction_threshold, reserve_tokens
from services.content import content_preview
from services.model_registry import context_window_tokens, get_model_config
from services.skills import Skill
from services.tools import tools_anthropic, tools_openai
from services.workspace import system_parts


@dataclass
class ContextSegment:
    label: str
    text: str
    detail: str = ""

    @property
    def byte_count(self) -> int:
        return len(self.text.encode("utf-8"))

    @property
    def token_count(self) -> int:
        return max(1, self.byte_count // 4) if self.text else 0


@dataclass
class ContextBudget:
    segments: list[ContextSegment]
    window_tokens: int
    reserve_tokens: int = 0

    @property
    def used_tokens(self) -> int:
        return sum(s.token_count for s in self.segments)

    @property
    def used_bytes(self) -> int:
        return sum(s.byte_count for s in self.segments)

    @property
    def pct(self) -> float:
        if self.window_tokens <= 0:
            return 0.0
        return min(100.0, self.used_tokens / self.window_tokens * 100)

    @property
    def compaction_limit_tokens(self) -> int:
        return compaction_threshold(self.window_tokens)


def format_bytes(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def analyze_context(
    model: str,
    cwd: str,
    history: list[dict],
    custom_system: str = "",
    active_skill: Skill | None = None,
) -> ContextBudget:
    api = get_model_config(model).api
    window = context_window_tokens(model)

    base, agents, workspace, extensions = system_parts(cwd, custom_system or None)
    tools_json = json.dumps(
        tools_anthropic(cwd) if api == "anthropic" else tools_openai(cwd),
        ensure_ascii=False,
    )

    segments = [
        ContextSegment("System prompt", base),
    ]
    if agents:
        segments.append(ContextSegment("Rules", agents, "AGENTS.md"))
    segments.append(ContextSegment("Workspace", workspace, "File tree & git status"))
    if extensions:
        segments.append(ContextSegment("Extensions", extensions, "Context snippets"))
    segments.append(ContextSegment("Tool definitions", tools_json))

    if active_skill:
        segments.append(ContextSegment(
            "Skills",
            active_skill.prompt,
            f"/{active_skill.name}",
        ))

    msg_text = _history_text(history)
    segments.append(ContextSegment(
        "Messages",
        msg_text,
        f"{len(history)} message{'s' if len(history) != 1 else ''}",
    ))

    return ContextBudget(
        segments=segments,
        window_tokens=window,
        reserve_tokens=reserve_tokens(window),
    )


def _history_text(history: list[dict]) -> str:
    return "\n\n".join(content_preview(m.get("content", "")) for m in history)
