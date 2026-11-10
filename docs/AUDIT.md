# Atlas BI project audit

Audit date: 2026-11-10

Scope: correctness, evaluation integrity, data/security boundaries, operations, and interview claims.

## Fixed in this audit

| Area | Risk found | Current behavior | Evidence |
| --- | --- | --- | --- |
| SQL safety | sqlglot exposes `orders` for `other_schema.orders`, so a table-name allowlist alone could accept a same-name relation in another schema. | Explicit schema/database qualifiers are rejected; the existing AST and table/function checks remain in force. | `tests/test_engine.py` cross-schema regression |
| SQL safety | A top-level `Select` check alone is not a complete expression type policy. | The current validator still rejects the tested `INTO` mutations through the table/keyword checks; the audit keeps this as a defence-in-depth item rather than claiming all dialect-specific SELECT variants are formally proven safe. | `tests/test_engine.py` |
| Evaluation datasets | A dataset checksum covered only case IDs, not the question or reference SQL. Changing the benchmark could silently change the meaning of a historical dataset. | New datasets persist a manifest with ID, question, intent and reference SQL; execution refuses a task when the stored snapshot no longer matches the executable benchmark. | dataset snapshot/tamper tests |
| Evaluation datasets | Empty `cases` could fall back to the full regression set because of `cases or CASES`. | `None` means the default suite; an explicitly empty selection raises an error. | eval-platform regression |
| Evaluation comparison | A comparison used shared IDs without saying whether dataset, manifest, source revision or model were the same. | The response now returns `comparable`, per-dimension `comparability` and a note; fixed/regressed entries are diagnostic when the comparison is not controlled. | comparison regression |
| External sources | A DSN alone was enough to write a successful `ready` snapshot even when the driver was missing or Schema inspection failed. | Sync probes the live external Schema; failure updates the source to `error` and never marks it ready. | external-source sync tests |
| External sources | Connection/Schema errors could echo credentials. | External connection, Schema and query errors redact the DSN and password before returning text. | DSN redaction tests |
| External data service | An external PostgreSQL/MySQL source fell through to a local SQLite path and returned a misleading file error. | It is rejected explicitly: the data service currently supports SQLite-backed snapshots only. | data-platform regression |
| SQLite lifecycle | `with sqlite3.connect(...)` commits but does not close; this caused Windows file locks and flaky temporary-directory cleanup. | Metadata, evaluation and session stores now use a context manager that commits/rolls back and always closes. | focused platform tests |
| Session concurrency | Two writers for one session could race on the turn cap and latest context. | The append path takes a write lock for the session transaction. | session tests |
| Frontend safety | API-derived strings were interpolated into `innerHTML` without escaping in several result/evidence paths. | High-risk result, evidence, chart and example strings are escaped; backend responses also set `nosniff`, `DENY` framing and a restrictive referrer policy. | JS syntax check and API security-header test |
| Operator surface | Data-platform and model-config read endpoints exposed source, lineage, audit and provider metadata without authentication. | When `ATLAS_ADMIN_API_TOKEN` is configured, protected reads also require `X-Admin-Token`; the browser supplies it for those routes. Local demo mode remains open when the token is unset. | API protected-read regression |
| Retrieval wiring | Recall results were passed to the generator but discarded; a later fix put all top-4 tables into the prompt, which made retrieval look influential without giving it discriminating power. | Default SQL generation now uses deterministic required tables only. `ATLAS_RETRIEVAL_SUPPLEMENTS=1` allows at most one top-1 supplemental table; the final frozen A/B scored 7/13 both with and without one supplement, with identical per-case hits, so the default is 0 because no generation gain was observed. | retrieval-context A/B and prompt regression |
| Retrieval concurrency | Elasticsearch scores were stored on the shared retriever, so concurrent searches could mix request-local scores. | Scores are returned as a local value for one `search()` call and never written on the shared instance. | retrieval concurrency regression |
| Retrieval lifecycle | Startup deleted shared Elasticsearch indices and Qdrant collections, which is destructive on a shared cluster. | Existing project-scoped indices are reused and upserted; names default to `atlas_schema_tables` and are configurable. | retrieval startup tests |
| Relative dates | Runtime planning could silently use a fixed demo date after the dataset date advanced. | The workflow derives the reference date from `MAX(order_date)` of the active database; direct offline planner calls use a documented fixed reference date. | planner/workflow regression |
| Request-local model errors | `LAST_LLM_ERROR` was process-global; concurrent requests could attribute one model failure to another request. | Generation and repair return request-scoped `(sql, error)` results; the old wrapper remains only for compatibility. | concurrency and strict-evaluation tests |
| Narrative grounding | Values with absolute magnitude `<= 1` bypassed fact checking, and two-decimal collisions could pass as grounded. | Small ratios/ROI values are checked and display rounding is derived from the candidate precision. | small-fraction hallucination test |
| Evaluation independence | The 100-case description did not distinguish raw cases from the repeated matrix shapes. | Reports expose raw case count plus `family_summary`; the latest strict model and rule runs both report 100 cases / 19 business-shape families. | family-summary regression |
| Evaluation integrity | An early colloquial set was rule-only, then a second 10/10 set shared a commit with the keyword patterns that made it pass. That score measured co-evolution, not transfer. | The question set is now frozen in its own commit (`4a07624`) with an independent manifest before semantic-layer changes. The final frozen run is honestly reported at 7/13; the old 10/10 remains as a development artifact only. | frozen colloquial manifest/report and CLI regression |
| PostgreSQL timeout | `SET LOCAL statement_timeout` under `autocommit=True` expires with the SET statement, so the following SELECT was not bounded. | The adapter now issues session-level `SET statement_timeout = %s` on its per-query connection; MySQL continues to use `SET SESSION MAX_EXECUTION_TIME`. Live database coverage is still missing. | DB-API adapter regression |
| API cost exposure | `/api/query` had no per-client admission and `/api/evaluation` ran a full suite synchronously. | Query routes have a process-local per-IP token bucket (`QUERY_RATE_LIMIT_PER_MINUTE`); legacy sync evaluation returns 410 unless `ATLAS_ENABLE_SYNC_EVALUATION=1`. | API regression tests |

