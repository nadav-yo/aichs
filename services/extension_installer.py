from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
import stat

from services.tool_registry import (
    ExtensionPermissions,
    ExtensionRequirements,
    extension_static_summary,
    set_extension_enabled,
    _static_extension_description,
)


@dataclass(frozen=True)
class ExtensionInstallCandidate:
    name: str
    source_path: Path
    entrypoint: Path
    kind: str
    description: str = ""
    permissions: ExtensionPermissions = field(default_factory=ExtensionPermissions)
    requirements: ExtensionRequirements = field(default_factory=ExtensionRequirements)
    missing_requirements: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExtensionInstallSource:
    url: str
    kind: str
    checkout_path: Path
    temp_dir: Path
    candidates: list[ExtensionInstallCandidate]


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
            candidates = discover_extension_candidates(checkout_path)
            return ExtensionInstallSource(
                url=url,
                kind=self.kind,
                checkout_path=checkout_path,
                temp_dir=temp_dir,
                candidates=candidates,
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

    root_entry = base / "extension.py"
    if root_entry.is_file():
        candidates.append(_folder_candidate(base, root_entry))
        seen.add(root_entry.resolve())

    for entrypoint in sorted(base.glob("*/extension.py")):
        resolved = entrypoint.resolve()
        if resolved not in seen:
            candidates.append(_folder_candidate(entrypoint.parent, entrypoint))
            seen.add(resolved)

    for file in sorted(base.glob("*.py")):
        if file.name == "__init__.py" or file.resolve() in seen:
            continue
        candidates.append(_file_candidate(file))

    return candidates


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
        return Path.home() / ".aichs" / "extensions"
    if scope != "local":
        raise ValueError("scope must be 'local' or 'global'")
    if not cwd:
        raise ValueError("local extension installs require a workspace")
    return Path(cwd) / ".aichs" / "extensions"


def _folder_candidate(path: Path, entrypoint: Path) -> ExtensionInstallCandidate:
    summary = extension_static_summary(entrypoint)
    return ExtensionInstallCandidate(
        name=_safe_install_name(path.name or "extension"),
        source_path=path,
        entrypoint=entrypoint,
        kind="folder",
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
        description=_static_extension_description(path),
        permissions=summary.permissions,
        requirements=summary.requirements,
        missing_requirements=summary.missing_requirements,
    )


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
