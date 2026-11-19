# Provenance

Persisted evaluation reports store:

- `git_head`: commit checked out when the report was produced.
- `app_dirty`: whether tracked files under `app/` had uncommitted changes.
- `source_sha256`: SHA-256 over the **Git-tracked** Python and YAML files under
  `app/`, using their working-tree bytes.
- `database.sha256`: active SQLite database fingerprint when available.

The source hash deliberately excludes untracked debug scripts and interpreter
cache files. `git ls-files` supplies the source manifest, so a report binds the
reviewed source set rather than incidental files created by pytest or a local
experiment.

## Archive gate

```bash
python -m app.evaluation.check_archives \
  docs/evaluations/rule-equivalence-v3-103.json \
  docs/evaluations/large-schema-v1.json \
  --require-clean --require-current-source
```

`--require-clean` requires `app_dirty=false`. `--require-current-source`
requires each report's `source_sha256` to equal the current tracked `app/`
source snapshot. Commit ancestry is intentionally not required because squash
merges rewrite commit identity; the source digest is the code-snapshot
contract.
