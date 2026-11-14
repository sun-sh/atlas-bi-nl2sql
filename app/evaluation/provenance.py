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


def source_sha256(root: Path | None = None) -> str:
    """Hash the working-tree Python and YAML files as an untracked snapshot."""
    source_root = root or APP_ROOT
    digest = hashlib.sha256()
    for path in sorted(
        item for item in source_root.rglob("*") if item.suffix in {".py", ".yaml"}
    ):
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
    app_status = _git("status", "--porcelain", "--", "app")
    return {
        "source_sha256": source_sha256(),
        "git_head": _git("rev-parse", "HEAD"),
        "app_dirty": bool(app_status),
    }
