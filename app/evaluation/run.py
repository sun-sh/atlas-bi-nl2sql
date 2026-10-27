from __future__ import annotations

import json
import argparse
import hashlib
import sys
import time
from pathlib import Path

from app.nl2sql.evaluation import CASES, run_evaluation
from app.nl2sql.model_config import get_model_config
from app.nl2sql.seed import generate_database
from app.nl2sql.settings import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run strict model evaluation without rule fallback")
    parser.add_argument("--output", type=Path, help="Save a redacted JSON report")
    parser.add_argument("--min-hit-rate", type=float, help="Fail with exit code 2 below this percentage")
    parser.add_argument("--workers", type=int, choices=(1, 2, 3), default=1,
                        help="Isolated model workers; default 1")
    args = parser.parse_args(argv)
    if args.min_hit_rate is not None and not 0 <= args.min_hit_rate <= 100:
        parser.error("--min-hit-rate must be between 0 and 100")
    settings = get_settings()
    if not settings.db_path.exists():
        generate_database(settings.db_path)
    config = get_model_config()

    def redacted(value: dict) -> str:
        text = json.dumps(value, ensure_ascii=False, indent=2)
        for secret, replacement in ((config.api_key, "[REDACTED]"), (config.base_url, "[MODEL_ENDPOINT]")):
            if secret:
                text = text.replace(secret, replacement)
        return text

    def progress(case: dict) -> None:
        message = f"{case['id']}: {'PASS' if case['hit'] else 'FAIL'} ({case['elapsed_ms']} ms)"
        print(message, file=sys.stderr, flush=True)
        if args.output:
            with args.output.with_suffix(".cases.jsonl").open("a", encoding="utf-8") as checkpoint:
                checkpoint.write(redacted(case).replace("\n", " ") + "\n")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix(".cases.jsonl").write_text("", encoding="utf-8")
    started = time.perf_counter()
    source_root = Path(__file__).resolve().parents[1]
    source_hash = hashlib.sha256()
    for path in sorted(source_root.rglob("*.py")):
        source_hash.update(path.relative_to(source_root).as_posix().encode())
        source_hash.update(path.read_bytes())
    report = run_evaluation(on_case=progress, workers=args.workers)
    report["runtime"] = {"workers": args.workers, "wall_elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                         "source_sha256": source_hash.hexdigest()}
    report["evaluation_contract"] = "strict-v3-single-channel-scalar-v1"
    manifest = json.dumps(CASES, sort_keys=True, ensure_ascii=False).encode("utf-8")
    report["case_manifest_sha256"] = hashlib.sha256(manifest).hexdigest()
    if args.min_hit_rate is not None:
        passed = bool(report["available"] and report["total"] == len(CASES) and report["hit_rate"] >= args.min_hit_rate)
        report["quality_gate"] = {"min_hit_rate": args.min_hit_rate, "passed": passed}
    text = redacted(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 2 if args.min_hit_rate is not None and not report["quality_gate"]["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
