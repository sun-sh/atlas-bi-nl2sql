#!/usr/bin/env bash
set -euo pipefail
if [ ! -f data/commerce.db ]; then
  python -m app.nl2sql.seed
fi
python -m uvicorn app.api.main:app --reload --port 8000
