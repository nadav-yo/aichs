from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
import stat

import config
from services.tool_registry import (
    ExtensionPermissions,
    ExtensionRequirements,
    extension_content_hash,
    extension_static_summary,
    set_extension_enabled,
    _static_extension_description,
)


@dataclass(frozen=True)
class ExistingExtensionInstall:
    path: Path
    modified_at: datetime | None = None
    scope: str = ""
    replaces_target: bool = True
    same_content: bool = False


@dataclass(frozen=True)
class ExtensionInstallCandidate:
    name: str
    source_path: Path
    entrypoint: Path
    kind: str
    display_name: str = ""
    description: str = ""
    permissions: ExtensionPermissions = field(default_factory=ExtensionPermissions)
    requirements: ExtensionRequirements = field(default_factory=ExtensionRequirements)
    missing_requirements: list[str] = field(default_factory=list)
    source_commit: str = ""
    source_commit_date: str = ""


@dataclass(frozen=True)
class ExtensionInstallSource:
    url: str
    kind: str
    checkout_path: Path
    temp_dir: Path
    candidates: list[ExtensionInstallCandidate]
    commit_hash: str = ""
    commit_date: str = ""


@dataclass(frozen=True)
class ExtensionInstallResult:
    name: str
    path: Path


class ExtensionSourceResolver:
    kind = "source"

    def can_handle(self, url: str) -> bool:
        raise NotImplementedError

    def prepare(self, url: str) -> ExtensionInstallSource:
        raise NotImplementedError


class GitExtensionSourceResolver(ExtensionSourceResolver):
    kind = "git"

    def can_handle(self, url: str) -> bool:
        value = str(url or "").strip()
        if not value:
            return False
        if value.startswith(("http://", "https://", "ssh://", "git://", "file://")):
            return True
        if value.startswith("git@"):
            return True
        return Path(value).exists()

    def prepare(self, url: str) -> ExtensionInstallSource:
        temp_dir = Path(tempfile.mkdtemp(prefix="aichs-ext-"))
        checkout_path = temp_dir / _source_name_from_url(url)
        try:
            _run_git_clone(url, checkout_path)
            commit_hash, commit_date = git_source_metadata(checkout_path)
            candidates = with_source_metadata(
                discover_extension_candidates(checkout_path),
                commit_hash,
                commit_date,
            )
            return ExtensionInstallSource(
                url=url,
                kind=self.kind,
                checkout_path=checkout_path,
                temp_dir=temp_dir,
                candidates=candidates,
                commit_hash=commit_hash,
                commit_date=commit_date,
            )
        except Exception:
            _rmtree(temp_dir)
            raise


_RESOLVERS: tuple[ExtensionSourceResolver, ...] = (GitExtensionSourceResolver(),)


def prepare_extension_install_source(url: str) -> ExtensionInstallSource:
    value = str(url or "").strip()
    for resolver in _RESOLVERS:
        if resolver.can_handle(value):
            return resolver.prepare(value)
    raise ValueError("Only git extension URLs are supported right now.")


def cleanup_extension_install_source(source: ExtensionInstallSource | None) -> None:
    if source is not None:
        _rmtree(source.temp_dir)


def discover_extension_candidates(root: str | Path) -> list[ExtensionInstallCandidate]:
    base = Path(root)
    candidates: list[ExtensionInstallCandidate] = []
    seen: set[Path] = set()
    children = _install_source_children(base)

    root_entry = base / "extension.py"
    if root_entry.is_file():
        candidates.append(_folder_candidate(base, root_entry))
        seen.add(root_entry.resolve())

    folder_entrypoints = []
    files = []
    for child in children:
        if child.is_dir():
            entrypoint = child / "extension.py"
            if entrypoint.is_file():
                folder_entrypoints.append(entrypoint)
        elif child.is_file() and child.suffix == ".py":
            files.append(child)

    for entrypoint in sorted(folder_entrypoints):
        resolved = entrypoint.resolve()
        if resolved not in seen:
            candidates.append(_folder_candidate(entrypoint.parent, entrypoint))
            seen.add(resolved)

    for file in sorted(files):
        if file.name == "__init__.py" or file.resolve() in seen:
            continue
        candidates.append(_file_candidate(file))

    return candidates


def _install_source_children(base: Path) -> list[Path]:
    try:
        return sorted(base.iterdir(), key=lambda item: item.name.casefold())
    except OSError:
        return []


