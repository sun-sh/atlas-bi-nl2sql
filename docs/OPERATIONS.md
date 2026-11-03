# Operations Runbook

## Startup

```bash
python -m pip install -r requirements.txt
python -m app.nl2sql.seed
python -m uvicorn app.api.main:app --port 8000
```

The application creates a missing deterministic database on demand. The first BGE-backed request may take longer while local embeddings initialize.

## Readiness checks

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/overview
```

`/api/health` reports database availability, active retrieval backends, model activation and repair limits. A healthy response has `status: ok` and `database: true`.

## Model configuration

1. Open `/models`.
2. Enter an OpenAI-compatible Base URL and API key.
3. Load the model list.
4. Run a connectivity test.
5. Save and enable the model.

Runtime configuration is stored at `data/model_config.json` (override with `ATLAS_MODEL_CONFIG_PATH`) and excluded from Git. The public configuration API never returns the API key; it does return the saved Base URL and a boolean `api_key_configured`. If a model call fails, the query response includes the failure in `warnings` and falls back to rule SQL when possible. Result narration uses the same model only after SQL execution succeeds; a narration failure falls back to a deterministic digest and never invalidates the query result.

## Session data

Session memory is stored in `data/sessions.db` and excluded from Git. It is short-lived by design: 7 days and at most 20 turns per session. Delete a session with `DELETE /api/sessions/{id}` when clearing context. For a fresh environment, simply remove the local session database while the service is stopped.

## Streaming endpoint

`POST /api/query/stream` uses Server-Sent Events. Clients should handle `result`, `delta`, `reset`, `thinking`, `narrative` and `done`. The `result` event carries trusted rows, SQL, evidence and a deterministic first-screen `answer`; the final model text arrives later. `delta` events append model content, `reset` replaces a partial draft with the deterministic digest, and `narrative` supplies the final text and source mode. When `session_id` is supplied, `done` returns the persisted session snapshot. Disable proxy buffering for this route.

For Qwen-family models, both SQL generation/repair and result narration send `chat_template_kwargs.enable_thinking=false`, so output tokens are spent on the answer rather than a hidden reasoning phase. Other OpenAI-compatible providers do not receive this extension.

## Quality gate

```bash
python -m pytest -q
python -m app.evaluation.run
```

A release candidate should have all tests passing and no unexpected warning regressions.

SQL execution is bounded by `QUERY_TIMEOUT_SECONDS` (default 5 seconds) and `MAX_RESULT_ROWS`. Running queries can be cancelled with `POST /api/query/cancel/{trace_id}`; the browser stops its SSE reader and the server interrupts the SQLite progress handler. The harness also rejects recursive CTEs, CROSS JOIN, more than three JOINs and projection-level `*` in subqueries. Raise these limits only with a matching load test; do not silently widen them for one-off demos. Evaluation uses the configured model in strict mode: record `execution_rate`, `hit_rate` and `model_rate` as observed, and never treat rule fallback as a model pass. Model-dependent scores are a dated snapshot, not a deterministic CI gate.

## Troubleshooting

| Symptom | Check | Action |
| --- | --- | --- |
| First query is slow | BGE initialization and network | Keep the service warm; pre-download the model in the deployment image |
| Model calls fail | `/api/health`, base URL, key, model name | Use `/models` connection test and inspect `warnings` |
| Retrieval falls back unexpectedly | `retrieval` block in health | Verify Qdrant/Elasticsearch connectivity or accept local degradation |
| SQL rejected | Attempt error and SQL guard message | Correct generated SQL or extend the approved schema contract |
| Evaluation result changes | Fixture seed and strict regression-case definitions | Regenerate a clean database and rerun tests |
| Configuration appears stale | Runtime config path and service process | Restart the service after changing environment variables |

## Deployment checks

- Keep `data/`, `.env` and model credentials outside version control.
- Terminate TLS at the ingress and restrict model configuration routes to trusted operators.
- Run the database account with read-only permissions.
- Export health, latency, trace ID and validation-failure metrics to the monitoring stack.
- Apply tenant and role context before exposing the service to multiple business units.

## Data-platform storage

The project-level data platform keeps metadata, snapshots, quality runs and audit records in `data/data_platform.db`; generated platform warehouse data defaults to `data/platform_warehouse.db`. Override these with `ATLAS_DATA_PLATFORM_DB_PATH` and `ATLAS_PLATFORM_WAREHOUSE_DB_PATH`. Source registration accepts project-accessible SQLite and CSV paths, with `snapshot` or `ingest` synchronisation modes. The scheduler endpoint is an explicit due-sync entry point, not a resident cluster scheduler.

## Evaluation-platform storage

Evaluation datasets, experiments, feedback and queue tasks live in `data/evaluation_tasks.db`; override with `ATLAS_EVALUATION_DB_PATH`. The queue enforces one running evaluation at a time. A task may run 1-3 isolated model processes. Execution is triggered by FastAPI `BackgroundTasks` for the submitted task or by the explicit `POST /api/evaluation/queue/claim` worker path; there is no Celery-style resident distributed worker. Queued rows survive a process restart but must be claimed again before they resume.

The default local role context is an administrator. Workspace and viewer/analyst/admin checks exist in the data-platform functions, but SSO, multi-tenant identity propagation and production authorization are not included.
