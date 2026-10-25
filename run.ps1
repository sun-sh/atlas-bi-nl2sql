$ErrorActionPreference = "Stop"
if (-not (Test-Path "data/commerce.db")) {
    python -m app.nl2sql.seed
}
python -m uvicorn app.api.main:app --reload --port 8000