## Still open before calling this production-ready

1. **Real database integration evidence.** The DB-API adapters have mock-driver tests, not live PostgreSQL/MySQL instances, migrations, TLS verification or failure/failover tests. They now issue dialect-specific server-side timeout commands, but that behavior still needs live integration coverage.
2. **Connection and transaction model.** There is no connection pool, per-request isolation policy, retry/backoff or circuit breaker for external databases.
3. **Tenant isolation.** Workspace and role parameters exist in the data-platform functions, but there is no authenticated identity propagation, row-level security or per-tenant database routing.
4. **Distributed execution.** The evaluation queue is SQLite-backed with one execution slot. Model concurrency and request metrics are process-local; multi-worker deployment needs a shared queue, leases and aggregated observability.
5. **Credential storage.** Model keys and external DSNs are stored in local files/SQLite metadata. They are masked or omitted from public responses, but not encrypted at rest or managed by a secret service.
6. **Complete browser hardening.** Escaping has been tightened in the identified paths, but the UI is still a large `innerHTML`-based rendering surface. A templating/DOMPurify pass and a Content-Security-Policy deployment policy remain necessary. Operator-read endpoints now share the optional token guard, but this remains a shared secret rather than SSO/RBAC.
7. **Open-domain evaluation.** The current 100-case regression, 30-case holdout, 15 natural-language variants, 13 frozen colloquial cases and 12 retrieval-search cases are project-defined and small. The 100-case matrix has 19 business-shape families; raw case count is not independent semantic coverage. The frozen colloquial run is 7/13, so these are regression and diagnostic evidence, not production accuracy.
8. **Evaluation provenance.** Source hash in a persisted task currently covers `app/` Python files, not the full repository, prompt/model configuration or database snapshot. Add those dimensions before treating historical task comparisons as reproducible experiments.
9. **Operational controls.** There is no SSO, full RBAC, distributed/ingress rate limiting, audit retention policy, backup/restore drill, dead-letter queue or alert routing. The query token bucket is process-local and only protects a single process.
10. **External query cancellation/timeouts.** PostgreSQL and MySQL now issue server-side statement-timeout commands, but there is no live integration test and no cross-thread active-query cancellation equivalent to SQLite's progress handler. Treat the external adapters as opt-in, not production-complete.

## Claims to avoid

- Do not call 100/100 or 29/30 an open-domain production accuracy.
- Do not present the rule-equivalence 100/100 or natural-language 15/15 as model capability.
- Do not present the old 10/10 development set as a holdout result; the frozen one-shot result is 7/13.
- Do not say retrieval is the default SQL-generation path or improves accuracy; it is evidence-only by default, and the frozen A/B was 7/13 both without and with one supplement, with no per-case change.
- Do not say 100 cases are 100 independent semantic samples; the matrix expands about 19 business shapes.
- Do not say PostgreSQL/MySQL are production database integrations; they are opt-in DB-API adapters with Schema probes.
- Do not say external-source data service is implemented; it is intentionally rejected.
- Do not claim full async execution, distributed evaluation or cross-worker metrics.
- Do not claim secret encryption at rest, SSO, tenant isolation or row-level security.

## Verification snapshot

- Full pytest suite: **191 passed** (sandbox environment required an approved temp-directory run; the code assertions all passed).
- Rule equivalence: **100/100 raw cases / 19 fully passed business-shape families**, forced rule path.
- Natural-language deterministic end-to-end: **15/15 raw cases / 8 fully passed families**.
- Frozen colloquial strict model snapshot: **7/13**, model rate 61.5%, execution rate 61.5%; report in [colloquial frozen JSON](evaluations/colloquial-frozen-2026-11-10.json).
- Retrieval search ablation: **Top-1 91.7%, MRR 0.9444, full table recall@4 100%** on the 12-case local fallback snapshot; one sales query still prefers the marketing table. This is search evidence, not a generation-gain claim.
- Strict model snapshots: **100/100** regression (19 families) and **29/30** holdout; dated `deepseek/deepseek-v4.1-flash` results, not a fixed CI guarantee. Holdout's sole miss is a relative-date inheritance error in the ROI follow-up.
