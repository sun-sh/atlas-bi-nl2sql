# Operations Runbook

## Startup

```bash
python -m pip install -r requirements.txt
python -m app.nl2sql.seed
python -m uvicorn app.api.main:app --port 8000
```

The application creates a missing deterministic database on demand. The first BGE-backed request may take longer while local embeddings initialize. The relative-date anchor is read from `MAX(order_date)` in the active query database; `ATLAS_REFERENCE_DATE` is only a planner fallback for offline calls.

## Readiness checks

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/overview
```

`/api/health` reports the resolved analysis database, active retrieval backends, model activation and repair limits. SQL generation uses deterministic business-table routing by default; retrieval is evidence-only unless `ATLAS_RETRIEVAL_SUPPLEMENTS=1` is set explicitly. A healthy response has `status: ok` and `database: true`; inspect `query_database.source_id` and `query_database.fallback` to confirm which source is live. `/api/metrics` returns process-local request counts, error rates and P50/P95/max latency for the synchronous and streaming query paths, plus model-call admission, failure, token and latency counters.

## Model configuration

1. Open `/models`.
2. Enter an OpenAI-compatible Base URL and API key.
3. Load the model list.
4. Run a connectivity test.
5. Save and enable the model.

Runtime configuration is stored at `data/model_config.json` (override with `ATLAS_MODEL_CONFIG_PATH`) and excluded from Git. The public configuration API never returns the API key; it does return the saved Base URL and a boolean `api_key_configured`. If a model call fails, the query response includes the failure in `warnings` and falls back to rule SQL when possible. Result narration uses the same model only after SQL execution succeeds; a narration failure falls back to a deterministic digest and never invalidates the query result.

If `ATLAS_ADMIN_API_TOKEN` is set, model configuration/test, model probing, the data-platform control plane, quality rules, metrics, evaluation datasets/experiments/feedback/tasks and queue claims require `X-Admin-Token`. The browser reads the token from `localStorage` and sends it on mutating and protected operator-read calls. This shared secret protects the operator surface only; it is not user authentication, RBAC or a replacement for an ingress policy. The legacy synchronous `GET /api/evaluation` is disabled by default and returns 410 unless `ATLAS_ENABLE_SYNC_EVALUATION=1`.

## Session data

Session memory is stored in `data/sessions.db` and excluded from Git. It is short-lived by design: 7 days and at most 20 turns per session. Delete a session with `DELETE /api/sessions/{id}` when clearing context. For a fresh environment, simply remove the local session database while the service is stopped.

## Streaming endpoint

`POST /api/query/stream` uses Server-Sent Events. Clients should handle `result`, `delta`, `reset`, `thinking`, `narrative`, `done` and `cancelled`. The `result` event carries trusted rows, SQL, evidence and a deterministic first-screen `answer`; the final model text arrives later. `delta` events append model content, `reset` replaces a partial draft with the deterministic digest, and `narrative` supplies the final text and source mode. When `session_id` is supplied, `done` returns the persisted session snapshot. Disable proxy buffering for this route.

For Qwen-family models, both SQL generation/repair and result narration send `chat_template_kwargs.enable_thinking=false`, so output tokens are spent on the answer rather than a hidden reasoning phase. Other OpenAI-compatible providers do not receive this extension.

## Quality gate

```bash
python -m pytest -q
python -m app.evaluation.run
python -m app.evaluation.run --suite scope --min-hit-rate 100
python -m app.evaluation.run --suite rules --min-hit-rate 100
python -m app.evaluation.run --suite retrieval --min-hit-rate 100
python -m app.evaluation.run --suite natural --min-hit-rate 100
# Strict model snapshot; requires an enabled model and consumes quota.
python -m app.evaluation.run --suite colloquial-frozen --min-hit-rate 0
```

A release candidate should have all tests passing and no unexpected warning regressions. The scope, rule-equivalence, retrieval and natural-language gates are deterministic and do not require a model key; the strict model suites are dated capability snapshots and are not run as fixed CI scores. The frozen colloquial suite uses the strict model path; the current 7/13 result is a small fixed-dataset snapshot, not an open-domain score. The pytest suite includes four Playwright browser cases against a real local FastAPI process; CI installs Chromium for that gate.

`/api/metrics` includes a `model_calls` block with a process-wide concurrency cap, admission timeout, started/succeeded/failed/rejected counts, provider token usage and queue/call latency. These counters are in-memory and per process; aggregate them externally when running multiple workers. The call timeout and SQL token budgets are configurable through `MODEL_CALL_TIMEOUT_SECONDS`, `MODEL_MAX_CONCURRENCY`, `MODEL_ADMISSION_TIMEOUT_SECONDS`, `MODEL_SQL_MAX_TOKENS` and `MODEL_SQL_RETRY_MAX_TOKENS`.

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

## Load test

Start the service with `LLM_ENABLED=false`, `ATLAS_DISABLE_MODEL=1` and `USE_BGE=false`, then run a bounded local workload:

```bash
python -m app.evaluation.loadtest --base-url http://127.0.0.1:8000 --requests 20 --concurrency 4 --output docs/evaluations/http-loadtest-YYYY-MM-DD.json
```

Record the saved JSON and state the environment. The script covers HTTP plus deterministic rule SQL against the seeded SQLite database; it does not measure LLM latency, token cost, multi-worker contention, connection-pool saturation, failover or production capacity. Do not extrapolate its throughput to the model-enabled path.

## Active analysis source

List sources at `GET /api/data-platform/sources`, sync one, then activate it with `POST /api/data-platform/sources/{id}/activate`. Activation requires a ready source with the fixed five-table schema contract; SQLite files and explicitly configured PostgreSQL/MySQL DSNs are supported. CSV imports and incompatible databases are rejected. `/api/health` exposes the active source, dialect and fallback flag. If an activated SQLite file disappears, query and overview return 503 rather than silently querying a different database.

## External query databases

SQLite remains the default and the only backend covered by the fixed demo overview and offline evaluation fixtures. PostgreSQL and MySQL can be registered with an explicit read-only DSN, are inspected against the five-table schema contract before activation, and execute through the shared SQL AST guard and bounded adapter. The drivers are optional: install `psycopg` for PostgreSQL or `pymysql` for MySQL before using those targets. External databases do not yet have connection pooling, per-tenant row filters, automatic asset-catalog synchronization, or cross-thread active-query cancellation. Because the adapter uses one connection per query with `autocommit=True`, PostgreSQL receives session-level `SET statement_timeout = ...`; MySQL receives `SET SESSION MAX_EXECUTION_TIME = ...`. This still needs a live integration test and cross-thread cancellation; also set database/role timeouts and connect through a read-only account as deployment requirements.

The DSN is stored as plain text in the local metadata database (`data/data_platform.db` by default) and is not returned by public source/overview APIs. Protect that file, `.env`, backups and logs with filesystem permissions and a secrets manager appropriate for the deployment; the API redaction is not encryption at rest. The read-only guarantee is enforced by the database account and SELECT-only AST policy, not by the local demo's optional admin token.

## Deployment checks

- Keep `data/`, `.env` and model credentials outside version control.
- Terminate TLS at the ingress, set `ATLAS_ADMIN_API_TOKEN`, and restrict model/data-platform/evaluation operator routes to trusted networks.
- Run the database account with read-only permissions.
- Export health, process metrics, trace ID and validation-failure metrics to the monitoring stack; aggregate `/api/metrics` across workers externally.
- Apply tenant and role context before exposing the service to multiple business units.

## Data-platform storage

The project-level data platform keeps metadata, snapshots, quality runs and audit records in `data/data_platform.db`; generated platform warehouse data defaults to `data/platform_warehouse.db`. Override these with `ATLAS_DATA_PLATFORM_DB_PATH` and `ATLAS_PLATFORM_WAREHOUSE_DB_PATH`. Source registration accepts project-accessible SQLite and CSV paths, with `snapshot` or `ingest` synchronisation modes. The scheduler endpoint is an explicit due-sync entry point, not a resident cluster scheduler.

## Scope guard and narrative validation

`python -m app.evaluation.run --suite scope --min-hit-rate 100` runs the deterministic refusal/clarification suite without a model. Its cases must produce no SQL and no rows when the request is ambiguous, destructive, sensitive or outside the supported commerce domain.

The result narrative is checked against the deterministic digest before it is returned. A model answer that cites an unknown number, calls a non-leading row the top result, or states the opposite trend is replaced by the deterministic summary. The response exposes `outcome` and `narrative_validation` for inspection. These checks reduce confident-wrong answers; they do not certify every business explanation.

## Evaluation-platform storage

Evaluation datasets, experiments, feedback and queue tasks live in `data/evaluation_tasks.db`; override with `ATLAS_EVALUATION_DB_PATH`. The queue enforces one running evaluation at a time. A task may run 1-3 isolated model processes. Execution is triggered by FastAPI `BackgroundTasks` for the submitted task or by the explicit `POST /api/evaluation/queue/claim` worker path; there is no Celery-style resident distributed worker. Queued rows survive a process restart but must be claimed again before they resume.

The default local role context is an administrator. Workspace and viewer/analyst/admin checks exist in the data-platform functions, but SSO, multi-tenant identity propagation and production authorization are not included.
