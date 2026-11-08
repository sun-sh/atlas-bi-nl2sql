# Atlas BI project audit

Audit date: 2026-11-08

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

## Still open before calling this production-ready

1. **Real database integration evidence.** The DB-API adapters have mock-driver tests, not live PostgreSQL/MySQL instances, migrations, TLS verification, server-side statement timeouts or failure/failover tests.
2. **Connection and transaction model.** There is no connection pool, per-request isolation policy, retry/backoff or circuit breaker for external databases.
3. **Tenant isolation.** Workspace and role parameters exist in the data-platform functions, but there is no authenticated identity propagation, row-level security or per-tenant database routing.
4. **Distributed execution.** The evaluation queue is SQLite-backed with one execution slot. Model concurrency and request metrics are process-local; multi-worker deployment needs a shared queue, leases and aggregated observability.
5. **Credential storage.** Model keys and external DSNs are stored in local files/SQLite metadata. They are masked or omitted from public responses, but not encrypted at rest or managed by a secret service.
6. **Complete browser hardening.** Escaping has been tightened in the identified paths, but the UI is still a large `innerHTML`-based rendering surface. A templating/DOMPurify pass and a Content-Security-Policy deployment policy remain necessary. Operator-read endpoints now share the optional token guard, but this remains a shared secret rather than SSO/RBAC.
7. **Open-domain evaluation.** The current 100-case regression, 30-case holdout, 15 natural-language variants and 12 retrieval cases are project-defined and small. They are useful regression evidence, not production accuracy.
8. **Evaluation provenance.** Source hash in a persisted task currently covers `app/` Python files, not the full repository, prompt/model configuration or database snapshot. Add those dimensions before treating historical task comparisons as reproducible experiments.
9. **Operational controls.** There is no SSO, full RBAC, rate limiting, audit retention policy, backup/restore drill, dead-letter queue or alert routing.

## Claims to avoid

- Do not call 99/100 or 28/30 an open-domain production accuracy.
- Do not present the rule-equivalence 100/100 or natural-language 15/15 as model capability.
- Do not say PostgreSQL/MySQL are production database integrations; they are opt-in DB-API adapters with Schema probes.
- Do not say external-source data service is implemented; it is intentionally rejected.
- Do not claim full async execution, distributed evaluation or cross-worker metrics.
- Do not claim secret encryption at rest, SSO, tenant isolation or row-level security.

## Verification snapshot

- Full pytest suite: **175 passed**.
- Rule equivalence: **100/100**, all cases executed through the forced rule path.
- Natural-language deterministic end-to-end: **15/15**.
- Retrieval ablation: **Top-1 91.7%, MRR 0.9583, full table recall@4 100%** on the 12-case local set.
- Strict model snapshots remain **99/100** regression and **28/30** holdout; they are dated model/network/configuration evidence, not a fixed CI guarantee.
