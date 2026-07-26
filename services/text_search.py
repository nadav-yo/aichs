from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Callable, Iterable

from config import IGNORED, MAX_FILE_PREVIEW_BYTES
from services.file_search import list_workspace_files
from services.performance import time_operation
from services.ripgrep import ripgrep_path
from services.subprocess_utils import popen_no_window


@dataclass(frozen=True)
class TextSearchMatch:
    path: str
    rel_path: str
    line_no: int
    line_text: str
    start: int
    end: int


def search_file_contents(
    root: str | Path,
    query: str,
    *,
    limit: int = 100,
    scan_limit: int | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> list[TextSearchMatch]:
    with time_operation(
        "text_search.scan",
        detail=f"query_len={len(query.strip())} limit={limit} scan_limit={scan_limit}",
    ):
        q = query.strip()
        if not q:
            return []

        root_path = Path(root).resolve()
        native_matches = _search_with_rg(root_path, q, limit=limit, cancelled=cancelled)
        if native_matches is not None:
            return native_matches
        folded_query = q.casefold()
        matches: list[TextSearchMatch] = []
        for file_path in list_workspace_files(root_path, limit=scan_limit):
            if cancelled and cancelled():
                return matches
            path = Path(file_path)
            try:
                raw = _read_preview_bytes(path)
            except OSError:
                continue
            if b"\0" in raw:
                continue
            for match in _iter_preview_matches(path, root_path, raw, q, folded_query):
                if cancelled and cancelled():
                    return matches
                matches.append(match)
                if len(matches) >= limit:
                    return matches
        return matches


def search_file_contents_with_candidates(
    root: str | Path,
    query: str,
    *,
    limit: int = 100,
    scan_limit: int | None = None,
    candidate_limit: int | None = None,
    candidates: Iterable[TextSearchMatch] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[list[TextSearchMatch], tuple[TextSearchMatch, ...]]:
    with time_operation(
        "text_search.refine" if candidates is not None else "text_search.scan",
        detail=(
            f"query_len={len(query.strip())} limit={limit} "
            f"scan_limit={scan_limit} candidate_limit={candidate_limit}"
        ),
    ):
        q = query.strip()
        if not q:
            return [], ()
        root_path = Path(root).resolve()
        # Native search always evaluates the full workspace.  Reusing a previous
        # result set would turn an ordinary query refinement into a hidden scan cap.
        native_matches = _search_with_rg(root_path, q, limit=limit, cancelled=cancelled)
        if native_matches is not None:
            return native_matches, ()
        if candidates is not None:
            return _filter_text_candidates(
                q,
                candidates,
                limit=limit,
                candidate_limit=candidate_limit,
                cancelled=cancelled,
            )

        folded_query = q.casefold()
        matches: list[TextSearchMatch] = []
        next_candidates: list[TextSearchMatch] = []
        for file_path in list_workspace_files(root_path, limit=scan_limit):
            if cancelled and cancelled():
                return matches, tuple(next_candidates)
            path = Path(file_path)
            try:
                raw = _read_preview_bytes(path)
            except OSError:
                continue
            if b"\0" in raw:
                continue
            for match in _iter_preview_matches(path, root_path, raw, q, folded_query):
                if cancelled and cancelled():
                    return matches, tuple(next_candidates)
                next_candidates.append(match)
                if len(matches) < limit:
                    matches.append(match)
                if candidate_limit is not None and len(next_candidates) >= candidate_limit:
                    return matches, tuple(next_candidates)
        return matches, tuple(next_candidates)


def _filter_text_candidates(
    query: str,
    candidates: Iterable[TextSearchMatch],
    *,
    limit: int,
    candidate_limit: int | None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[list[TextSearchMatch], tuple[TextSearchMatch, ...]]:
    matches: list[TextSearchMatch] = []
    next_candidates: list[TextSearchMatch] = []
    folded_query = query.casefold()
    for candidate in candidates:
        if cancelled and cancelled():
            break
        start = candidate.line_text.casefold().find(folded_query)
        if start < 0:
            continue
        match = TextSearchMatch(
            path=candidate.path,
            rel_path=candidate.rel_path,
            line_no=candidate.line_no,
            line_text=candidate.line_text,
            start=start,
            end=start + len(query),
        )
        next_candidates.append(match)
        if len(matches) < limit:
            matches.append(match)
        if candidate_limit is not None and len(next_candidates) >= candidate_limit:
            break
    return matches, tuple(next_candidates)


def _search_with_rg(
    root: Path,
    query: str,
    *,
    limit: int,
    cancelled: Callable[[], bool] | None,
) -> list[TextSearchMatch] | None:
    """Search all eligible workspace files with Ripgrep's structured output.

    ``None`` means Ripgrep is unavailable and callers should use the portable
    Python fallback.  Returning an empty list is a successful search with no hits.
    """

    rg = ripgrep_path()
    if not rg:
        return None
    command = [
        rg,
        "--json",
        "--line-number",
        "--column",
        "--color",
        "never",
        "--no-messages",
        "--fixed-strings",
        "--ignore-case",
    ]
    for ignored in sorted(IGNORED):
        command.extend(("--glob", f"!{ignored}/**"))
    command.extend(("--", query, str(root)))
    try:
        process = popen_no_window(
            command,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None

    matches: list[TextSearchMatch] = []
    try:
        assert process.stdout is not None
        for raw_line in process.stdout:
            if cancelled and cancelled():
                _stop_process(process)
                return matches
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "match":
                continue
            data = event.get("data") or {}
            path_text = str((data.get("path") or {}).get("text") or "")
            line_text = str((data.get("lines") or {}).get("text") or "").rstrip("\r\n")
            if not path_text:
                continue
            display_line = line_text.strip()
            start = display_line.casefold().find(query.casefold())
            if start < 0:
                continue
            path = Path(path_text)
            try:
                rel_path = str(path.resolve().relative_to(root))
            except (OSError, ValueError):
                rel_path = path_text
            matches.append(
                TextSearchMatch(
                    path=str(path),
                    rel_path=rel_path,
                    line_no=int(data.get("line_number") or 1),
                    line_text=display_line,
                    start=start,
                    end=start + len(query),
                )
            )
            if len(matches) >= limit:
                _stop_process(process)
                return matches
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        _stop_process(process)
        return None
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
    return matches


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)


def _read_preview_bytes(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(MAX_FILE_PREVIEW_BYTES)


def _iter_preview_matches(
    path: Path,
    root_path: Path,
    raw: bytes,
    query: str,
    folded_query: str,
) -> Iterable[TextSearchMatch]:
    text = raw.decode("utf-8", errors="replace")
    if folded_query not in text.casefold():
        return

    path_text = str(path)
    rel_path = str(path.relative_to(root_path))
    for line_no, line in enumerate(text.splitlines(), start=1):
        match = _line_match(path_text, rel_path, line_no, line, query, folded_query)
        if match is not None:
            yield match


def _line_match(
    path: str,
    rel_path: str,
    line_no: int,
    line: str,
    query: str,
    folded_query: str,
) -> TextSearchMatch | None:
    start = line.casefold().find(folded_query)
    if start < 0:
        return None
    return TextSearchMatch(
        path=path,
        rel_path=rel_path,
        line_no=line_no,
        line_text=line.strip(),
        start=start,
        end=start + len(query),
    )
