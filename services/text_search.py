from dataclasses import dataclass
from pathlib import Path

from config import MAX_FILE_PREVIEW_BYTES
from services.file_search import list_workspace_files


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
) -> list[TextSearchMatch]:
    q = query.strip()
    if not q:
        return []

    root_path = Path(root).resolve()
    folded_query = q.casefold()
    matches: list[TextSearchMatch] = []
    for file_path in list_workspace_files(root_path, limit=scan_limit):
        path = Path(file_path)
        try:
            raw = path.read_bytes()[:MAX_FILE_PREVIEW_BYTES]
        except OSError:
            continue
        if b"\0" in raw:
            continue
        text = raw.decode("utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), start=1):
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
