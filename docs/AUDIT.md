# Atlas BI project audit

Audit date: 2026-11-10

Scope: correctness, evaluation integrity, data/security boundaries, operations, and interview claims.

## Hardening in this audit (2026-11-10)

| Area | Risk found | Current behavior | Evidence |
| --- | --- | --- |
| Semantic source | Table columns, enums, metric formulas, aliases and time words were copied across `schema_catalog.py`, `data_assets.py`, `planner.py`, `generator.py`, `result_insight.py`, `evaluation.py` and `seed.py`; copies could disagree without a test noticing. | `app/semantic.yaml` is the runtime single source. `semantic.py` renders the SQLite seed DDL, schema/metric prompts, rule SQL expressions **and filters**, result aliases, overview metrics and evaluation constants. Tests compare the actual SQLite columns with the YAML catalog. | `tests/test_semantic.py`, `tests/test_generator.py`, semantic reports |
| Evaluation contract | A single `hit` flag let plan metadata and answer correctness hide each other. | Strict and colloquial reports expose independent `answer_correct` and `plan_correct` fields and rates. Answer acceptance is driven by execution, an independent reference SQL and grounded narrative; plan correctness is checked separately. Evaluation storage, UI and comparison persist/show both. | evaluator independence tests, API/queue regressions |
| SQL guard | `CASE` was rejected because sqlglot exposes its nested `IF` as a `Func`. Subquery policy counted SELECT nodes, so sibling scalar subqueries could be rejected as if deeply nested. | `CASE`/`IF` expression nodes are exempt from the function allowlist. The harness computes real recursive SELECT depth, allowing wide sibling subqueries and rejecting depth beyond the configured limit. | `tests/test_engine.py` |
| Default dependencies | BGE, Qdrant, Elasticsearch and their transitive dependencies were mandatory for the local SQLite demo, and `USE_BGE` defaulted on. | Core `requirements.txt` contains only the local demo/runtime stack plus PyYAML. Optional retrieval packages live in `requirements-retrieval.txt`; `USE_BGE=false` and `ATLAS_RETRIEVAL_SUPPLEMENTS=0` are defaults. | clean CI install, import smoke check |
| Admin reads | Evaluation metadata, task reports and comparisons were readable without the optional operator gate even though the corresponding writes were protected. | All evaluation read routes now share `_require_admin_token`; local token-unset mode remains open for the demo. | API protected-read regression |
| Overview definition drift | The overview page maintained separate metric SQL and a hard-coded month while the semantic layer had moved. | Overview metrics and channel/dataset labels render from YAML and use semantic reference dates; a separate test compares the surfaced values with the local database. | `test_overview_metrics_uses_semantic_expressions` |
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
| Retrieval wiring | Recall results were passed to the generator but discarded; a later fix put all top-4 tables into the prompt, which made retrieval look influential without giving it discriminating power. | Default SQL generation now uses deterministic required tables only. `ATLAS_RETRIEVAL_SUPPLEMENTS=1` allows at most one top-1 supplemental table; the historical A/B found no measurable generation gain, so the default remains 0. Current frozen snapshot: answer 11/13, plan 9/13. | retrieval-context A/B and prompt regression |
| Retrieval concurrency | Elasticsearch scores were stored on the shared retriever, so concurrent searches could mix request-local scores. | Scores are returned as a local value for one `search()` call and never written on the shared instance. | retrieval concurrency regression |
| Retrieval lifecycle | Startup deleted shared Elasticsearch indices and Qdrant collections, which is destructive on a shared cluster. | Existing project-scoped indices are reused and upserted; names default to `atlas_schema_tables` and are configurable. | retrieval startup tests |
| Relative dates | Runtime planning could silently use a fixed demo date after the dataset date advanced. | The workflow derives the reference date from `MAX(order_date)` of the active database; direct offline planner calls use a documented fixed reference date. | planner/workflow regression |
| Request-local model errors | `LAST_LLM_ERROR` was process-global; concurrent requests could attribute one model failure to another request. | Generation and repair return request-scoped `(sql, error)` results; the old wrapper remains only for compatibility. | concurrency and strict-evaluation tests |
| Narrative grounding | Values with absolute magnitude `<= 1` bypassed fact checking, and two-decimal collisions could pass as grounded. | Small ratios/ROI values are checked and display rounding is derived from the candidate precision. | small-fraction hallucination test |
| Evaluation independence | The 100-case description did not distinguish raw cases from the repeated matrix shapes. | Reports expose raw case count plus `family_summary`; the latest strict model and rule runs both report 100 cases / 19 business-shape families. | family-summary regression |
| Evaluation integrity | An early colloquial set was rule-only, then a second 10/10 set shared a commit with the keyword patterns that made it pass. That score measured co-evolution, not transfer. | The question set is now frozen in its own commit (`4a07624`) with an independent manifest before semantic-layer changes. The current frozen run is honestly reported as answer 11/13 and plan 9/13; the old 10/10 remains as a development artifact only. | frozen colloquial manifest/report and CLI regression |
| PostgreSQL timeout | `SET LOCAL statement_timeout` under `autocommit=True` expires with the SET statement, so the following SELECT was not bounded. | The adapter now issues session-level `SET statement_timeout = %s` on its per-query connection; MySQL continues to use `SET SESSION MAX_EXECUTION_TIME`. Live database coverage is still missing. | DB-API adapter regression |
| API cost exposure | `/api/query` had no per-client admission and `/api/evaluation` ran a full suite synchronously. | Query routes have a process-local per-IP token bucket (`QUERY_RATE_LIMIT_PER_MINUTE`); legacy sync evaluation returns 410 unless `ATLAS_ENABLE_SYNC_EVALUATION=1`. | API regression tests |

