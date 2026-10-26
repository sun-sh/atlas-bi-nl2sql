# Evaluation Snapshot

Dataset: deterministic simulated e-commerce data, 2025-10-01 to 2026-02-28.

The evaluation exercises the configured model end to end: schema retrieval,
model SQL generation, SQL repair, AST validation, read-only execution, golden
result assertions and grounded result interpretation. Rule SQL remains a
runtime availability fallback, but the evaluation disables it. A model that
does not produce SQL, or whose interpretation falls back to a deterministic
digest, is recorded as a failed case and never counted as a model pass.

## Latest local model snapshot

- Model: `qwen/qwen3.8-flash`
Observed runs were not stable across repeated calls: one recent run passed 5/6,
while another returned 4/6. The table below records a representative failing
run rather than hiding the variance behind the best result.

- Golden cases: 6
- Passed: 4/6
- Result hit rate: 66.7%
- Model completion rate: 83.3%
- SQL execution rate: 83.3%
- Average response time: about 5–6 seconds
- Automated tests: 42 passing in the current repository

One case returned model SQL that omitted the paid-order filter and therefore
missed the expected amount. Another returned no SQL at all and was recorded as a
failure without invoking rule fallback. Scores depend on the selected model,
provider latency and network and should be treated as a dated snapshot, not a
universal benchmark.

## Run locally

```bash
python -m pip install -r requirements-dev.txt
python -m app.evaluation.run
python -m pytest -q
```

The benchmark requires an enabled model for a model score. Without an active
model, the API returns `available=false` and zero scored cases rather than a
synthetic passing result.
