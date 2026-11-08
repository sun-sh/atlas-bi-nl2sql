from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.nl2sql.data_assets import TABLE_PROFILES
from app.nl2sql.schema_catalog import SCHEMA
from app.nl2sql.settings import get_settings
from app.nl2sql.sql_engine import QueryTarget


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKSPACE = "default"
ROLE_LEVEL = {"viewer": 1, "analyst": 2, "admin": 3}
SENSITIVE_KEYS = {"password", "secret", "token", "api_key", "authorization"}


class DataPlatformError(ValueError):
    pass


class PermissionDenied(DataPlatformError):
    pass


class DataSourceNotFound(DataPlatformError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _db_path() -> Path:
    override = os.getenv("ATLAS_DATA_PLATFORM_DB_PATH")
    if override:
        return Path(override)
    return get_settings().data_dir / "data_platform.db"


def _warehouse_path() -> Path:
    override = os.getenv("ATLAS_PLATFORM_WAREHOUSE_DB_PATH")
    if override:
        return Path(override)
    return get_settings().data_dir / "platform_warehouse.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _quote(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier or ""):
        raise DataPlatformError(f"非法标识符：{identifier}")
    return '"' + identifier.replace('"', '""') + '"'


def _slug(value: str, fallback: str = "source") -> str:
    value = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip()).strip("_").lower()
    if not value or value[0].isdigit():
        value = f"{fallback}_{uuid.uuid4().hex[:6]}"
    return value[:48]


def _require_role(role: str, minimum: str) -> None:
    if ROLE_LEVEL.get(role, 0) < ROLE_LEVEL[minimum]:
        raise PermissionDenied(f"当前角色 {role or 'unknown'} 无权执行该操作，至少需要 {minimum}")


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items() if key.lower() not in SENSITIVE_KEYS}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def audit(workspace_id: str, actor: str, role: str, action: str, resource_type: str, resource_id: str, payload: dict | None = None) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO platform_audit(id, workspace_id, actor, role, action, resource_type, resource_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_" + uuid.uuid4().hex[:12], workspace_id, actor[:80], role, action, resource_type, resource_id[:120], json.dumps(_sanitize(payload or {}), ensure_ascii=False), _iso()),
        )


