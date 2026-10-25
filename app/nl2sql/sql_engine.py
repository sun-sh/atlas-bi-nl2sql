from __future__ import annotations

import re
import sqlite3
import time
from typing import Any

import sqlglot
from sqlglot import exp

from app.nl2sql.schema_catalog import allowed_tables
from app.nl2sql.settings import get_settings


FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|vacuum|replace|truncate|grant|revoke)\b", re.I)
SENSITIVE_COLUMNS = {"customer_id", "cost_price"}
ALLOWED_FUNCTIONS = {"sum", "avg", "count", "min", "max", "round", "coalesce", "ifnull", "cast", "substr", "strftime", "date", "datetime", "lower", "upper", "abs", "nullif"}
ALLOWED_OPERATORS = {"=", "!=", "<>", ">", "<", ">=", "<=", "+", "-", "*", "/", "AND", "OR", "NOT", "LIKE", "IN", "BETWEEN", "IS", "IS NOT"}


class SQLValidationError(ValueError):
    pass


def validate_sql(sql: str) -> str:
    text = sql.strip().rstrip(";").strip()
    if not text:
        raise SQLValidationError("SQL 为空")
    if ";" in text:
        raise SQLValidationError("只允许单条 SQL")
    if FORBIDDEN.search(text):
        raise SQLValidationError("检测到写操作或危险关键字")
    try:
        statements = sqlglot.parse(text, read="sqlite")
    except Exception as exc:
        raise SQLValidationError(f"SQL 语法无法解析：{exc}") from exc
    if len(statements) != 1:
        raise SQLValidationError("只允许单条 SQL")
    statement = statements[0]
    if not isinstance(statement, exp.Select):
        raise SQLValidationError("只允许 SELECT 查询")
    cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
    tables = {t.name.lower() for t in statement.find_all(exp.Table) if t.name}
    if not tables:
        raise SQLValidationError("只允许查询授权表")
    allowed = allowed_tables() | cte_names
    unknown = tables - allowed
    if unknown:
        raise SQLValidationError(f"查询了未授权表：{', '.join(sorted(unknown))}")
    if any(isinstance(item, exp.Star) or item.name == "*" for item in statement.expressions):
        raise SQLValidationError("禁止 SELECT *，请显式列出字段")
    for func in statement.find_all(exp.Func):
        if isinstance(func, (exp.And, exp.Or, exp.Not, exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.In, exp.Between, exp.Is)):
            continue
        name = (func.sql_name() or "").lower()
        if isinstance(func, exp.Anonymous):
            name = func.name.lower()
        if name and name not in ALLOWED_FUNCTIONS:
            raise SQLValidationError(f"未授权函数：{name}")
    for col in statement.find_all(exp.Column):
        if col.name.lower() in SENSITIVE_COLUMNS:
            raise SQLValidationError(f"敏感字段不可直接查询：{col.name}")
    return statement.sql(dialect="sqlite")


def execute_sql(sql: str, limit: int | None = None) -> dict[str, Any]:
    settings = get_settings()
    safe_sql = validate_sql(sql)
    row_limit = limit or settings.max_rows
    started = time.perf_counter()
    conn = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(safe_sql)
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description or ()]
    finally:
        conn.close()
    elapsed = (time.perf_counter() - started) * 1000
    data = [{k: r[k] for k in r.keys()} for r in rows[:row_limit]]
    return {
        "sql": safe_sql,
        "rows": data,
        "columns": columns,
        "row_count": len(rows),
        "truncated": len(rows) > row_limit,
        "elapsed_ms": round(elapsed, 2),
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