## Still open before calling this production-ready

1. **Real database integration evidence.** The DB-API adapters have mock-driver tests, not live PostgreSQL/MySQL instances, migrations, TLS verification or failure/failover tests. They now issue dialect-specific server-side timeout commands, but that behavior still needs live integration coverage.
2. **Connection and transaction model.** There is no connection pool, per-request isolation policy, retry/backoff or circuit breaker for external databases.
3. **Tenant isolation.** Workspace and role parameters exist in the data-platform functions, but there is no authenticated identity propagation, row-level security or per-tenant database routing.
4. **Distributed execution.** The evaluation queue is SQLite-backed with one execution slot. Model concurrency and request metrics are process-local; multi-worker deployment needs a shared queue, leases and aggregated observability.
5. **Credential storage.** Model keys and external DSNs are stored in local files/SQLite metadata. They are masked or omitted from public responses, but not encrypted at rest or managed by a secret service.
6. **Complete browser hardening.** Escaping has been tightened in the identified paths, but the UI is still a large `innerHTML`-based rendering surface. A templating/DOMPurify pass and a Content-Security-Policy deployment policy remain necessary. Operator-read endpoints now share the optional token guard, but this remains a shared secret rather than SSO/RBAC.
7. **Open-domain evaluation.** The current 100-case regression, 30-case holdout, 15 natural-language variants, 13 frozen colloquial cases and 12 retrieval-search cases are project-defined and small. The 100-case matrix has 19 business-shape families; raw case count is not independent semantic coverage. The current frozen colloquial run is answer 11/13 and plan 9/13, so these are regression and diagnostic evidence, not production accuracy.
8. **Evaluation provenance.** Persisted reports now record `git_head`, `app_dirty` and a working-tree `source_sha256` over `app/` Python and YAML files. A dirty worktree still lacks a commit, prompt/model configuration and database snapshot, so dirty reports remain experiments rather than reproducible release evidence.
9. **Operational controls.** There is no SSO, full RBAC, distributed/ingress rate limiting, audit retention policy, backup/restore drill, dead-letter queue or alert routing. The query token bucket is process-local and only protects a single process.
10. **External query cancellation/timeouts.** PostgreSQL and MySQL now issue server-side statement-timeout commands, but there is no live integration test and no cross-thread active-query cancellation equivalent to SQLite's progress handler. Treat the external adapters as opt-in, not production-complete.

## Claims to avoid

