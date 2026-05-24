from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_USER_DIR = Path.home() / ".aicc" / "skills"

_FRONT_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)", re.DOTALL)


@dataclass
class Skill:
    name:        str
    description: str
    prompt:      str
    tools:       list[str] | None = field(default=None)  # None = all tools allowed


def load_all(cwd: str | None = None) -> list[Skill]:
    """Return skills from user-global and project-local .aicc/skills/.

    Load order: user-global (~/.aicc/skills/) then project-local (.aicc/skills/
    in cwd). Later entries override earlier ones with the same name.
    """
    skills: dict[str, Skill] = {}
    dirs = [_USER_DIR]
    if cwd:
        dirs.append(Path(cwd) / ".aicc" / "skills")
    for directory in dirs:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            skill = _parse(path)
            if skill:
                skills[skill.name] = skill
    return sorted(skills.values(), key=lambda s: s.name)


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
