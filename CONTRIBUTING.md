# Contributing

## Development flow

1. Create a focused branch.
2. Keep changes behind the existing module boundaries.
3. Add or update tests for behavior changes.
4. Run the quality gate locally.
5. Submit a pull request with the problem, design choice and verification evidence.

## Quality gate

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m app.evaluation.run
```

## Engineering expectations

- Never bypass the SQL AST validator for generated or repaired statements.
- Preserve read-only execution and bounded result behavior.
- Do not log or return API keys.
- Add schema and golden-case coverage together when extending the analytical domain.
- Make degradation observable through warnings, health status or trace data.
- Keep evaluation deterministic; external model services belong in separate model-mode checks.

## Commit guidance

Use imperative, scoped messages such as `fix: surface model fallbacks` or `feat: add schema evidence`. Do not commit runtime databases, local model configuration, screenshots from private environments or credentials.
