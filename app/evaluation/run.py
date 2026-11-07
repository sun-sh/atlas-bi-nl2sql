from __future__ import annotations

import json
import argparse
import hashlib
import sys
import time
from pathlib import Path

from app.nl2sql.evaluation import (
    CASES,
    DATASET_RANGE,
    RETRIEVAL_CASES,
    SCOPE_CASES,
    SUITE_VERSION,
    run_evaluation,
    run_natural_language_evaluation,
    run_retrieval_ablation,
    run_rule_equivalence_evaluation,
    run_scope_evaluation,
)
from app.nl2sql.holdout import NATURAL_HOLDOUT_CASES
from app.nl2sql.holdout import HOLDOUT_CASES, HOLDOUT_DATASET_RANGE, HOLDOUT_SUITE_VERSION
from app.nl2sql.model_config import get_model_config
from app.nl2sql.seed import generate_database
from app.nl2sql.settings import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run NL2SQL model and deterministic quality evaluations")
    parser.add_argument("--output", type=Path, help="Save a redacted JSON report")
    parser.add_argument("--min-hit-rate", type=float, help="Fail with exit code 2 below this percentage")
    parser.add_argument("--workers", type=int, choices=(1, 2, 3), default=1,
                        help="Isolated model workers; default 1")
    parser.add_argument("--suite", choices=("regression", "holdout", "scope", "retrieval", "natural", "rules"), default="regression",
                        help="Evaluation suite: regression, holdout, scope guard, retrieval ablation, natural wording or rule equivalence")
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

    def _retrieval_progress(report: dict) -> None:
        if not args.output:
            return
        with args.output.with_suffix(".cases.jsonl").open("a", encoding="utf-8") as checkpoint:
            for name, variant in report.get("variants", {}).items():
                checkpoint.write(
                    redacted({"variant": name, **variant}).replace("\n", " ") + "\n"
                )

    def _natural_progress(report: dict) -> None:
        if not args.output:
            return
        with args.output.with_suffix(".cases.jsonl").open("a", encoding="utf-8") as checkpoint:
            for item in report.get("cases", []):
                checkpoint.write(redacted(item).replace("\n", " ") + "\n")

    def progress(case: dict) -> None:
        elapsed = case.get('elapsed_ms', 0)
        message = f"{case['id']}: {'PASS' if case['hit'] else 'FAIL'} ({elapsed} ms)"
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
    is_holdout = args.suite == "holdout"
    is_scope = args.suite == "scope"
    is_retrieval = args.suite == "retrieval"
    is_natural = args.suite == "natural"
    is_rules = args.suite == "rules"
    selected_cases = HOLDOUT_CASES if is_holdout else CASES
    if is_scope:
        report = run_scope_evaluation(on_case=progress)
    elif is_retrieval:
        report = run_retrieval_ablation()
        selected = report["variants"][report["selected"]]
        report["available"] = True
        report["passed"] = selected["full_recall_cases"]
        report["hit_rate"] = selected["full_recall_at_4"]
        report["top1_accuracy"] = selected["top1_accuracy"]
        report["mrr"] = selected["mrr"]
        report["evaluated_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        _retrieval_progress(report)
    elif is_natural:
        report = run_natural_language_evaluation()
        report["available"] = True
        report["evaluated_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        _natural_progress(report)
    elif is_rules:
        report = run_rule_equivalence_evaluation(on_case=progress)
        report["evaluated_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    else:
        report = run_evaluation(
            cases=tuple(selected_cases),
            on_case=progress,
            workers=args.workers,
            suite_version=HOLDOUT_SUITE_VERSION if is_holdout else SUITE_VERSION,
            dataset_range=HOLDOUT_DATASET_RANGE if is_holdout else DATASET_RANGE,
        )
    report["runtime"] = {"workers": args.workers, "wall_elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                         "source_sha256": source_hash.hexdigest()}
    report["evaluation_contract"] = (
        "scope-guard-v1"
        if is_scope
        else "natural-language-holdout-v1"
        if is_natural
        else "rule-equivalence-v1"
        if is_rules
        else "retrieval-ablation-v1"
        if is_retrieval
        else "strict-v3-result-equivalence-v1"
    )
    if is_scope or is_natural:
        # Hash the stable input manifest, not per-run elapsed timing or output
        # fields. Otherwise the same cases produce different "manifest" hashes.
        manifest_cases = (
            [
                {"id": case["id"], "question": case["question"], "expected_outcome": case.get("expected_outcome", "answered")}
                for case in NATURAL_HOLDOUT_CASES
            ]
            if is_natural
            else [
                {"id": case["id"], "question": case["question"], "outcome": case["outcome"]}
                for case in SCOPE_CASES
            ]
        )
    elif is_retrieval:
        manifest_cases = [
            {"question": case["question"], "expected_tables": sorted(case["expected"])}
            for case in RETRIEVAL_CASES
        ]
    elif is_rules:
        manifest_cases = [case["id"] for case in CASES]
    else:
        manifest_cases = [case["id"] for case in selected_cases]
    manifest = json.dumps(manifest_cases, sort_keys=True, ensure_ascii=False).encode("utf-8")
    report["case_manifest_sha256"] = hashlib.sha256(manifest).hexdigest()
    if args.min_hit_rate is not None:
        expected_total = (
            report["total"]
            if is_scope or is_retrieval or is_natural or is_rules
            else len(selected_cases)
        )
        passed = bool(report["available"] and report["total"] == expected_total and report["hit_rate"] >= args.min_hit_rate)
        report["quality_gate"] = {"min_hit_rate": args.min_hit_rate, "passed": passed}
    text = redacted(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 2 if args.min_hit_rate is not None and not report["quality_gate"]["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
