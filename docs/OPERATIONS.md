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

Runtime configuration is stored at `data/model_config.json` and excluded from Git. The public configuration API masks the secret. If a model call fails, the query response includes the failure in `warnings` and falls back to rule SQL when possible.

## Quality gate

```bash
python -m pytest -q
python -m app.evaluation.run
```

A release candidate should have all tests passing, `execution_rate=100.0` and no unexpected warning regressions. Evaluation forces rule mode so external model latency and availability do not change the benchmark.

## Troubleshooting

| Symptom | Check | Action |
| --- | --- | --- |
| First query is slow | BGE initialization and network | Keep the service warm; pre-download the model in the deployment image |
| Model calls fail | `/api/health`, base URL, key, model name | Use `/models` connection test and inspect `warnings` |
| Retrieval falls back unexpectedly | `retrieval` block in health | Verify Qdrant/Elasticsearch connectivity or accept local degradation |
| SQL rejected | Attempt error and SQL guard message | Correct generated SQL or extend the approved schema contract |
| Evaluation result changes | Fixture seed and golden-case definitions | Regenerate a clean database and rerun tests |
| Configuration appears stale | Runtime config path and service process | Restart the service after changing environment variables |

## Deployment checks

- Keep `data/`, `.env` and model credentials outside version control.
- Terminate TLS at the ingress and restrict model configuration routes to trusted operators.
- Run the database account with read-only permissions.
- Export health, latency, trace ID and validation-failure metrics to the monitoring stack.
- Apply tenant and role context before exposing the service to multiple business units.
