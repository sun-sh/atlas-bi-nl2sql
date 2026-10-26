from __future__ import annotations

from pathlib import Path

from app.nl2sql.seed import generate_database


def test_empty_data_directory_can_be_seeded(tmp_path: Path):
    db_path = tmp_path / "commerce.db"
    counts = generate_database(db_path)
    assert db_path.exists()
    assert counts["orders"] > 0
    assert counts["marketing_spend"] > 0
