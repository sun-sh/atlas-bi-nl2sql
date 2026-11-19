from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "app"


def _git(*args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _tracked_source_paths(source_root: Path) -> list[Path]:
    """Return tracked Python/YAML files under source_root, never cache files."""
    try:
        relative = source_root.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        relative = ""
    output = _git("ls-files", "--", relative or ".")
    paths: list[Path] = []
    for line in output.splitlines():
        path = REPO_ROOT / line
        if path.suffix not in {".py", ".yaml"}:
            continue
        if not path.is_file():
            continue
        try:
            path.relative_to(source_root)
        except ValueError:
            continue
        paths.append(path)
    return sorted(paths)


def source_sha256(root: Path | None = None) -> str:
    """Hash tracked Python and YAML source files at their working-tree bytes.

    Untracked debug scripts and interpreter caches are deliberately excluded:
    the commit identity plus app_dirty records those separately. A report must
    bind the reviewed source manifest, not incidental files created by pytest
    or a local experiment.
    """
    source_root = (root or APP_ROOT).resolve()
    digest = hashlib.sha256()
    for path in _tracked_source_paths(source_root):
        digest.update(path.relative_to(source_root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def database_identity(target) -> dict[str, Any]:
    """Describe the active database without exposing paths or credentials."""
    if target is None:
        return {"kind": "", "dialect": "", "sha256": ""}
    path = getattr(target, "path", None)
    digest = ""
    if path is not None:
        try:
            h = hashlib.sha256()
            with Path(path).open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    h.update(chunk)
            digest = h.hexdigest()
        except OSError:
            digest = ""
    return {
        "kind": str(getattr(target, "kind", "")),
        "dialect": str(getattr(target, "dialect", "")),
        "sha256": digest,
    }


def evaluation_provenance() -> dict[str, Any]:
    """Return both commit identity and dirty-worktree evidence.

    The source hash intentionally remains a working-tree hash. It is useful
    only when paired with git_head and app_dirty; it does not identify a
    commit on its own.
    """
    # Untracked debug scripts and interpreter caches must not make archived
    # source snapshots drift. Tracked modifications still mark the app dirty.
    app_status = _git("status", "--porcelain", "--untracked-files=no", "--", "app")
    return {
        "source_sha256": source_sha256(),
        "git_head": _git("rev-parse", "HEAD"),
        "app_dirty": bool(app_status),
    }
