from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
import re
from typing import Any

import yaml

from app.nl2sql.models import ColumnInfo, TableInfo


SEMANTIC_PATH = Path(__file__).resolve().parents[1] / "semantic.yaml"


def _load() -> dict[str, Any]:
    with SEMANTIC_PATH.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError("semantic.yaml 必须是对象")
    return data


SEMANTIC = _load()
VERSION = str(SEMANTIC.get("version") or "unknown")
DATASET = dict(SEMANTIC.get("dataset") or {})
DATASET_START = str(DATASET.get("start") or "")
DATASET_END = str(DATASET.get("end") or "")
REFERENCE_DATE = date.fromisoformat(str(DATASET.get("reference_date")))
DATASET_REFERENCE_DATE = REFERENCE_DATE
DATASET_RANGE = f"{DATASET_START} 至 {DATASET_END}"

SCHEMA: tuple[TableInfo, ...] = tuple(
    TableInfo(
        name=str(table["name"]),
        label=str(table.get("label") or table["name"]),
        description=str(table.get("description") or ""),
        grain=str(table.get("grain") or ""),
        aliases=tuple(str(alias) for alias in table.get("aliases") or ()),
        columns=tuple(
            ColumnInfo(
                name=str(column["name"]),
                type=str(column.get("type") or "TEXT"),
                description=str(column.get("description") or ""),
                sensitivity=str(column.get("sensitivity") or "public"),
            )
            for column in table.get("columns") or ()
        ),
    )
    for table in SEMANTIC.get("tables") or ()
)
TABLE_BY_NAME = {table.name: table for table in SCHEMA}
TABLE_CONFIG = {str(table["name"]): table for table in SEMANTIC.get("tables") or ()}
TABLE_PROFILES = {
    name: {
        "key": list(config.get("key") or ()),
        "date_column": config.get("date_column"),
        "date_parent": tuple(config["date_parent"]) if config.get("date_parent") else None,
    }
    for name, config in TABLE_CONFIG.items()
}
RELATIONSHIPS = tuple(dict(item) for item in SEMANTIC.get("relationships") or ())
ENUMS = dict(SEMANTIC.get("enums") or {})
TIME_WINDOWS = dict(SEMANTIC.get("time_windows") or {})
FOLLOW_UP_TERMS = tuple(str(item) for item in SEMANTIC.get("follow_up_terms") or ())
INTENTS = dict(SEMANTIC.get("intents") or {})
DIMENSIONS = dict(SEMANTIC.get("dimensions") or {})
METRICS = tuple(dict(item) for item in SEMANTIC.get("metrics") or ())
DASHBOARD_METRICS = tuple(
    dict(item) for item in SEMANTIC.get("dashboard_metrics") or ()
)
DASHBOARD_METRIC_BY_NAME = {
    str(item["name"]): item for item in DASHBOARD_METRICS
}
METRIC_BY_NAME = {str(item["name"]): item for item in METRICS}
METRIC_BY_NAME.update(DASHBOARD_METRIC_BY_NAME)
METRIC_BY_KEY = {str(item["name"]).lower(): item for item in METRICS}
METRIC_ALIASES = {
    str(item["name"]): set(str(alias) for alias in item.get("aliases") or ())
    for item in METRICS
}
DIMENSION_ALIASES = {
    str(name): set(str(alias) for alias in config.get("aliases") or ())
    for name, config in DIMENSIONS.items()
}

# Enum-derived planner constants. No independent hard-coded copies should be
# needed for regions, channels, categories, member levels or refund reasons.
def _enum_values(key: str) -> tuple[str, ...]:
    return tuple(str(value) for value in (ENUMS.get(key) or {}).get("values") or ())


def _enum_aliases(key: str) -> dict[str, str]:
    config = ENUMS.get(key) or {}
    mapping: dict[str, str] = {}
    for canonical, aliases in (config.get("aliases") or {}).items():
        for alias in aliases or ():
            mapping[str(alias).lower()] = str(canonical)
    return mapping


ORDER_STATUSES = _enum_values("orders.status")
CHANNELS = _enum_values("orders.channel")
REGIONS = _enum_values("orders.region")
CATEGORIES = _enum_values("orders.category")
REFUND_REASONS = _enum_values("refunds.reason")
REFUND_STATUSES = _enum_values("refunds.status")
MEMBER_LEVELS = _enum_values("customers.member_level")
REGION_CITIES = dict((ENUMS.get("orders.region") or {}).get("cities") or {})

REGION_TERMS = _enum_aliases("orders.region")
CHANNEL_TERMS = _enum_aliases("orders.channel")
CATEGORY_TERMS = CATEGORIES
MEMBER_TERMS = MEMBER_LEVELS
ORDER_STATUS_TERMS = _enum_aliases("orders.status")
REFUND_STATUS_TERMS = _enum_aliases("refunds.status")

# Rendering aliases use human-facing canonical labels when an enum has a
# Chinese synonym (e.g. channel App stays App; 小程序 resolves to MiniProgram).
ENUM_ALIAS_DISPLAY = {
    key: {
        str(alias).lower(): str(canonical)
        for canonical, aliases in ((ENUMS.get(key) or {}).get("aliases") or {}).items()
        for alias in aliases or ()
    }
    for key in ENUMS
}


def enum_value(enum_key: str, text: str, default: str = "") -> str:
    aliases = ENUM_ALIAS_DISPLAY.get(str(enum_key), {})
    return aliases.get(str(text).strip().lower(), default)