- Do not call 100/100 or 30/30 answer-correct open-domain production accuracy; they are dated, small, fixed-domain snapshots.
- Do not collapse answer-correct and plan-correct into one score. A correct result can coexist with a wrong plan label, and vice versa.
- Do not present the rule-equivalence 100/100 or natural-language 15/15 as model capability.
- Do not present the old 10/10 development set as a holdout result; the current frozen snapshot is 11/13 answer-correct and 9/13 plan-correct.
- Do not say retrieval is the default SQL-generation path or improves accuracy; it is evidence-only by default.
- Do not say 100 cases are 100 independent semantic samples; the matrix expands about 19 business shapes.
- Do not say PostgreSQL/MySQL are production integrations; they are opt-in DB-API adapters with Schema probes and no live integration suite.
- Do not say external-source data service is implemented; it is intentionally rejected.
- Do not claim full async execution, distributed evaluation or cross-worker metrics.
- Do not claim secret encryption at rest, SSO, tenant isolation or row-level security.

## Known design limits

- **Rule-first semantic routing with an optional structured planner.** The deterministic guard and aliases remain the offline path; when a model is active, a Pydantic-validated planner constrained to `semantic.yaml` is tried first. The validator also enforces a separate executable-shape contract (intent/metric combinations, dimension/filter table closure and slot inheritance) before generation. Invalid output falls back to the rules. This is not an open-domain NLU claim and still needs calibration data before broad schema expansion.
- **Retrieval is a scoped experiment.** The five-table demo has no measured generation gain from retrieval supplements; it remains evidence-only by default. Qdrant, BGE and Elasticsearch are extension points, not a claim that RAG improves this benchmark.
- **LangGraph is used as a bounded state machine.** The graph is intentionally linear with a bounded repair loop. There is no checkpointer, human-in-the-loop interrupt/resume or parallel fan-out; a smaller state machine would express the same current runtime.
- **Single-process control planes.** Data and evaluation backends are local SQLite services in one FastAPI process. They demonstrate workflow and provenance, not distributed multi-tenant scale.
- **External adapter boundaries.** PostgreSQL/MySQL adapters are opt-in and lack pooling, live integration tests and active cross-thread cancellation; the SQLite demo is the supported reference deployment.

## Verification snapshot

Historical evidence was produced from source hash `1e111260d4bc14392adc72ffdec676e05efdfafafe7f4c44ae6fbe3267aa4178` after the semantic single-source refactor. This is a historical working-tree snapshot, not a commit identifier; new reports add `git_head` and `app_dirty` to make that distinction explicit.

- Full pytest suite: **307 passed** in an approved environment (Windows sandbox blocked pytest temp paths, named pipes and local sockets; the same code passed outside it).
- Rule equivalence (current v3-103): **103/103 raw cases** after adding three unfiltered scalar sales cases; historical v3-100 report remains **100/100**, forced rule path.
- Natural-language deterministic end-to-end: **15/15 raw cases / 8 fully passed families**.
- Scope guard: **7/7**.
- Strict model regression: answer **100/100**, plan **94/100**, execution/model rates **100%**; self-healing attempted and passed **2/2**. Report: `regression`（历史基线已移出精简归档；可用 `python -m app.evaluation.run --suite regression` 重跑）.
- Holdout: answer **30/30**, plan **28/30**, execution/model rates **100%**; self-healing attempted and passed **3/3**. The two plan-only misses are refund-rate questions where the answer was accepted but the plan metric was recorded as refund count. Report: [holdout](evaluations/archive/holdout-2026-11-10-final.json).
- Frozen colloquial strict model snapshot: answer **11/13**, plan **9/13**, execution/model rates **69.2%**. Report: [colloquial frozen](evaluations/archive/colloquial-frozen-2026-11-10-final.json).
- Retrieval search ablation: Top-1 **91.7%**, MRR **0.9444**, full table recall@4 **100%** on the 12-case local-IDF fallback. This is search evidence, not a generation-gain claim. Report: `python -m app.evaluation.run`（原报告未保留）.
- Large-schema ablation: on a 40-table synthetic catalog, Top-4 full recall is **66.7%** and Top-4-seeded, explicit foreign-key closure (10-table cap) reaches **91.7%** with **266.0** full prompt tokens; a same-budget pure Top-K arm also reaches **91.7%**, so closure is not claimed to improve recall. Report: [large-schema-v1.json](evaluations/large-schema-v1.json). This validates schema selection/closure, not SQL accuracy.

Historical reports remain immutable snapshots and must not be mixed with the current source hash.
