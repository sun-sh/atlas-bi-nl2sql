from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

import sqlglot
from sqlglot import exp

from app.nl2sql.harness import HarnessPolicyError, is_cancelled, validate_policy
from app.nl2sql.schema_catalog import allowed_tables
from app.nl2sql.settings import get_settings


FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|vacuum|replace|truncate|grant|revoke)\b", re.I)
SENSITIVE_COLUMNS = {"customer_id", "cost_price"}
ALLOWED_FUNCTIONS = {
    "sum", "avg", "count", "min", "max", "round", "coalesce", "ifnull",
    "cast", "substr", "strftime", "date", "datetime", "lower", "upper",
    "abs", "nullif",
    # CASE is a SQL expression, not a callable, but sqlglot exposes it as a
    # function node. Blocking it rejects legitimate conditional aggregation.
    "case",
    # Window functions are read-only ranking primitives and are useful for
    # top-N questions; they still pass the same SELECT/AST policy.
    "row_number", "rank", "dense_rank", "lag", "lead", "ntile",
}

SQL_DIALECTS = {"sqlite": "sqlite", "postgresql": "postgres", "mysql": "mysql"}


class SQLValidationError(ValueError):
    pass


class QueryCancelled(RuntimeError):
    pass


class QueryAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class QueryTarget:
    """A read-only database target resolved by the data platform."""

    kind: str
    dialect: str
    path: Path | None = None
    dsn: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "dialect": self.dialect,
            "database": self.path.name if self.path else "",
            "dsn_configured": bool(self.dsn),
        }


class QueryAdapter(Protocol):
    dialect: str

    def execute(
        self,
        sql: str,
        *,
        limit: int,
        timeout_seconds: float,
        cancel_trace_id: str | None = None,
    ) -> dict[str, Any]:
        ...


class SQLiteAdapter:
    dialect = "sqlite"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def execute(
        self,
        sql: str,
        *,
        limit: int,
        timeout_seconds: float,
        cancel_trace_id: str | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        deadline = time.monotonic() + timeout_seconds

        def progress_handler() -> int:
            return 1 if time.monotonic() >= deadline or is_cancelled(cancel_trace_id) else 0

        conn.set_progress_handler(progress_handler, 1000)
        try:
            cur = conn.execute(sql)
            columns = [d[0] for d in cur.description or ()]
            rows = cur.fetchmany(limit + 1)
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc).lower():
                if is_cancelled(cancel_trace_id):
                    raise QueryCancelled("查询已取消") from exc
                raise TimeoutError(f"查询超过 {timeout_seconds:g} 秒执行上限") from exc
            raise
        finally:
            conn.close()
        data = [{k: r[k] for k in r.keys()} for r in rows[:limit]]
        return {
            "rows": data,
            "columns": columns,
            "row_count": min(len(rows), limit),
            "truncated": len(rows) > limit,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        }


