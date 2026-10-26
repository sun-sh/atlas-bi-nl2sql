# Security Policy

## Security model

Atlas BI treats model output as untrusted input. SQL is parsed and validated before execution, and the database connection is opened in read-only mode.

## Enforced controls

- Single-statement `SELECT` only.
- AST validation with sqlglot before execution.
- Authorized table and function allowlists.
- Explicit column selection; `SELECT *` is rejected.
- Sensitive-column blocking.
- Read-only database URI.
- Bounded result rows.
- Bounded SQL repair attempts.
- Server-side model credential storage with masked read responses.
- Explicit warnings when model generation or repair falls back.

## Credentials

Do not commit `.env` or `data/model_config.json`. Use the model configuration page or environment variables to provide credentials. Production deployments should use a secret manager and inject credentials at runtime.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Send a private report to the repository owner with a minimal reproduction, affected endpoint, expected impact and relevant logs. Do not include live credentials or customer data.

## Scope

This repository includes a deterministic analytical dataset and a local execution boundary. Before connecting production data, add organization-specific identity, authorization, audit retention, data classification and tenant-isolation controls.
