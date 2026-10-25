from __future__ import annotations

import json

from app.nl2sql.evaluation import run_evaluation


if __name__ == "__main__":
    print(json.dumps(run_evaluation(), ensure_ascii=False, indent=2))