class DBAPIAdapter:
    """Read-only DB-API adapter used by opt-in PostgreSQL/MySQL targets.

    Import is intentionally deferred so the core demo does not require either
    driver. The adapter enforces a read-only SQL prefix and bounded fetches;
    server-side timeout and statement limits must also be configured by the DBA.
    """

    def __init__(self, dsn: str, dialect: str):
        self.dsn = dsn
        self.dialect = dialect

    def _safe_error(self, exc: Exception) -> str:
        """Remove credentials if a driver echoes the DSN in an exception."""
        message = str(exc).strip() or type(exc).__name__
        message = message.replace(self.dsn, "[REDACTED]")
        parsed = urlparse(self.dsn)
        if parsed.password:
            message = message.replace(parsed.password, "[REDACTED]")
            message = message.replace(unquote(parsed.password), "[REDACTED]")
        return message[:500]


    def _connect(self):
        if self.dialect == "postgres":
            try:
                import psycopg
            except ImportError as exc:
                raise QueryAdapterError("PostgreSQL adapter requires psycopg") from exc
            return psycopg.connect(self.dsn, autocommit=True)
        if self.dialect == "mysql":
            try:
                import pymysql
            except ImportError as exc:
                raise QueryAdapterError("MySQL adapter requires pymysql") from exc
            parsed = urlparse(self.dsn)
            if parsed.scheme not in {"mysql", "mysql+pymysql"}:
                raise QueryAdapterError("MySQL DSN must start with mysql://")
            return pymysql.connect(
                host=parsed.hostname or "127.0.0.1",
                port=parsed.port or 3306,
                user=unquote(parsed.username or ""),
                password=unquote(parsed.password or ""),
                database=(parsed.path or "/").lstrip("/"),
                connect_timeout=5,
                read_timeout=30,
                write_timeout=30,
                autocommit=True,
            )
        raise QueryAdapterError(f"unsupported adapter dialect: {self.dialect}")

    def execute(
        self,
        sql: str,
        *,
        limit: int,
        timeout_seconds: float,
        cancel_trace_id: str | None = None,
    ) -> dict[str, Any]:
        if cancel_trace_id and is_cancelled(cancel_trace_id):
            raise QueryCancelled("查询已取消")
        started = time.perf_counter()
        try:
            conn = self._connect()
        except Exception as exc:
            raise QueryAdapterError(
                f"{self.dialect} 数据库连接失败：{self._safe_error(exc)}"
            ) from exc
        try:
            try:
                cursor = conn.cursor()
            except Exception as exc:
                raise QueryAdapterError(
                    f"{self.dialect} 数据库游标创建失败：{self._safe_error(exc)}"
                ) from exc
            try:
                try:
                    timeout_ms = max(1, int(timeout_seconds * 1000))
                    if self.dialect == "postgres":
                        # autocommit=True means SET LOCAL would only live for
                        # this statement. The connection is per query, so a
                        # session-level setting is scoped by connection lifetime.
                        cursor.execute("SET statement_timeout = %s", (timeout_ms,))
                    elif self.dialect == "mysql":
                        cursor.execute("SET SESSION MAX_EXECUTION_TIME = %s", (timeout_ms,))
                    cursor.execute(sql)
                except Exception as exc:
                    message = self._safe_error(exc)
                    if "timeout" in message.lower() or "canceling statement" in message.lower():
                        raise TimeoutError(
                            f"{self.dialect} 查询超过 {timeout_seconds:g} 秒执行上限"
                        ) from exc
                    raise QueryAdapterError(
                        f"{self.dialect} 查询执行失败：{message}"
                    ) from exc
                columns = [item[0] for item in (cursor.description or ())]
                rows = cursor.fetchmany(limit + 1)
            finally:
                cursor.close()
        finally:
            conn.close()
        data = [dict(zip(columns, row)) for row in rows[:limit]]
        return {
            "rows": data,
            "columns": columns,
            "row_count": min(len(rows), limit),
            "truncated": len(rows) > limit,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        }


def adapter_for(target: QueryTarget) -> QueryAdapter:
    if target.kind == "sqlite":
        if target.path is None:
            raise QueryAdapterError("SQLite target requires a path")
        return SQLiteAdapter(target.path)
    if target.kind in {"postgresql", "mysql"}:
        if not target.dsn:
            raise QueryAdapterError(f"{target.kind} target requires a DSN")
        return DBAPIAdapter(target.dsn, target.dialect)
    raise QueryAdapterError(f"unsupported query target: {target.kind}")


