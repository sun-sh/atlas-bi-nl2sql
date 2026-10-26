from __future__ import annotations

import os

# Keep the standard suite deterministic and offline-safe. The BGE integration
# path is exercised separately by the documented local evaluation command.
os.environ["USE_BGE"] = "false"
os.environ["LLM_ENABLED"] = "false"
os.environ["ATLAS_DISABLE_MODEL"] = "1"

from app.nl2sql.seed import generate_database  # noqa: E402
from app.nl2sql.settings import get_settings  # noqa: E402


def pytest_configure() -> None:
    settings = get_settings()
    if not settings.db_path.exists():
        generate_database(settings.db_path)