def init_data_platform() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS platform_workspaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS data_sources (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                location TEXT NOT NULL,
                sync_mode TEXT NOT NULL DEFAULT 'snapshot',
                sensitivity TEXT NOT NULL DEFAULT 'internal',
                schedule_minutes INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'registered',
                last_sync_at TEXT,
                next_sync_at TEXT,
                row_count INTEGER NOT NULL DEFAULT 0,
                checksum TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                active_for_query INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(workspace_id) REFERENCES platform_workspaces(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS asset_snapshots (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                tables_json TEXT NOT NULL,
                quality_json TEXT NOT NULL,
                checksum TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(source_id) REFERENCES data_sources(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS lineage_edges (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                upstream_node TEXT NOT NULL,
                downstream_node TEXT NOT NULL,
                relation TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(source_id) REFERENCES data_sources(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS metric_definitions (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                name TEXT NOT NULL,
                grain TEXT NOT NULL DEFAULT '',
                definition TEXT NOT NULL,
                owner TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS quality_rules (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                name TEXT NOT NULL,
                rule_type TEXT NOT NULL,
                table_name TEXT NOT NULL,
                column_name TEXT NOT NULL DEFAULT '',
                expression TEXT NOT NULL DEFAULT '',
                severity TEXT NOT NULL DEFAULT 'error',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY(source_id) REFERENCES data_sources(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS quality_runs (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                status TEXT NOT NULL,
                passed INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                result_json TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                FOREIGN KEY(source_id) REFERENCES data_sources(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS platform_audit (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                actor TEXT NOT NULL,
                role TEXT NOT NULL,
                action TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sources_workspace ON data_sources(workspace_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_workspace ON platform_audit(workspace_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_quality_source ON quality_runs(source_id, started_at DESC);
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(data_sources)").fetchall()}
        if "active_for_query" not in columns:
            conn.execute(
                "ALTER TABLE data_sources ADD COLUMN active_for_query INTEGER NOT NULL DEFAULT 0"
            )
        if "dsn" not in columns:
            conn.execute(
                "ALTER TABLE data_sources ADD COLUMN dsn TEXT NOT NULL DEFAULT ''"
            )
        now = _iso()
        conn.execute(
            "INSERT OR IGNORE INTO platform_workspaces(id, name, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (DEFAULT_WORKSPACE, "默认数据工作区", "Atlas BI Demo 的项目级数据中台工作区", now, now),
        )


def _seed_default_sources(conn: sqlite3.Connection) -> None:
    exists = conn.execute("SELECT 1 FROM data_sources WHERE workspace_id = ? LIMIT 1", (DEFAULT_WORKSPACE,)).fetchone()
    if exists:
        return
    now = _iso()
    warehouse = get_settings().db_path
    conn.execute(
        "INSERT INTO data_sources(id, workspace_id, name, kind, location, sync_mode, sensitivity, schedule_minutes, status, active_for_query, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("ds_commerce", DEFAULT_WORKSPACE, "电商业务库", "sqlite", str(warehouse), "snapshot", "internal", 60, "registered", 1, now, now),
    )
    metric_rows = (
        ("销售额", "订单", "SUM(orders.paid_amount)，仅统计 paid 订单", "数据团队", "published"),
        ("客单价", "订单", "AVG(orders.paid_amount)，仅统计 paid 订单", "数据团队", "published"),
        ("退款率", "订单 / 品类", "退款申请数 / 全部订单数 × 100%", "数据团队", "published"),
        ("ROI", "渠道", "已支付销售额 / 投放花费，先分别聚合再计算", "增长团队", "published"),
        ("商品销量", "商品行", "SUM(order_items.quantity)，仅统计 paid 订单", "数据团队", "published"),
    )
    for name, grain, definition, owner, status in metric_rows:
        conn.execute(
            "INSERT INTO metric_definitions(id, workspace_id, name, grain, definition, owner, status, version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("m_" + uuid.uuid4().hex[:10], DEFAULT_WORKSPACE, name, grain, definition, owner, status, 1, now, now),
        )
    _seed_default_rules(conn, now)


def _seed_default_rules(conn: sqlite3.Connection, now: str) -> None:
    rules = (
        ("订单主键唯一", "unique", "orders", "order_id", "", "error"),
        ("订单实付金额非负", "non_negative", "orders", "paid_amount", "", "error"),
        ("退款金额非负", "non_negative", "refunds", "refund_amount", "", "error"),
        ("投放花费非负", "non_negative", "marketing_spend", "spend", "", "error"),
        ("商品行必须归属有效订单", "not_orphan", "order_items", "order_id", "orders.order_id", "error"),
        ("退款必须归属有效订单", "not_orphan", "refunds", "order_id", "orders.order_id", "error"),
    )
    for name, rule_type, table, column, expression, severity in rules:
        conn.execute(
            "INSERT INTO quality_rules(id, workspace_id, source_id, name, rule_type, table_name, column_name, expression, severity, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("qr_" + uuid.uuid4().hex[:10], DEFAULT_WORKSPACE, "ds_commerce", name, rule_type, table, column, expression, severity, 1, now),
        )


_bootstrapping_default_source = False


def ensure_data_platform() -> None:
    global _bootstrapping_default_source
    init_data_platform()
    with _connect() as conn:
        _seed_default_sources(conn)
    if _bootstrapping_default_source:
        return
    with _connect() as conn:
        needs_bootstrap = conn.execute(
            "SELECT 1 FROM data_sources s WHERE s.id = ? AND s.status = 'registered' "
            "AND NOT EXISTS (SELECT 1 FROM asset_snapshots snap WHERE snap.source_id = s.id) LIMIT 1",
            ("ds_commerce",),
        ).fetchone()
    if needs_bootstrap:
        _bootstrapping_default_source = True
        try:
            _sync_source("ds_commerce", actor="system", role="admin")
        except Exception:
            # A broken demo source must not make the control plane unreadable.
            pass
        finally:
            _bootstrapping_default_source = False


def list_workspaces() -> list[dict[str, Any]]:
    ensure_data_platform()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM platform_workspaces ORDER BY created_at").fetchall()
    return [dict(row) for row in rows]


def create_workspace(*, name: str, description: str = "", actor: str = "local", role: str = "admin") -> dict[str, Any]:
    ensure_data_platform()
    _require_role(role, "admin")
    name = " ".join(name.split())[:80]
    if len(name) < 2:
        raise DataPlatformError("工作区名称至少 2 个字符")
    workspace_id = "ws_" + uuid.uuid4().hex[:10]
    now = _iso()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO platform_workspaces(id, name, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (workspace_id, name, description.strip()[:240], now, now),
        )
    audit(workspace_id, actor, role, "workspace.create", "workspace", workspace_id, {"name": name})
    return next(item for item in list_workspaces() if item["id"] == workspace_id)


QUERY_SCHEMA_CONTRACT = "commerce-demo-v1"
QUERY_REQUIRED_SCHEMA = {
    table.name: tuple(column.name for column in table.columns) for table in SCHEMA
}
QUERY_INCOMPATIBLE_MESSAGE = "当前数据源缺少 NL2SQL 所需业务表，仅可用于数据服务/治理。"


def query_schema_compatibility(path: str | Path) -> dict[str, Any]:
    """Check the fixed semantic-layer contract used by retrieval and SQL planning."""
    target = Path(path)
    report: dict[str, Any] = {
        "compatible": False,
        "contract": QUERY_SCHEMA_CONTRACT,
        "required_tables": list(QUERY_REQUIRED_SCHEMA),
        "missing_tables": [],
        "missing_columns": {},
        "columns_checked": sum(len(columns) for columns in QUERY_REQUIRED_SCHEMA.values()),
    }
    if not target.exists():
        report["missing_tables"] = list(QUERY_REQUIRED_SCHEMA)
        report["error"] = "分析数据库文件不存在"
        return report
    try:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            existing_tables = {
                str(row[0]).lower(): str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            for table, required_columns in QUERY_REQUIRED_SCHEMA.items():
                physical_table = existing_tables.get(table.lower())
                if physical_table is None:
                    report["missing_tables"].append(table)
                    continue
                existing_columns = {
                    str(row[1]).lower()
                    for row in conn.execute(
                        f"PRAGMA table_info({_quote(physical_table)})"
                    ).fetchall()
                }
                missing = [
                    column for column in required_columns
                    if column.lower() not in existing_columns
                ]
                if missing:
                    report["missing_columns"][table] = missing
        finally:
            conn.close()
    except sqlite3.Error as exc:
        report["error"] = f"分析数据库无法读取：{exc}"
        return report

    report["compatible"] = not report["missing_tables"] and not report["missing_columns"]
    return report


def _compatibility_error(report: dict[str, Any]) -> str:
    details = []
    if report.get("missing_tables"):
        details.append("缺少表：" + "、".join(report["missing_tables"]))
    if report.get("missing_columns"):
        columns = "；".join(
            f"{table}({', '.join(columns)})"
            for table, columns in report["missing_columns"].items()
        )
        details.append("缺少字段：" + columns)
    if report.get("error"):
        details.append(str(report["error"]))
    suffix = "；" + "；".join(details) if details else ""
    return QUERY_INCOMPATIBLE_MESSAGE + suffix


def inspect_external_schema(dsn: str, kind: str) -> dict[str, Any]:
    """Inspect a read-only external DB-API target against the five-table contract."""
    report: dict[str, Any] = {
        "compatible": False,
        "contract": QUERY_SCHEMA_CONTRACT,
        "required_tables": list(QUERY_REQUIRED_SCHEMA),
        "missing_tables": [],
        "missing_columns": {},
        "columns_checked": sum(len(columns) for columns in QUERY_REQUIRED_SCHEMA.values()),
    }
    if not dsn:
        report["error"] = "外部数据库缺少只读 DSN"
        return report
    try:
        if kind == "postgresql":
            import psycopg  # type: ignore
            conn = psycopg.connect(dsn, autocommit=True)
        else:
            from app.nl2sql.sql_engine import DBAPIAdapter
            conn = DBAPIAdapter(dsn, "mysql")._connect()
    except ImportError as exc:
        report["error"] = f"{kind} 驱动未安装：{exc.name}"
        return report
    except Exception as exc:
        report["error"] = f"外部数据库连接失败：{str(exc)[:300]}"
        return report
    try:
        cursor = conn.cursor()
        try:
            if kind == "postgresql":
                for table, required in QUERY_REQUIRED_SCHEMA.items():
                    cursor.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name=%s",
                        (table,),
                    )
                    existing = {str(row[0]).lower() for row in cursor.fetchall()}
                    if not existing:
                        report["missing_tables"].append(table)
                        continue
                    missing = [column for column in required if column.lower() not in existing]
                    if missing:
                        report["missing_columns"][table] = missing
            else:
                for table, required in QUERY_REQUIRED_SCHEMA.items():
                    cursor.execute(
                        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s",
                        (table,),
                    )
                    existing = {str(row[0]).lower() for row in cursor.fetchall()}
                    if not existing:
                        report["missing_tables"].append(table)
                        continue
                    missing = [column for column in required if column.lower() not in existing]
                    if missing:
                        report["missing_columns"][table] = missing
        finally:
            cursor.close()
    except Exception as exc:
        report["error"] = f"外部数据库 Schema 检查失败：{str(exc)[:300]}"
        return report
    finally:
        conn.close()
    report["compatible"] = not report["missing_tables"] and not report["missing_columns"]
    return report


def _query_compatibility_for_source(source: dict[str, Any]) -> dict[str, Any]:
    if source["kind"] == "csv":
        report = query_schema_compatibility(_warehouse_path())
        report.update({
            "compatible": False,
            "error": "CSV 单表入库默认仅用于数据服务/治理，不能启用为 NL2SQL 分析库",
        })
        return report
    if source["kind"] in {"postgresql", "mysql"}:
        return inspect_external_schema(source.get("_dsn") or "", source["kind"])
    try:
        return query_schema_compatibility(_resolve_external_path(source["location"]))
    except DataPlatformError as exc:
        report = query_schema_compatibility(Path(source["location"]))
        report["compatible"] = False
        report["error"] = str(exc)
        return report


def _source_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "name": row["name"],
        "kind": row["kind"],
        "location": row["location"],
        "sync_mode": row["sync_mode"],
        "sensitivity": row["sensitivity"],
        "schedule_minutes": row["schedule_minutes"],
        "status": row["status"],
        "last_sync_at": row["last_sync_at"],
        "next_sync_at": row["next_sync_at"],
        "row_count": row["row_count"],
        "checksum": row["checksum"],
        "error": row["error"],
        "active_for_query": bool(row["active_for_query"]),
        "dsn_configured": bool(row["dsn"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def register_data_source(
    *, workspace_id: str, name: str, kind: str, location: str, sync_mode: str = "snapshot",
    sensitivity: str = "internal", schedule_minutes: int = 0, actor: str = "local", role: str = "admin",
    dsn: str = "",
) -> dict[str, Any]:
    ensure_data_platform()
    _require_role(role, "admin")
    kind = kind.lower().strip()
    if kind not in {"sqlite", "csv", "postgresql", "mysql"}:
        raise DataPlatformError("仅支持 sqlite、csv、postgresql 或 mysql 数据源")
    if sync_mode not in {"snapshot", "ingest"}:
        raise DataPlatformError("同步模式仅支持 snapshot 或 ingest")
    if schedule_minutes < 0 or schedule_minutes > 10080:
        raise DataPlatformError("调度周期需在 0–10080 分钟之间")
    if kind in {"sqlite", "csv"}:
        _resolve_external_path(location)
    elif not dsn.strip():
        raise DataPlatformError("PostgreSQL/MySQL 数据源必须提供只读 DSN")
    else:
        location = location.strip() or kind
    workspace_id = workspace_id or DEFAULT_WORKSPACE
    with _connect() as conn:
        if not conn.execute("SELECT 1 FROM platform_workspaces WHERE id = ?", (workspace_id,)).fetchone():
            raise DataPlatformError("工作区不存在")
        source_id = "ds_" + uuid.uuid4().hex[:10]
        now = _now()
        conn.execute(
            "INSERT INTO data_sources(id, workspace_id, name, kind, location, sync_mode, sensitivity, schedule_minutes, status, next_sync_at, dsn, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (source_id, workspace_id, name.strip()[:80] or "未命名数据源", kind, location.strip(), sync_mode, sensitivity, schedule_minutes, "registered", _iso(now + timedelta(minutes=schedule_minutes)) if schedule_minutes else None, dsn.strip(), _iso(now), _iso(now)),
        )
    audit(
        workspace_id,
        actor,
        role,
        "source.register",
        "data_source",
        source_id,
        {"kind": kind, "location": location, "dsn_configured": bool(dsn.strip())},
    )
    return get_data_source(source_id)


def get_data_source(source_id: str) -> dict[str, Any]:
    ensure_data_platform()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM data_sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise DataSourceNotFound("数据源不存在")
    return _source_row(row)


def list_data_sources(workspace_id: str = DEFAULT_WORKSPACE) -> list[dict[str, Any]]:
    ensure_data_platform()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM data_sources WHERE workspace_id = ? ORDER BY created_at", (workspace_id,)).fetchall()
    sources = []
    for row in rows:
        source = _source_row(row)
        if source["status"] == "ready":
            full_source = dict(source)
            full_source["_dsn"] = str(row["dsn"] or "")
            source["query_compatibility"] = _query_compatibility_for_source(full_source)
        sources.append(source)
    return sources


def _resolve_external_path(location: str) -> Path:
    path = Path(location)
    if not path.is_absolute():
        path = ROOT / path
    resolved = path.resolve()
    allowed_roots = [ROOT.resolve(), get_settings().data_dir.resolve(), _warehouse_path().parent.resolve()]
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        raise DataPlatformError("数据源路径必须位于项目或数据目录内")
    if not resolved.exists():
        raise DataPlatformError(f"数据源文件不存在：{location}")
    return resolved


def _copy_csv_to_warehouse(source: dict[str, Any], target: Path) -> dict[str, Any]:
    src = _resolve_external_path(source["location"])
    with src.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        try:
            headers = next(reader)
        except StopIteration as exc:
            raise DataPlatformError("CSV 为空") from exc
        rows = list(reader)
    if not headers or any(not h.strip() for h in headers):
        raise DataPlatformError("CSV 表头不能为空")
    if len(set(headers)) != len(headers):
        raise DataPlatformError("CSV 表头不能重复")
    table = _slug(source["name"], "csv_import")
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target) as conn:
        cols = ", ".join(_quote(h.strip()) for h in headers)
        conn.execute(f"DROP TABLE IF EXISTS {_quote(table)}")
        conn.execute(f"CREATE TABLE {_quote(table)} ({cols})")
        if rows:
            placeholders = ", ".join("?" for _ in headers)
            conn.executemany(f"INSERT INTO {_quote(table)} VALUES ({placeholders})", rows)
        conn.commit()
    return {"table": table, "rows": len(rows), "columns": [h.strip() for h in headers]}


def _inspect_sqlite(path: Path, source_name: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        names = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        tables, checks = [], []
        for table in names:
            rows = int(conn.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0])
            columns = []
            for col in conn.execute(f"PRAGMA table_info({_quote(table)})").fetchall():
                nulls = int(conn.execute(f"SELECT COUNT(*) FROM {_quote(table)} WHERE {_quote(col['name'])} IS NULL").fetchone()[0])
                columns.append({"name": col["name"], "type": col["type"] or "TEXT", "not_null": bool(col["notnull"]), "primary_key": bool(col["pk"]), "null_count": nulls})
            tables.append({"name": table, "row_count": rows, "columns": columns})
            checks.append({"name": f"{table}.has_rows", "passed": rows > 0, "value": rows})
            checks.append({"name": f"{table}.no_nulls", "passed": sum(c["null_count"] for c in columns) == 0, "value": sum(c["null_count"] for c in columns)})
        return tables, {"checks": checks, "passed": sum(1 for c in checks if c["passed"]), "failed": sum(1 for c in checks if not c["passed"])}
    finally:
        conn.close()


def _sync_source(source_id: str, actor: str = "local", role: str = "admin") -> dict[str, Any]:
    source = get_data_source(source_id)
    _require_role(role, "analyst")
    now = _now()
    try:
        target = _warehouse_path()
        if source["kind"] == "sqlite":
            src_path = _resolve_external_path(source["location"])
            tables, quality = _inspect_sqlite(src_path, source["name"])
            checksum = (
                "demo"
                if source["location"].endswith("demo.db")
                else f"{src_path.stat().st_size}:{src_path.stat().st_mtime_ns}"
            )
        elif source["kind"] in {"postgresql", "mysql"}:
            # External databases are registered and activated behind a
            # read-only DSN; this lightweight control plane does not pull their
            # full catalog into the local SQLite metadata store yet.
            tables = []
            quality = {
                "checks": [{"name": "external_dsn_configured", "passed": True, "value": 1}],
                "passed": 1,
                "failed": 0,
            }
            checksum = f"{source['kind']}:dsn"
        else:
            info = _copy_csv_to_warehouse(source, target)
            tables = [{"name": info["table"], "row_count": info["rows"], "columns": [{"name": c, "type": "TEXT", "not_null": False, "primary_key": False, "null_count": 0} for c in info["columns"]]}]
            quality = {"checks": [{"name": f"{info['table']}.has_rows", "passed": info["rows"] > 0, "value": info["rows"]}], "passed": int(info["rows"] > 0), "failed": int(info["rows"] <= 0)}
            checksum = str(_resolve_external_path(source["location"]).stat().st_mtime_ns)
        row_count = sum(t["row_count"] for t in tables)
        snapshot_id = "snap_" + uuid.uuid4().hex[:12]
        with _connect() as conn:
            conn.execute(
                "INSERT INTO asset_snapshots(id, workspace_id, source_id, synced_at, tables_json, quality_json, checksum) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (snapshot_id, source["workspace_id"], source_id, _iso(now), json.dumps(tables, ensure_ascii=False), json.dumps(quality, ensure_ascii=False), checksum),
            )
            conn.execute(
                "UPDATE data_sources SET status='ready', last_sync_at=?, next_sync_at=?, row_count=?, checksum=?, error='', updated_at=? WHERE id=?",
                (_iso(now), _iso(now + timedelta(minutes=source["schedule_minutes"])) if source["schedule_minutes"] else None, row_count, checksum, _iso(now), source_id),
            )
            conn.execute("DELETE FROM lineage_edges WHERE source_id=? AND upstream_node LIKE 'source:%'", (source_id,))
            for t in tables:
                for c in t["columns"]:
                    conn.execute(
                        "INSERT INTO lineage_edges(id, workspace_id, source_id, upstream_node, downstream_node, relation, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        ("lin_" + uuid.uuid4().hex[:10], source["workspace_id"], source_id, f"{source['name']}.{t['name']}.{c['name']}", f"asset.{t['name']}.{c['name']}", "column", _iso(now)),
                    )
        audit(source["workspace_id"], actor, role, "source.sync", "data_source", source_id, {"snapshot_id": snapshot_id, "rows": row_count})
        return {"snapshot_id": snapshot_id, "tables": tables, "quality": quality, "row_count": row_count, "synced_at": _iso(now)}
    except Exception as exc:
        with _connect() as conn:
            conn.execute("UPDATE data_sources SET status='error', error=?, updated_at=? WHERE id=?", (str(exc)[:500], _iso(now), source_id))
        raise


def sync_data_source(source_id: str, *, actor: str = "local", role: str = "admin") -> dict[str, Any]:
    ensure_data_platform()
    return _sync_source(source_id, actor=actor, role=role)



def activate_query_source(
    source_id: str,
    *,
    actor: str = "local",
    role: str = "admin",
) -> dict[str, Any]:
    """Select one synced source as the default read-only NL2SQL database."""
    ensure_data_platform()
    _require_role(role, "admin")
    source = get_data_source(source_id)
    if source["status"] != "ready":
        raise DataPlatformError("数据源尚未成功同步，不能启用为分析数据源")
    if source["kind"] == "csv":
        raise DataPlatformError(
            "CSV 数据源默认仅用于数据服务/治理，不能启用为 NL2SQL 分析库；"
            "请接入包含固定五表契约的 SQLite 或显式配置的 PostgreSQL/MySQL。"
        )
    if source["kind"] in {"postgresql", "mysql"}:
        full_source = _source_with_secret(source_id)
        compatibility = inspect_external_schema(
            full_source.get("_dsn") or "",
            source["kind"],
        )
        if not compatibility["compatible"]:
            raise DataPlatformError(_compatibility_error(compatibility))
        database_name = source["kind"]
    else:
        path = _resolve_external_path(source["location"])
        compatibility = query_schema_compatibility(path)
        if not compatibility["compatible"]:
            raise DataPlatformError(_compatibility_error(compatibility))
        database_name = path.name
    with _connect() as conn:
        conn.execute(
            "UPDATE data_sources SET active_for_query=0, updated_at=? WHERE workspace_id=?",
            (_iso(), source["workspace_id"]),
        )
        conn.execute(
            "UPDATE data_sources SET active_for_query=1, updated_at=? WHERE id=?",
            (_iso(), source_id),
        )
    audit(
        source["workspace_id"],
        actor,
        role,
        "source.activate",
        "data_source",
        source_id,
        {"database": database_name},
    )
    return {
        "source": get_data_source(source_id),
        "database": database_name,
        "read_only": True,
        "compatibility": compatibility,
    }


def query_target_for_source(source: dict[str, Any]) -> QueryTarget:
    if source["kind"] == "sqlite":
        return QueryTarget(
            "sqlite",
            "sqlite",
            path=_resolve_external_path(source["location"]),
        )
    if source["kind"] == "postgresql":
        return QueryTarget("postgresql", "postgres", dsn=source.get("_dsn") or "")
    if source["kind"] == "mysql":
        return QueryTarget("mysql", "mysql", dsn=source.get("_dsn") or "")
    raise DataPlatformError("CSV 单表来源不能作为 NL2SQL 查询目标")


def _source_with_secret(source_id: str) -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM data_sources WHERE id=?", (source_id,)).fetchone()
    if row is None:
        raise DataSourceNotFound("数据源不存在")
    value = _source_row(row)
    value["_dsn"] = str(row["dsn"] or "")
    return value


def active_query_database(workspace_id: str = DEFAULT_WORKSPACE) -> dict[str, Any]:
    """Resolve the active query database, falling back to the demo warehouse."""
    ensure_data_platform()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM data_sources WHERE workspace_id=? AND active_for_query=1 "
            "AND status='ready' ORDER BY updated_at DESC LIMIT 1",
            (workspace_id,),
        ).fetchone()
    if row is None:
        path = get_settings().db_path
        return {
            "source_id": None,
            "workspace_id": workspace_id,
            "database": path.name,
            "path": path,
            "target": QueryTarget("sqlite", "sqlite", path=path),
            "read_only": True,
            "fallback": True,
        }
    source = _source_row(row)
    source["_dsn"] = str(row["dsn"] or "")
    target = query_target_for_source(source)
    return {
        "source_id": source["id"],
        "workspace_id": source["workspace_id"],
        "database": target.path.name if target.path else source["kind"],
        "path": target.path,
        "target": target,
        "read_only": True,
        "fallback": False,
    }


def list_lineage(workspace_id: str = DEFAULT_WORKSPACE) -> list[dict[str, Any]]:
    ensure_data_platform()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM lineage_edges WHERE workspace_id=? ORDER BY downstream_node, upstream_node", (workspace_id,)).fetchall()
    return [dict(row) for row in rows]


def list_metrics(workspace_id: str = DEFAULT_WORKSPACE) -> list[dict[str, Any]]:
    ensure_data_platform()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM metric_definitions WHERE workspace_id=? ORDER BY name", (workspace_id,)).fetchall()
    return [dict(row) for row in rows]


def create_metric(*, workspace_id: str, name: str, grain: str, definition: str, owner: str = "", status: str = "draft", actor: str = "local", role: str = "admin") -> dict[str, Any]:
    ensure_data_platform()
    _require_role(role, "analyst")
    name = name.strip()[:60]
    definition = definition.strip()[:500]
    if len(name) < 2 or not definition:
        raise DataPlatformError("指标名称和口径不能为空")
    metric_id = "m_" + uuid.uuid4().hex[:10]
    now = _iso()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO metric_definitions(id, workspace_id, name, grain, definition, owner, status, version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (metric_id, workspace_id, name, grain.strip()[:60], definition, owner.strip()[:60], status if status in {"draft", "published", "deprecated"} else "draft", 1, now, now),
        )
    audit(workspace_id, actor, role, "metric.create", "metric", metric_id, {"name": name})
    return next(m for m in list_metrics(workspace_id) if m["id"] == metric_id)


def list_quality_rules(workspace_id: str = DEFAULT_WORKSPACE) -> list[dict[str, Any]]:
    ensure_data_platform()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM quality_rules WHERE workspace_id=? ORDER BY created_at", (workspace_id,)).fetchall()
    return [dict(row) for row in rows]


def create_quality_rule(*, workspace_id: str, source_id: str, name: str, rule_type: str, table_name: str, column_name: str = "", expression: str = "", severity: str = "error", actor: str = "local", role: str = "admin") -> dict[str, Any]:
    ensure_data_platform()
    _require_role(role, "analyst")
    if rule_type not in {"unique", "non_negative", "not_null", "not_orphan", "row_min"}:
        raise DataPlatformError("不支持的质量规则类型")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name) or (column_name and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column_name)):
        raise DataPlatformError("表名或字段名非法")
    with _connect() as conn:
        source = conn.execute("SELECT 1 FROM data_sources WHERE id=? AND workspace_id=?", (source_id, workspace_id)).fetchone()
        if not source:
            raise DataSourceNotFound("数据源不存在")
        rule_id = "qr_" + uuid.uuid4().hex[:10]
        conn.execute(
            "INSERT INTO quality_rules(id, workspace_id, source_id, name, rule_type, table_name, column_name, expression, severity, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (rule_id, workspace_id, source_id, name.strip()[:80] or rule_type, rule_type, table_name, column_name, expression.strip()[:200], severity, _iso()),
        )
    audit(workspace_id, actor, role, "quality_rule.create", "quality_rule", rule_id, {"type": rule_type, "table": table_name})
    return next(r for r in list_quality_rules(workspace_id) if r["id"] == rule_id)


def _quality_value(conn: sqlite3.Connection, rule: dict[str, Any]) -> int:
    table = rule["table_name"]
    column = rule["column_name"]
    kind = rule["rule_type"]
    if kind == "row_min":
        return int(conn.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0])
    if kind == "unique":
        return int(conn.execute(f"SELECT COUNT(*) FROM (SELECT COUNT(*) FROM {_quote(table)} GROUP BY {_quote(column)} HAVING COUNT(*)>1)").fetchone()[0])
    if kind == "non_negative":
        return int(conn.execute(f"SELECT COUNT(*) FROM {_quote(table)} WHERE {_quote(column)} < 0").fetchone()[0])
    if kind == "not_null":
        return int(conn.execute(f"SELECT COUNT(*) FROM {_quote(table)} WHERE {_quote(column)} IS NULL").fetchone()[0])
    if kind == "not_orphan":
        if "." not in rule["expression"]:
            raise DataPlatformError("not_orphan 规则需要 target_table.target_column")
        target_table, target_column = rule["expression"].split(".", 1)
        return int(conn.execute(
            f"SELECT COUNT(*) FROM {_quote(table)} s LEFT JOIN {_quote(target_table)} t ON t.{_quote(target_column)}=s.{_quote(column)} WHERE s.{_quote(column)} IS NOT NULL AND t.{_quote(target_column)} IS NULL"
        ).fetchone()[0])
    raise DataPlatformError("未知质量规则")


def _run_quality(source: dict[str, Any], rules: list[dict[str, Any]]) -> dict[str, Any]:
    path = _resolve_external_path(source["location"])
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        results = []
        for rule in rules:
            value = _quality_value(conn, rule)
            passed = value == 0 if rule["rule_type"] in {"unique", "non_negative", "not_null", "not_orphan"} else value >= int(rule["expression"] or "1")
            results.append({"id": rule["id"], "name": rule["name"], "severity": rule["severity"], "value": value, "passed": passed})
        return {"checks": results, "passed": sum(r["passed"] for r in results), "failed": sum(not r["passed"] for r in results)}
    finally:
        conn.close()


def run_quality(workspace_id: str, source_id: str, *, actor: str = "local", role: str = "analyst") -> dict[str, Any]:
    ensure_data_platform()
    _require_role(role, "analyst")
    source = get_data_source(source_id)
    with _connect() as conn:
        rules = [dict(r) for r in conn.execute("SELECT * FROM quality_rules WHERE workspace_id=? AND source_id=? AND enabled=1 ORDER BY created_at", (workspace_id, source_id)).fetchall()]
    result = _run_quality(source, rules)
    run_id = "qrun_" + uuid.uuid4().hex[:12]
    now = _iso()
    with _connect() as conn:
        conn.execute("INSERT INTO quality_runs(id, workspace_id, source_id, status, passed, failed, result_json, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (run_id, workspace_id, source_id, "completed", result["passed"], result["failed"], json.dumps(result, ensure_ascii=False), now, now))
    audit(workspace_id, actor, role, "quality.run", "quality_run", run_id, {"source_id": source_id, "passed": result["passed"], "failed": result["failed"]})
    return {"id": run_id, "source_id": source_id, "ran_at": now, **result}


def list_quality_runs(workspace_id: str = DEFAULT_WORKSPACE, limit: int = 20) -> list[dict[str, Any]]:
    ensure_data_platform()
    with _connect() as conn:
        rows = conn.execute("SELECT id, workspace_id, source_id, status, passed, failed, started_at, finished_at FROM quality_runs WHERE workspace_id=? ORDER BY started_at DESC LIMIT ?", (workspace_id, min(max(limit, 1), 100))).fetchall()
    return [dict(row) for row in rows]


def platform_overview(workspace_id: str = DEFAULT_WORKSPACE) -> dict[str, Any]:
    ensure_data_platform()
    sources = list_data_sources(workspace_id)
    rules = list_quality_rules(workspace_id)
    metrics = list_metrics(workspace_id)
    runs = list_quality_runs(workspace_id, 5)
    lineage = list_lineage(workspace_id)
    with _connect() as conn:
        audit_rows = conn.execute("SELECT * FROM platform_audit WHERE workspace_id=? ORDER BY created_at DESC LIMIT 20", (workspace_id,)).fetchall()
        schedules = conn.execute("SELECT id, name, next_sync_at FROM data_sources WHERE workspace_id=? AND schedule_minutes>0 ORDER BY next_sync_at", (workspace_id,)).fetchall()
    return {
        "workspace": {"id": workspace_id, "name": next((w["name"] for w in list_workspaces() if w["id"] == workspace_id), workspace_id)},
        "summary": {
            "source_count": len(sources), "ready_sources": sum(s["status"] == "ready" for s in sources),
            "query_ready_sources": sum(
                bool(s.get("query_compatibility", {}).get("compatible")) for s in sources
            ),
            "rule_count": len(rules), "metric_count": len(metrics), "published_metrics": sum(m["status"] == "published" for m in metrics),
            "lineage_edges": len(lineage),
            "last_quality": runs[0] if runs else None,
        },
        "sources": sources,
        "rules": rules,
        "metrics": metrics,
        "quality_runs": runs,
        "lineage": lineage,
        "schedules": [dict(row) for row in schedules],
        "audit": [_audit_row(row) for row in audit_rows],
        "controls": {
            "default_role": "admin",
            "roles": list(ROLE_LEVEL),
            "data_service": "read-only",
            "query_database": active_query_database(workspace_id)["database"],
        },
    }


def _audit_row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        payload = json.loads(row["payload_json"])
    except json.JSONDecodeError:
        payload = {}
    return {"id": row["id"], "workspace_id": row["workspace_id"], "actor": row["actor"], "role": row["role"], "action": row["action"], "resource_type": row["resource_type"], "resource_id": row["resource_id"], "payload": payload, "created_at": row["created_at"]}


def data_service_query(workspace_id: str, source_id: str, table: str, *, limit: int = 100, actor: str = "local", role: str = "viewer") -> dict[str, Any]:
    ensure_data_platform()
    _require_role(role, "viewer")
    source = get_data_source(source_id)
    if source["workspace_id"] != workspace_id:
        raise DataPlatformError("数据源不属于该工作区")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
        raise DataPlatformError("表名非法")
    path = _warehouse_path() if source["kind"] == "csv" else _resolve_external_path(source["location"])
    if not path.exists():
        raise DataPlatformError("数据源尚未同步")
    limit = min(max(int(limit), 1), 500)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table,),
        ).fetchone()
        if table_exists is None:
            raise DataPlatformError("数据服务表不存在")
        # The data service is read-only but still a presentation surface:
        # never expose columns that the NL2SQL semantic layer marks restricted.
        restricted = {
            column.name.lower()
            for catalog_table in SCHEMA
            for column in catalog_table.columns
            if column.sensitivity != "public"
        }
        physical_columns = [
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
        ]
        columns = [column for column in physical_columns if column.lower() not in restricted]
        if not columns:
            raise DataPlatformError("该表没有可展示的公开字段")
        projection = ", ".join(_quote(column) for column in columns)
        cur = conn.execute(f"SELECT {projection} FROM {_quote(table)} LIMIT ?", (limit,))
        rows = [dict(zip(columns, row)) for row in cur.fetchmany(limit)]
    finally:
        conn.close()
    audit(workspace_id, actor, role, "data_service.query", "data_source", source_id, {"table": table, "limit": limit, "returned": len(rows)})
    return {"workspace_id": workspace_id, "source_id": source_id, "table": table, "columns": columns, "rows": rows, "row_count": len(rows), "limit": limit}


def dispatch_due_syncs(*, role: str = "admin", actor: str = "scheduler") -> dict[str, Any]:
    ensure_data_platform()
    _require_role(role, "admin")
    now = _now()
    with _connect() as conn:
        rows = conn.execute("SELECT id FROM data_sources WHERE schedule_minutes>0 AND (next_sync_at IS NULL OR next_sync_at<=?) ORDER BY created_at", (_iso(now),)).fetchall()
    results = []
    for row in rows:
        try:
            results.append({"source_id": row["id"], "status": "synced", "result": _sync_source(row["id"], actor=actor, role=role)})
        except Exception as exc:
            results.append({"source_id": row["id"], "status": "error", "error": str(exc)[:300]})
    return {"dispatched_at": _iso(now), "count": len(results), "results": results}
