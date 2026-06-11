from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from config import MAX_FILE_PREVIEW_BYTES
from services.file_search import list_workspace_files
from services.performance import time_operation


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
    scan_limit: int = 800,
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
        folded_query = q.casefold()
        matches: list[TextSearchMatch] = []
        for file_path in list_workspace_files(root_path, limit=scan_limit):
            if cancelled and cancelled():
                return matches
            path = Path(file_path)
            try:
                raw = path.read_bytes()[:MAX_FILE_PREVIEW_BYTES]
            except OSError:
                continue
            if b"\0" in raw:
                continue
            text = raw.decode("utf-8", errors="replace")
            for line_no, line in enumerate(text.splitlines(), start=1):
                if cancelled and cancelled():
                    return matches
                start = line.casefold().find(folded_query)
                if start < 0:
                    continue
                rel_path = str(path.relative_to(root_path))
                matches.append(
                    TextSearchMatch(
                        path=str(path),
                        rel_path=rel_path,
                        line_no=line_no,
                        line_text=line.strip(),
                        start=start,
                        end=start + len(q),
                    )
                )
                if len(matches) >= limit:
                    return matches
        return matches


def search_file_contents_with_candidates(
    root: str | Path,
    query: str,
    *,
    limit: int = 100,
    scan_limit: int = 800,
    candidate_limit: int = 2000,
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
        if candidates is not None:
            return _filter_text_candidates(
                q,
                candidates,
                limit=limit,
                candidate_limit=candidate_limit,
                cancelled=cancelled,
            )

        root_path = Path(root).resolve()
        matches: list[TextSearchMatch] = []
        next_candidates: list[TextSearchMatch] = []
        for file_path in list_workspace_files(root_path, limit=scan_limit):
            if cancelled and cancelled():
                return matches, tuple(next_candidates)
            path = Path(file_path)
            try:
                raw = path.read_bytes()[:MAX_FILE_PREVIEW_BYTES]
            except OSError:
                continue
            if b"\0" in raw:
                continue
            text = raw.decode("utf-8", errors="replace")
            for line_no, line in enumerate(text.splitlines(), start=1):
                if cancelled and cancelled():
                    return matches, tuple(next_candidates)
                match = _line_match(path, root_path, line_no, line, q)
                if match is None:
                    continue
                next_candidates.append(match)
                if len(matches) < limit:
                    matches.append(match)
                if len(next_candidates) >= candidate_limit:
                    return matches, tuple(next_candidates)
        return matches, tuple(next_candidates)


def _filter_text_candidates(
    query: str,
    candidates: Iterable[TextSearchMatch],
    *,
    limit: int,
    candidate_limit: int,
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
        if len(next_candidates) >= candidate_limit:
            break
    return matches, tuple(next_candidates)


def _line_match(
    path: Path,
    root_path: Path,
    line_no: int,
    line: str,
    query: str,
) -> TextSearchMatch | None:
    start = line.casefold().find(query.casefold())
    if start < 0:
        return None
    return TextSearchMatch(
        path=str(path),
        rel_path=str(path.relative_to(root_path)),
        line_no=line_no,
        line_text=line.strip(),
        start=start,
        end=start + len(query),
    )
