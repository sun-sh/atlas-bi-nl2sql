from __future__ import annotations

from app.nl2sql.semantic import SCHEMA, TABLE_BY_NAME


def schema_prompt() -> str:
    lines: list[str] = []
    for table in SCHEMA:
        lines.append(f"- {table.name}（{table.label}）: {table.description}")
        for col in table.columns:
            sensitivity = " [敏感，禁止展示]" if col.sensitivity != "public" else ""
            lines.append(f"  - {col.name} {col.type}: {col.description}{sensitivity}")
    return "\n".join(lines)


def allowed_tables() -> set[str]:
    return {table.name for table in SCHEMA}


def compact_schema_prompt(tables: list[str] | None = None) -> str:
    selected = [t for t in SCHEMA if tables is None or t.name in tables]
    lines = []
    for table in selected:
        cols = ", ".join(c.name for c in table.columns if c.sensitivity == "public")
        lines.append(f"{table.name}({cols})")
    return "\n".join(lines)


def semantic_schema_prompt(tables: list[str]) -> str:
    """Public fields with their real values and grain, for model SQL calls."""
    lines = []
    for table in SCHEMA:
        if table.name not in tables:
            continue
        lines.append(f"{table.name}（{table.label}，粒度 {table.grain}）：{table.description}")
        lines.extend(
            f"  {column.name} {column.type}: {column.description}"
            for column in table.columns if column.sensitivity == "public"
        )
    return "\n".join(lines)