INTENT_TERMS = {
    str(name): tuple(str(term) for term in config.get("terms") or ())
    for name, config in INTENTS.items()
}
REQUIRED_TABLES = {
    str(name): tuple(str(table) for table in config.get("required_tables") or ())
    for name, config in INTENTS.items()
}


def enum_prompt(key: str) -> str:
    return str((ENUMS.get(key) or {}).get("prompt") or "")


def metric_definition(metric_name: str) -> dict[str, Any] | None:
    raw = str(metric_name).strip().lower()
    for metric in METRICS:
        if str(metric["name"]).lower() == raw:
            return metric
        if raw in {str(alias).lower() for alias in metric.get("aliases") or ()}:
            return metric
    return None


def _slug(name: str) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def metric_from_text(text: str) -> dict[str, Any] | None:
    q = str(text or "").lower()
    # Longer aliases first avoids "销售" winning over "销售额".
    candidates = []
    for metric in METRICS:
        aliases = [str(metric["name"]), *[str(alias) for alias in metric.get("aliases") or ()]]
        for alias in aliases:
            folded = alias.lower()
            if folded and folded in q:
                candidates.append((len(folded), metric))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def metric_aliases(metric_name: str) -> set[str]:
    metric = METRIC_BY_NAME.get(str(metric_name))
    if metric is None:
        metric = next(
            (item for item in METRICS if str(item["name"]).lower() == str(metric_name).lower()),
            None,
        )
    return set(str(alias) for alias in (metric or {}).get("aliases") or ()) | {str(metric_name)}


def dimension_aliases(dimension: str) -> set[str]:
    return DIMENSION_ALIASES.get(str(dimension), {str(dimension)})


def _alias_map(aliases: dict[str, str] | None = None) -> dict[str, str]:
    return {
        "o": "o",
        "oi": "oi",
        "r": "r",
        "m": "m",
        **(aliases or {}),
    }


def metric_sql(
    metric_name: str,
    *,
    aliases: dict[str, str] | None = None,
    values: dict[str, str] | None = None,
) -> str:
    """Render a metric expression, or a formula template if values are absent."""
    metric = METRIC_BY_NAME[str(metric_name)]
    template = str(metric["expr"])
    replacements = {**_alias_map(aliases), **(values or {})}
    if values is None:
        # Templates intentionally retain named placeholders for callers that
        # compute a ratio from already-trusted aggregates.
        for key in re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", template):
            replacements.setdefault(key, "{" + key + "}")
    return template.format(**replacements)


def metric_filters(
    metric_name: str, *, aliases: dict[str, str] | None = None
) -> tuple[str, ...]:
    """Render a metric's predicate contract from the same YAML entry."""
    metric = METRIC_BY_NAME[str(metric_name)]
    alias_map = _alias_map(aliases)
    return tuple(str(item).format(**alias_map) for item in metric.get("filters") or ())


@lru_cache(maxsize=1)
def semantic_snapshot() -> dict[str, Any]:
    return {
        "version": VERSION,
        "path": SEMANTIC_PATH.relative_to(SEMANTIC_PATH.parents[1]).as_posix(),
        "dataset": dict(DATASET),
        "tables": [table.name for table in SCHEMA],
        "metrics": [
            {
                "name": metric["name"],
                "expr": metric.get("expr"),
                "filters": list(metric.get("filters") or ()),
                "tables": list(metric.get("tables") or ()),
                "grain": metric.get("grain"),
                "definition": metric.get("definition"),
            }
            for metric in METRICS
        ],
    }


def metric_prompt() -> str:
    """Render metric formulas and filters from the single source."""
    lines: list[str] = []
    for metric in METRICS:
        filters = ", ".join(str(item) for item in metric.get("filters") or ()) or "无额外过滤"
        lines.append(
            f"- {metric['name']}（{metric.get('grain', '')}；表 {', '.join(metric.get('tables') or ())}）："
            f"{metric.get('expr', '')}；过滤：{filters}；口径：{metric.get('definition', '')}"
        )
    return "\n".join(lines)


def create_table_sql(table_name: str) -> str:
    """Render the SQLite DDL for one table from the semantic catalog."""
    table = TABLE_BY_NAME[str(table_name)]
    config = TABLE_CONFIG[table.name]
    keys = [str(item) for item in config.get("key") or ()]
    lines = []
    for column in table.columns:
        line = f"  {column.name} {column.type} NOT NULL"
        if len(keys) == 1 and column.name == keys[0]:
            line += " PRIMARY KEY"
        lines.append(line)
    if len(keys) > 1:
        lines.append(f"  PRIMARY KEY ({', '.join(keys)})")
    return f"CREATE TABLE {table.name} (\n" + ",\n".join(lines) + "\n)"


def create_tables_sql(*, drop: bool = True) -> str:
    """Render the complete seeded SQLite schema from semantic.yaml."""
    existing = {table.name for table in SCHEMA}
    drop_order = [
        name for name in ("refunds", "order_items", "orders", "marketing_spend", "customers")
        if name in existing
    ]
    create_order = [
        name for name in ("customers", "orders", "order_items", "refunds", "marketing_spend")
        if name in existing
    ]
    statements = []
    if drop:
        statements.extend(f"DROP TABLE IF EXISTS {name};" for name in drop_order)
    statements.extend(create_table_sql(name) + ";" for name in create_order)
    return "\n\n".join(statements)


def output_metric_names() -> tuple[str, ...]:
    return tuple(str(metric["name"]) for metric in METRICS)