def install_extension_candidates(
    candidates: list[ExtensionInstallCandidate],
    *,
    scope: str,
    cwd: str | None = None,
) -> list[ExtensionInstallResult]:
    target_root = extension_install_root(scope, cwd)
    target_root.mkdir(parents=True, exist_ok=True)
    results: list[ExtensionInstallResult] = []
    for candidate in candidates:
        dest = target_root / candidate.name
        _replace_path(candidate.source_path, dest, target_root)
        set_extension_enabled(dest, False, cwd)
        results.append(ExtensionInstallResult(name=candidate.name, path=dest))
    return results


def extension_install_root(scope: str, cwd: str | None = None) -> Path:
    if scope == "global":
        return config.AICHS_HOME / "extensions"
    if scope != "local":
        raise ValueError("scope must be 'local' or 'global'")
    if not cwd:
        raise ValueError("local extension installs require a workspace")
    return Path(cwd) / ".aichs" / "extensions"


def git_source_metadata(checkout_path: Path) -> tuple[str, str]:
    """Return the cloned source HEAD as (short hash, ISO-8601 commit date)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout_path), "log", "-1", "--format=%H|%cI"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        return "", ""
    line = (result.stdout or "").strip()
    if "|" not in line:
        return "", ""
    full_hash, commit_date = line.split("|", 1)
    short_hash = full_hash[:12] if full_hash else ""
    return short_hash, commit_date.strip()


def with_source_metadata(
    candidates: list[ExtensionInstallCandidate],
    commit_hash: str,
    commit_date: str,
) -> list[ExtensionInstallCandidate]:
    if not commit_hash and not commit_date:
        return candidates
    enriched: list[ExtensionInstallCandidate] = []
    for candidate in candidates:
        if candidate.source_commit or candidate.source_commit_date:
            enriched.append(candidate)
            continue
        enriched.append(
            replace(
                candidate,
                source_commit=commit_hash,
                source_commit_date=commit_date,
            )
        )
    return enriched


def lookup_existing_install(
    name: str,
    *,
    scope: str,
    cwd: str | None = None,
) -> ExistingExtensionInstall | None:
    root = extension_install_root(scope, cwd)
    dest = _resolve_install_path(root, name)
    if dest is None:
        return None
    return _existing_from_path(dest)


def existing_install_for_candidate(
    candidate: ExtensionInstallCandidate,
    *,
    scope: str,
    cwd: str | None = None,
) -> ExistingExtensionInstall | None:
    target_root = extension_install_root(scope, cwd)
    for found_scope, root in _install_search_roots(scope, cwd):
        match = _existing_in_root(candidate, root)
        if match is None:
            continue
        return ExistingExtensionInstall(
            path=match.path,
            modified_at=match.modified_at,
            scope=found_scope,
            replaces_target=_path_under_root(match.path, target_root),
            same_content=install_content_matches(candidate, match.path),
        )
    return None


def install_conflicts(
    candidates: list[ExtensionInstallCandidate],
    *,
    scope: str,
    cwd: str | None = None,
) -> dict[str, ExistingExtensionInstall]:
    conflicts: dict[str, ExistingExtensionInstall] = {}
    for candidate in candidates:
        existing = existing_install_for_candidate(candidate, scope=scope, cwd=cwd)
        if existing is not None:
            conflicts[candidate.name] = existing
    return conflicts


def install_content_matches(
    candidate: ExtensionInstallCandidate,
    install_path: Path,
) -> bool:
    try:
        incoming_hash = extension_content_hash(candidate.entrypoint)
        installed_hash = extension_content_hash(_entrypoint_for_install_path(install_path))
    except OSError:
        return False
    return bool(incoming_hash) and incoming_hash == installed_hash


def _entrypoint_for_install_path(path: Path) -> Path:
    if path.is_file():
        return path
    entrypoint = path / "extension.py"
    return entrypoint if entrypoint.is_file() else path


def format_commit_date(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    return dt.strftime("%b %d, %Y")


def format_install_timestamp(dt: datetime | None) -> str:
    if dt is None:
        return "unknown time"
    return dt.strftime("%b %d, %Y %H:%M")


def _folder_candidate(path: Path, entrypoint: Path) -> ExtensionInstallCandidate:
    summary = extension_static_summary(entrypoint)
    return ExtensionInstallCandidate(
        name=_safe_install_name(path.name or "extension"),
        source_path=path,
        entrypoint=entrypoint,
        kind="folder",
        display_name=summary.display_name,
        description=_static_extension_description(entrypoint),
        permissions=summary.permissions,
        requirements=summary.requirements,
        missing_requirements=summary.missing_requirements,
    )


def _file_candidate(path: Path) -> ExtensionInstallCandidate:
    summary = extension_static_summary(path)
    return ExtensionInstallCandidate(
        name=_safe_install_name(path.name),
        source_path=path,
        entrypoint=path,
        kind="file",
        display_name=summary.display_name,
        description=_static_extension_description(path),
        permissions=summary.permissions,
        requirements=summary.requirements,
        missing_requirements=summary.missing_requirements,
    )


def _install_search_roots(scope: str, cwd: str | None) -> list[tuple[str, Path]]:
    ordered: list[tuple[str, Path]] = []
    if scope == "local" and cwd:
        ordered.append(("local", extension_install_root("local", cwd)))
    ordered.append(("global", extension_install_root("global", cwd)))
    if scope == "global" and cwd:
        ordered.append(("local", extension_install_root("local", cwd)))
    seen: set[Path] = set()
    unique: list[tuple[str, Path]] = []
    for label, root in ordered:
        resolved = root.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append((label, root))
    return unique


def _existing_in_root(
    candidate: ExtensionInstallCandidate,
    root: Path,
) -> ExistingExtensionInstall | None:
    direct = _resolve_install_path(root, candidate.name)
    if direct is not None:
        return _existing_from_path(direct)
    if not root.exists():
        return None
    candidate_keys = {candidate.name.casefold()}
    if candidate.display_name:
        candidate_keys.add(candidate.display_name.casefold())
    for entrypoint in _iter_install_entrypoints(root):
        install_path = _install_path_from_entrypoint(entrypoint)
        if _safe_install_name(install_path.name).casefold() == candidate.name.casefold():
            return _existing_from_path(install_path)
        summary = extension_static_summary(entrypoint)
        if summary.display_name and summary.display_name.casefold() in candidate_keys:
            return _existing_from_path(install_path)
    return None


def _path_under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _resolve_install_path(root: Path, name: str) -> Path | None:
    if not root.exists():
        return None
    exact = root / name
    if exact.exists():
        return exact
    target = name.casefold()
    try:
        children = root.iterdir()
    except OSError:
        return None
    for child in children:
        if child.name.casefold() == target:
            return child
    return None


def _install_path_from_entrypoint(entrypoint: Path) -> Path:
    if entrypoint.name == "extension.py" and entrypoint.parent.name:
        return entrypoint.parent
    return entrypoint


def _iter_install_entrypoints(root: Path) -> list[Path]:
    candidates: list[Path] = []
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name.casefold())
    except OSError:
        return candidates
    for child in children:
        if child.name == "__init__.py":
            continue
        if child.is_file() and child.suffix == ".py":
            candidates.append(child)
            continue
        if child.is_dir():
            entrypoint = child / "extension.py"
            if entrypoint.is_file():
                candidates.append(entrypoint)
    return sorted(candidates)


def _existing_from_path(dest: Path) -> ExistingExtensionInstall:
    entrypoint = dest if dest.is_file() else dest / "extension.py"
    modified_at = _path_modified_at(entrypoint if entrypoint.exists() else dest)
    return ExistingExtensionInstall(path=dest, modified_at=modified_at)


def _path_modified_at(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None


def _replace_path(source: Path, dest: Path, target_root: Path) -> None:
    resolved_root = target_root.resolve()
    resolved_dest = dest.resolve() if dest.exists() else dest.parent.resolve() / dest.name
    try:
        resolved_dest.relative_to(resolved_root)
    except ValueError:
        raise ValueError("extension install destination escaped the extensions directory")

    if dest.exists():
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    if source.is_dir():
        shutil.copytree(source, dest, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    else:
        shutil.copy2(source, dest)


def _run_git_clone(url: str, checkout_path: Path) -> None:
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(checkout_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"git clone failed: {detail}") from exc


def _rmtree(path: Path) -> None:
    if not path.exists():
        return

    def onerror(func, value, _exc_info):
        try:
            Path(value).chmod(stat.S_IWRITE)
            func(value)
        except OSError:
            pass

    shutil.rmtree(path, ignore_errors=False, onerror=onerror)


def _safe_install_name(value: str) -> str:
    cleaned = "".join(
        ch if ch.isalnum() or ch in ("-", "_", ".") else "_"
        for ch in str(value or "extension")
    ).strip("._-")
    return cleaned or "extension"


def _source_name_from_url(url: str) -> str:
    value = str(url or "").rstrip("/\\")
    tail = value.replace("\\", "/").split("/")[-1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    if not tail and value.startswith("git@") and ":" in value:
        tail = value.rsplit(":", 1)[-1]
    return _safe_install_name(tail or "source")
