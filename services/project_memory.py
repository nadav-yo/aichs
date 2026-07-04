from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import config
from storage.settings import SettingsStore, project_memory_scope

MEMORY_VERSION = 1
LOCAL_MEMORY_PATH = Path(".aichs") / "memory" / "project-memory.json"
GLOBAL_MEMORY_PATH = Path("memory") / "global-memory.json"
VALID_MEMORY_SCOPES = {"local", "global", "disabled"}
MAX_TOPIC_CHARS = 80
MAX_KIND_CHARS = 32
MAX_TEXT_CHARS = 1000


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    kind: str
    topic: str
    text: str
    source: str
    created_at: str
    updated_at: str
    archived: bool = False

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "topic": self.topic,
            "text": self.text,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived": self.archived,
        }


class ProjectMemoryStore:
    def __init__(self, cwd: str, *, scope: str | None = None):
        self.cwd = str(cwd)
        self.scope = _normalize_scope(scope or project_memory_scope(SettingsStore().load()))

    @property
    def enabled(self) -> bool:
        return self.scope != "disabled"

    @property
    def path(self) -> Path:
        if self.scope == "global":
            return config.AICHS_HOME / GLOBAL_MEMORY_PATH
        return Path(self.cwd) / LOCAL_MEMORY_PATH

    def add(
        self,
        *,
        topic: str,
        text: str,
        kind: str = "decision",
        source: str = "user",
    ) -> MemoryRecord:
        if not self.enabled:
            raise ValueError("project memory is disabled")
        topic = normalize_topic(topic)
        kind = normalize_kind(kind)
        text = normalize_text(text)
        source = normalize_source(source)
        if not topic:
            raise ValueError("topic is required")
        if not text:
            raise ValueError("memory text is required")
        if len(topic) > MAX_TOPIC_CHARS:
            raise ValueError(f"topic must be {MAX_TOPIC_CHARS} characters or fewer")
        if len(kind) > MAX_KIND_CHARS:
            raise ValueError(f"kind must be {MAX_KIND_CHARS} characters or fewer")
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError(f"memory text must be {MAX_TEXT_CHARS} characters or fewer")

        data = self._load_data()
        now = datetime.now(timezone.utc).isoformat()
        record = MemoryRecord(
            id=uuid4().hex,
            kind=kind,
            topic=topic,
            text=text,
            source=source,
            created_at=now,
            updated_at=now,
            archived=False,
        )
        records = [record.as_dict(), *data.get("records", [])]
        data = {"version": MEMORY_VERSION, "records": records}
        self._save_data(data)
        return record

    def list(self, *, include_archived: bool = False) -> list[MemoryRecord]:
        if not self.enabled:
            return []
        records = [_coerce_record(item) for item in self._load_data().get("records", [])]
        return [record for record in records if record and (include_archived or not record.archived)]

    def search(self, query: str, *, include_archived: bool = False) -> list[MemoryRecord]:
        query = str(query or "").strip()
        records = self.list(include_archived=include_archived)
        if not query:
            return records
        terms = [term.casefold() for term in re.findall(r"\w+", query) if term]
        if not terms:
            terms = [query.casefold()]
        matches = []
        for record in records:
            haystack = " ".join((record.topic, record.kind, record.text)).casefold()
            if all(term in haystack for term in terms):
                matches.append(record)
        return matches

    def _load_data(self) -> dict:
        path = self.path
        if not path.exists():
            return {"version": MEMORY_VERSION, "records": []}
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {"version": MEMORY_VERSION, "records": []}
        if not isinstance(data, dict):
            return {"version": MEMORY_VERSION, "records": []}
        records = data.get("records")
        if not isinstance(records, list):
            records = []
        return {"version": MEMORY_VERSION, "records": records}

    def _save_data(self, data: dict) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")


def save_project_memory(cwd: str, *, topic: str, text: str, kind: str = "decision", source: str = "user") -> str:
    store = ProjectMemoryStore(cwd)
    record = store.add(topic=topic, text=text, kind=kind, source=source)
    return f"Saved {record.kind} memory under {record.topic!r}."


def read_project_memory(cwd: str, query: str = "", *, limit: int = 20) -> str:
    store = ProjectMemoryStore(cwd)
    if not store.enabled:
        return "Project memory is disabled."
    try:
        max_results = int(limit)
    except (TypeError, ValueError):
        max_results = 20
    max_results = max(1, min(50, max_results))
    records = store.search(query)[:max_results]
    if not records:
        if str(query or "").strip():
            return f"(no project memory matches {query!r})"
        return "(no project memory saved)"
    label = f"Project memory matches for {query!r}:" if str(query or "").strip() else "Project memory:"
    lines = [label]
    for record in records:
        lines.append(f"- [{record.kind}] {record.topic}: {record.text}")
    return "\n".join(lines)


def parse_save_memory_args(args: str) -> tuple[str, str] | None:
    text = str(args or "").strip()
    if not text:
        return None
    if ":" in text:
        topic, memory = text.split(":", 1)
        topic = topic.strip()
        memory = memory.strip()
        if topic and memory:
            return topic, memory
    parts = text.split(maxsplit=1)
    if len(parts) != 2:
        return None
    return parts[0], parts[1].strip()


def normalize_topic(value) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def normalize_kind(value) -> str:
    text = str(value or "decision").strip().casefold()
    text = re.sub(r"[^a-z0-9_-]+", "_", text)
    return text.strip("_-") or "decision"


def normalize_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_source(value) -> str:
    text = str(value or "user").strip().casefold()
    text = re.sub(r"[^a-z0-9_-]+", "_", text)
    return text.strip("_-") or "user"


def _normalize_scope(value: str) -> str:
    scope = str(value or "local").strip().casefold()
    return scope if scope in VALID_MEMORY_SCOPES else "local"


def _coerce_record(value) -> MemoryRecord | None:
    if not isinstance(value, dict):
        return None
    record_id = str(value.get("id") or "").strip()
    topic = normalize_topic(value.get("topic"))
    text = normalize_text(value.get("text"))
    if not record_id or not topic or not text:
        return None
    created = str(value.get("created_at") or "").strip()
    updated = str(value.get("updated_at") or created).strip()
    return MemoryRecord(
        id=record_id,
        kind=normalize_kind(value.get("kind")),
        topic=topic,
        text=text,
        source=normalize_source(value.get("source")),
        created_at=created,
        updated_at=updated,
        archived=bool(value.get("archived", False)),
    )
