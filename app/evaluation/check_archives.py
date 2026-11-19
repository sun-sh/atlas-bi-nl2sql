from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from app.evaluation.provenance import source_sha256


REQUIRED_RUNTIME_FIELDS = {"source_sha256", "git_head", "app_dirty"}


def _git(*args: str) -> bool:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def validate_report(
    path: Path,
    *,
    require_clean: bool = False,
    require_current_source: bool = False,
) -> list[str]:
    errors: list[str] = []
    try:
        report: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{path}: unreadable report: {exc}"]
    runtime = report.get("runtime")
    if not isinstance(runtime, dict):
        return [f"{path}: missing runtime object"]
    missing = REQUIRED_RUNTIME_FIELDS - set(runtime)
    if missing:
        errors.append(f"{path}: missing runtime fields: {sorted(missing)}")
    head = str(runtime.get("git_head") or "")
    if head and (len(head) < 7 or any(ch not in "0123456789abcdef" for ch in head.lower())):
        errors.append(f"{path}: invalid git_head: {head!r}")
    digest = str(runtime.get("source_sha256") or "")
    if digest and (len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest.lower())):
        errors.append(f"{path}: invalid source_sha256")
    if head and not _git("cat-file", "-e", f"{head}^{{commit}}"):
        errors.append(f"{path}: git_head does not resolve to a commit: {head!r}")
    # Squash merges rewrite commit identity, so ancestry alone is too brittle
    # and also too weak. The source digest is the actual code snapshot contract.
    if require_current_source and digest != source_sha256():
        errors.append(
            f"{path}: source_sha256 does not match the current app/ snapshot"
        )
    if require_clean and runtime.get("app_dirty") is not False:
        errors.append(f"{path}: app_dirty must be false for archived release evidence")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate archived evaluation provenance")
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--require-current-source", action="store_true")
    args = parser.parse_args(argv)
    errors = [
        error
        for path in args.reports
        for error in validate_report(
            path,
            require_clean=args.require_clean,
            require_current_source=args.require_current_source,
        )
    ]
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
