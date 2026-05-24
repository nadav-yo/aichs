import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from config import IGNORED, MODELS
from storage.repository import ConversationStore


@dataclass
class PaletteItem:
    label: str
    subtitle: str = ""
    search_text: str = ""
    run: Callable[[], None] = field(default=lambda: None)

    def haystack(self) -> str:
        return self.search_text or f"{self.label} {self.subtitle}"


@dataclass
class PaletteContext:
    store: ConversationStore
    cwd: str
    is_streaming: Callable[[], bool]
    on_open_conversation: Callable[[str], None]
    on_open_file: Callable[[str], None]
    on_new_chat: Callable[[], None]
    on_export: Callable[[], None]
    on_compact: Callable[[], None]
    on_settings: Callable[[], None]
    on_stop: Callable[[], None]
    on_set_model: Callable[[str], None]


def fuzzy_score(query: str, text: str) -> int:
    q = query.casefold().strip()
    t = text.casefold()
    if not q:
        return 1
    if t.startswith(q):
        return 2000 - len(q)
    if q in t:
        return 1000 - t.index(q)
    qi = 0
    for ch in t:
        if qi < len(q) and q[qi] == ch:
            qi += 1
    if qi == len(q):
        return 100 + qi
    return 0


def filter_items(items: list[PaletteItem], query: str) -> list[PaletteItem]:
    q = query.strip()
    if not q:
        return items
    scored = [(fuzzy_score(q, it.haystack()), it) for it in items]
    scored = [(s, it) for s, it in scored if s > 0]
    scored.sort(key=lambda x: (-x[0], x[1].label.casefold()))
    return [it for _, it in scored]


def build_palette_items(ctx: PaletteContext) -> list[PaletteItem]:
    items: list[PaletteItem] = []

    items.append(PaletteItem(
        "New chat", "/new",
        search_text="/new new chat clear start",
        run=ctx.on_new_chat,
    ))
    items.append(PaletteItem(
        "Export conversation", "/export",
        search_text="/export export markdown save",
        run=ctx.on_export,
    ))
    items.append(PaletteItem(
        "Compact context", "/compact",
        search_text="/compact compact context summarize shrink",
        run=ctx.on_compact,
    ))
    items.append(PaletteItem(
        "Clear chat", "/clear",
        search_text="/clear clear chat reset new",
        run=ctx.on_new_chat,
    ))
    items.append(PaletteItem(
        "Open settings", "Preferences",
        search_text="settings preferences config",
        run=ctx.on_settings,
    ))
    if ctx.is_streaming():
        items.append(PaletteItem(
            "Stop streaming", "Esc",
            search_text="stop cancel streaming esc",
            run=ctx.on_stop,
        ))

    for provider, models in MODELS.items():
        for model in models:
            items.append(PaletteItem(
                f"Switch model: {model}",
                provider,
                search_text=f"/model {model} {provider} model switch",
                run=lambda m=model: ctx.on_set_model(m),
            ))

    for path, data in ctx.store.list_all():
        title = data.get("title", "Untitled")
        p = str(path)
        items.append(PaletteItem(
            title,
            "Conversation",
            search_text=f"{title} conversation chat {p}",
            run=lambda fp=p: ctx.on_open_conversation(fp),
        ))

    root = Path(ctx.cwd)
    for fpath in _list_files(root, limit=400):
        rel = os.path.relpath(fpath, ctx.cwd)
        name = os.path.basename(fpath)
        items.append(PaletteItem(
            name,
            rel,
            search_text=f"{name} {rel} file open",
            run=lambda fp=fpath: ctx.on_open_file(fp),
        ))

    return items


def _list_files(root: Path, limit: int = 400) -> list[str]:
    found: list[str] = []
    root = root.resolve()

    def walk(dir_path: Path):
        if len(found) >= limit:
            return
        try:
            entries = sorted(dir_path.iterdir(), key=lambda e: e.name.lower())
        except PermissionError:
            return
        for entry in entries:
            if len(found) >= limit:
                return
            if entry.name in IGNORED or entry.name.startswith("."):
                continue
            if entry.is_file():
                found.append(str(entry))
            elif entry.is_dir():
                walk(entry)

    if root.is_dir():
        walk(root)
    return found