def validate_sql(sql: str, *, dialect: str = "sqlite") -> str:
    text = sql.strip().rstrip(";").strip()
    if not text:
        raise SQLValidationError("SQL 为空")
    if ";" in text:
        raise SQLValidationError("只允许单条 SQL")
    if FORBIDDEN.search(text):
        raise SQLValidationError("检测到写操作或危险关键字")
    parse_dialect = SQL_DIALECTS.get(dialect, dialect)
    try:
        statements = sqlglot.parse(text, read=parse_dialect)
    except Exception as exc:
        raise SQLValidationError(f"SQL 语法无法解析：{exc}") from exc
    if len(statements) != 1:
        raise SQLValidationError("只允许单条 SQL")
    statement = statements[0]
    if not isinstance(statement, exp.Select):
        raise SQLValidationError("只允许 SELECT 查询")
    cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
    table_nodes = [t for t in statement.find_all(exp.Table) if t.name]
    tables = {t.name.lower() for t in table_nodes}
    if not tables:
        raise SQLValidationError("只允许查询授权表")
    allowed = allowed_tables() | cte_names
    unknown = tables - allowed
    if unknown:
        raise SQLValidationError(f"查询了未授权表：{', '.join(sorted(unknown))}")
    # sqlglot exposes `orders` for both `orders` and `other_schema.orders`.
    # Treat an explicit non-default schema/database as an unapproved table so
    # the allowlist cannot be bypassed through a same-name relation.
    for table in table_nodes:
        qualifier = table.args.get("db") or table.args.get("catalog")
        if qualifier and table.name.lower() not in cte_names:
            raise SQLValidationError(
                f"不允许跨 Schema 访问表：{qualifier}.{table.name}"
            )
    if any(isinstance(item, exp.Star) or item.name == "*" for item in statement.expressions):
        raise SQLValidationError("禁止 SELECT *，请显式列出字段")
    for func in statement.find_all(exp.Func):
        # sqlglot represents boolean operators and CASE/IF branches as Func
        # subclasses. They are expression syntax, not callable allowlist entries:
        # treating CASE's nested IF as an unknown function made every legitimate
        # conditional aggregation fail validation.
        if isinstance(func, (
            exp.And, exp.Or, exp.Not, exp.EQ, exp.NEQ, exp.GT, exp.GTE,
            exp.LT, exp.LTE, exp.Like, exp.In, exp.Between, exp.Is,
            exp.Case, exp.If,
        )):
            continue
        name = (func.sql_name() or "").lower()
        if isinstance(func, exp.Anonymous):
            name = func.name.lower()
        if name and name not in ALLOWED_FUNCTIONS:
            raise SQLValidationError(f"未授权函数：{name}")
    for col in statement.find_all(exp.Column):
        if col.name.lower() in SENSITIVE_COLUMNS:
            raise SQLValidationError(f"敏感字段不可直接查询：{col.name}")
    try:
        validate_policy(text, parsed=statement, dialect=dialect)
    except HarnessPolicyError as exc:
        raise SQLValidationError(str(exc)) from exc
    return statement.sql(dialect=parse_dialect)


def execute_sql(
    sql: str,
    limit: int | None = None,
    cancel_trace_id: str | None = None,
    db_path: str | Path | None = None,
    *,
    target: QueryTarget | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    row_limit = limit or settings.max_rows
    resolved = target or QueryTarget("sqlite", "sqlite", Path(db_path) if db_path else settings.db_path)
    safe_sql = validate_sql(sql, dialect=resolved.dialect)
    result = adapter_for(resolved).execute(
        safe_sql,
        limit=row_limit,
        timeout_seconds=settings.query_timeout_seconds,
        cancel_trace_id=cancel_trace_id,
    )
    return {
        "sql": safe_sql,
        **result,
        "policy_version": "nl2sql-harness-v1",
        "query_timeout_seconds": settings.query_timeout_seconds,
        "adapter": resolved.as_dict(),
    }


def explain_error(error: Exception, sql: str) -> str:
    msg = str(error)
    if "no such column" in msg:
        match = re.search(r"no such column:\s*([\w.]+)", msg, re.I)
        return f"列不存在：{match.group(1) if match else msg}。请只使用 Schema 中已给出的列。"
    if "no such table" in msg:
        match = re.search(r"no such table:\s*([\w.]+)", msg, re.I)
        return f"表不存在：{match.group(1) if match else msg}。请只使用授权表。"
    if "syntax error" in msg:
        return f"SQL 语法错误：{msg}"
    if "ambiguous column" in msg:
        return f"字段有歧义，需要加表别名：{msg}"
    return msg
