from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import config
from services.performance import time_operation

_USER_DIR = config.AICHS_HOME / "skills"

_FRONT_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)", re.DOTALL)


@dataclass
class Skill:
    name:        str
    description: str
    prompt:      str
    tools:       list[str] | None = field(default=None)  # None = all tools allowed


def load_all(cwd: str | None = None) -> list[Skill]:
    """Return skills from user-global and project-local .aichs/skills/.

    Load order: user-global (AICHS_HOME/skills/) then project-local
    (.aichs/skills/ in cwd). Later entries override earlier ones with the same
    name.
    """
    with time_operation("skills.load", detail=f"cwd={cwd or ''}"):
        skills: dict[str, Skill] = {}
        dirs = [_USER_DIR]
        if cwd:
            dirs.append(Path(cwd) / ".aichs" / "skills")
        for directory in dirs:
            for path in _skill_paths(directory):
                skill = _parse(path)
                if skill:
                    skills[skill.name] = skill
        return sorted(skills.values(), key=lambda s: s.name)


def _skill_paths(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    try:
        children = sorted(directory.iterdir(), key=lambda path: path.name.casefold())
    except OSError:
        return []
    return [
        child
        for child in children
        if child.suffix == ".md"
        and not child.name.startswith(".")
        and child.is_file()
    ]


def _parse(path: Path) -> Skill | None:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None

    m = _FRONT_RE.match(text)
    if not m:
        return None

    meta = _parse_front(m.group(1))
    prompt = m.group(2).strip()
    if not prompt:
        return None

    name = meta.get("name") or path.stem
    desc = meta.get("description", "")
    tools = _parse_tools(meta.get("tools", ""))
    return Skill(name=name, description=desc, prompt=prompt, tools=tools)


def _parse_front(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        result[k.strip()] = v.strip()
    return result


def _parse_tools(raw: str) -> list[str] | None:
    if not raw:
        return None
    names = re.findall(r"[\w_]+", raw)
    return names if names else None
