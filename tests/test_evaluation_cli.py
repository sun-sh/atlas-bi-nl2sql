from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.evaluation import run


@pytest.mark.parametrize("rate, available, exit_code", [(89, True, 2), (90, True, 0), (100, False, 2)])
def test_quality_gate_rejects_low_score_and_unavailable_model(monkeypatch, tmp_path, capsys, rate, available, exit_code):
    monkeypatch.setattr(run, "get_model_config", lambda: SimpleNamespace(api_key="fake-secret", base_url="https://private.invalid"))
    monkeypatch.setattr(run, "run_evaluation", lambda **kwargs: {
        "available": available, "total": len(run.CASES), "hit_rate": rate,
        "error": "fake-secret https://private.invalid",
    })
    output = tmp_path / "report.json"
    assert run.main(["--output", str(output), "--min-hit-rate", "90", "--workers", "3"]) == exit_code
    text = output.read_text(encoding="utf-8")
    assert "fake-secret" not in text
    assert "https://private.invalid" not in text
    report = json.loads(text)
    assert report["quality_gate"]["passed"] is (exit_code == 0)
    assert len(report["case_manifest_sha256"]) == 64


def test_invalid_worker_count_is_rejected():
    from app.nl2sql.evaluation import run_evaluation
    with pytest.raises(ValueError):
        run_evaluation(workers=100)
