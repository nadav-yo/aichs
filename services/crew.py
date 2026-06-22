from __future__ import annotations

import re
from dataclasses import dataclass

ASK_CREW_TOOL_NAME = "ask_crew"


@dataclass(frozen=True)
class CrewMember:
    id: str
    name: str
    title: str
    description: str
    tools: tuple[str, ...]
    preferred_model: str | None = None
    write_roots: tuple[str, ...] = ()
    called_when: tuple[str, ...] = ()
    prompt: str = ""

    def metadata(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "title": self.title,
            "preferred_model": self.preferred_model or "",
        }


CREW: tuple[CrewMember, ...] = (
    CrewMember(
        id="scout",
        name="Scout",
        title="Research",
        description="Finds repo evidence and reads relevant files before the lead decides.",
        tools=("list_files", "read_file", "search_files"),
        called_when=(
            "The user explicitly mentions @Scout.",
            "The task needs repo facts, APIs, docs, or current project evidence.",
        ),
        prompt=(
            "You are Scout, the crew researcher. Stay read-only. Find evidence in the "
            "workspace, cite files or search results you checked, and return a compact "
            "answer with confidence and any open questions. Do not edit files."
        ),
    ),
    CrewMember(
        id="archivist",
        name="Archivist",
        title="Memory",
        description="Distills decisions and keeps context tidy.",
        tools=("search_project_chats", "read_project_chat", "list_files", "read_file", "search_files"),
        called_when=(
            "The user explicitly mentions @Archivist.",
            "A long thread needs decision notes, summaries, or context cleanup.",
        ),
        prompt=(
            "You are Archivist, the crew memory keeper. Summarize durable decisions, "
            "open threads, and context worth carrying forward. Use read_project_chat "
            "for exact dropped chat references and search_project_chats when the user "
            "asks whether something was discussed before. Do not edit files."
        ),
    ),
    CrewMember(
        id="architect",
        name="Architect",
        title="System Design",
        description="Turns goals, constraints, and repo shape into a coherent implementation plan.",
        tools=("list_files", "read_file", "search_files"),
        called_when=(
            "The user explicitly mentions @Architect.",
            "A feature needs decomposition, ownership, architecture, or design tradeoffs.",
        ),
        prompt=(
            "You are Architect, the crew system designer. Stay concrete and repo-aware. "
            "Map goals into components, constraints, sequencing, and risks. Prefer small "
            "testable steps, call out weak assumptions, and do not edit files."
        ),
    ),
)

_BY_ID = {member.id: member for member in CREW}
_BY_NAME = {member.name.casefold(): member for member in CREW}
_MENTION_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_-]*)")


def all_crew() -> tuple[CrewMember, ...]:
    return CREW


def get_crew_member(member_id: str) -> CrewMember | None:
    return _BY_ID.get(str(member_id or "").casefold())


def crew_name_from_metadata(meta: dict | None) -> str:
    if not isinstance(meta, dict):
        return ""
    name = str(meta.get("name") or "").strip()
    if name:
        return name
    member = get_crew_member(str(meta.get("id") or ""))
    return member.name if member else ""


def crew_settings(settings: dict | None, member: CrewMember) -> dict:
    settings = settings if isinstance(settings, dict) else {}
    crew_data = settings.get("crew", {})
    raw = crew_data.get(member.id, {}) if isinstance(crew_data, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    legacy_models = settings.get("crew_models", {})
    legacy_model = legacy_models.get(member.id, "") if isinstance(legacy_models, dict) else ""
    return {
        "enabled": bool(raw.get("enabled", True)),
        "prompt": str(raw.get("prompt") or "").strip(),
        "model": str(raw.get("model") or legacy_model or member.preferred_model or "").strip(),
        "color": _clean_hex_color(raw.get("color", "")),
        "avatar": str(raw.get("avatar") or f"crew_{member.id}").strip(),
    }


def crew_enabled(settings: dict | None, member: CrewMember) -> bool:
    return bool(crew_settings(settings, member)["enabled"])


def crew_prompt(member: CrewMember, settings: dict | None = None) -> str:
    override = crew_settings(settings, member)["prompt"]
    return override or member.prompt


def crew_metadata(member: CrewMember, settings: dict | None = None) -> dict:
    cfg = crew_settings(settings, member)
    meta = member.metadata()
    meta.update({
        "enabled": cfg["enabled"],
        "model": cfg["model"],
        "color": cfg["color"],
        "avatar": cfg["avatar"],
    })
    return meta


def summoned_members(text: str) -> list[CrewMember]:
    found: list[CrewMember] = []
    seen: set[str] = set()
    for match in _MENTION_RE.finditer(text or ""):
        member = _BY_NAME.get(match.group(1).casefold())
        if member and member.id not in seen:
            found.append(member)
            seen.add(member.id)
    return found


def crew_roster_prompt() -> str:
    names = " and ".join(f"@{member.name}" for member in CREW)
    lines = [
        f"Optional Crew: {names}.",
        "Use ask_crew only for a focused second opinion; the lead owns the final answer.",
        "Crew members do not talk to each other. Usually call 0-2 members.",
        "",
        "Crew roster:",
    ]
    for member in CREW:
        tools = ", ".join(member.tools) if member.tools else "none"
        write_scope = (
            f"; writes limited to {', '.join(member.write_roots)}"
            if member.write_roots
            else "; read-only"
        )
        lines.append(
            f"- @{member.name} ({member.title}): {member.description} "
            f"Tools: {tools}{write_scope}."
        )
    return "\n".join(lines)


def ask_crew_tool_anthropic() -> dict:
    return {
        "name": ASK_CREW_TOOL_NAME,
        "description": (
            "Invite a specialized crew member to answer a focused question in its own "
            "context, then return the crew member's answer for synthesis."
        ),
        "input_schema": _ask_crew_schema(),
    }


def ask_crew_tool_openai() -> dict:
    tool = ask_crew_tool_anthropic()
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }


def _ask_crew_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "member": {
                "type": "string",
                "enum": [member.id for member in CREW],
                "description": "Crew member to invite.",
            },
            "task": {
                "type": "string",
                "description": "Focused request for that crew member.",
            },
            "reason": {
                "type": "string",
                "description": "Why this crew member is useful now.",
            },
        },
        "required": ["member", "task"],
    }


def crew_system_prompt(
    member: CrewMember,
    base_system: str,
    prompt_override: str | None = None,
) -> str:
    return "\n\n".join(
        [
            base_system,
            "## Crew Role",
            prompt_override or member.prompt,
            "Return one chat-bubble-sized response. Include evidence when you used tools. "
            "Do not pretend to be the lead assistant.",
        ]
    )


def crew_model_choice(
    crew: CrewMember,
    fallback: str,
    saved_models: dict | None,
    configured_providers: set[str] | None = None,
) -> str:
    from services.model_registry import MODEL_PROVIDER

    saved_models = saved_models if isinstance(saved_models, dict) else {}
    configured_providers = configured_providers or set()
    candidates = [
        str(saved_models.get(crew.id) or "").strip(),
        str(crew.preferred_model or "").strip(),
    ]
    for model in candidates:
        provider = MODEL_PROVIDER.get(model)
        if provider and (not configured_providers or provider in configured_providers):
            return model
    return fallback


def _clean_hex_color(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not text.startswith("#"):
        text = f"#{text}"
    if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        return text.lower()
    return ""
