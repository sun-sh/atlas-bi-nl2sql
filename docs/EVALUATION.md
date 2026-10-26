# Evaluation Snapshot

Dataset: deterministic simulated e-commerce data, 2025-10-01 to 2026-02-28.

- Golden cases: 6
- SQL execution rate: 100%
- Result hit rate: 100%
- Latency: the first query includes local embedding initialization; warm queries complete in tens of milliseconds. Exact timing depends on hardware and the selected retrieval backend
- Covered intents: sales overview, category analysis, refund analysis, marketing efficiency, daily trend
- Automated tests: 26 passing in the final repository

Run locally:

```bash
python -m pip install -r requirements-dev.txt
python -m app.evaluation.run
python -m pytest -q
```

Measurements are produced by the included deterministic dataset and benchmark script.
