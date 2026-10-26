from __future__ import annotations

import json

from app.nl2sql.evaluation import run_evaluation
from app.nl2sql.seed import generate_database
from app.nl2sql.settings import get_settings


if __name__ == "__main__":
    settings = get_settings()
    if not settings.db_path.exists():
        generate_database(settings.db_path)
    print(json.dumps(run_evaluation(), ensure_ascii=False, indent=2))
