import os


def repo_path_candidates(repo_root: str, limit: int = 20000) -> list[str]:
    root = os.path.abspath(repo_root)
    ignored = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", "node_modules"}
    matches: list[str] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in ignored and not name.startswith(".ruff_cache")]
        dirs.sort(key=str.casefold)
        files.sort(key=str.casefold)
        try:
            rel_dir = os.path.relpath(current, root)
        except ValueError:
            continue
        rel_dir = "" if rel_dir == "." else rel_dir.replace("\\", "/")
        if rel_dir:
            matches.append(f"{rel_dir}/")
            if len(matches) >= limit:
                break
        for filename in files:
            rel = f"{rel_dir}/{filename}" if rel_dir else filename
            matches.append(rel.replace("\\", "/"))
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break
    return matches


def scope_refs(detail: str) -> list[str]:
    return [
        part.strip().lstrip("@").strip('"')
        for part in str(detail or "").replace(",", "\n").splitlines()
        if part.strip()
    ]


def normalize_scope_ref(ref: str) -> str:
    return str(ref or "").strip().lstrip("@").strip('"').replace("\\", "/").rstrip("/")


def scope_title(ref: str) -> str:
    trimmed = str(ref or "").rstrip("/\\")
    return os.path.basename(trimmed) or trimmed or "Scope"


def relative_ref(path: str, repo_root: str) -> str:
    value = str(path or "").strip()
    if not value:
        return "unknown"
    try:
        rel = os.path.relpath(os.path.abspath(value), os.path.abspath(repo_root))
    except ValueError:
        return value.replace("\\", "/")
    if rel.startswith(".."):
        return value.replace("\\", "/")
    return rel.replace("\\", "/")


def absolute_ref(ref: str, repo_root: str) -> str:
    if os.path.isabs(ref):
        return ref
    return os.path.abspath(os.path.join(repo_root, ref))
