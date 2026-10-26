from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.nl2sql import session_store
from app.nl2sql.models import QueryPlan, QueryResult


@pytest.fixture()
def isolated_sessions(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ATLAS_SESSION_DB_PATH", str(tmp_path / "sessions.db"))
    session_store.init_session_store()
    return tmp_path / "sessions.db"


def make_result(question: str, context: dict) -> QueryResult:
    plan = QueryPlan(
        intent="sales_overview",
        tables=["orders"],
        metrics=["销售额"],
        filters=["o.region = '华东'"],
        time_range="2026-01-01 至 2026-01-31",
        chart_type="bar",
        context=context,
    )
    return QueryResult(
        question=question,
        sql="SELECT 1 AS n",
        rows=[{"n": 1}],
        columns=["n"],
        chart_type="bar",
        answer="测试结果",
        plan=plan,
        retrieval=[],
        attempts=[{"attempt": 1, "status": "success"}],
        elapsed_ms=1.0,
        trace_id="trace-test",
    )


def test_session_persists_turns_and_context(isolated_sessions):
    session = session_store.create_session()
    context = {
        "time_filter": "o.order_date BETWEEN '2026-01-01' AND '2026-01-31'",
        "time_range": "2026-01-01 至 2026-01-31",
        "region_filter": "o.region = '华东'",
        "region_label": "华东",
        "metrics": ["销售额"],
    }
    assert [item["id"] for item in session_store.list_sessions()] == [session["id"]]
    updated = session_store.append_turn(
        session["id"],
        "上个月华东销售额",
        make_result("上个月华东销售额", context),
    )
    assert updated is not None
    assert updated["turn_count"] == 1
    assert updated["title"] == "上个月华东销售额"
    assert updated["turns"][0]["question"] == "上个月华东销售额"
    assert updated["context"]["region_label"] == "华东"
    restored = session_store.get_session(session["id"])
    assert restored is not None
    assert restored["turns"][0]["result"]["sql"] == "SELECT 1 AS n"


def test_turn_history_is_capped(isolated_sessions):
    session = session_store.create_session()
    for index in range(session_store.MAX_TURNS_PER_SESSION + 3):
        session_store.append_turn(
            session["id"],
            f"问题 {index}",
            make_result(f"问题 {index}", {}),
        )
    restored = session_store.get_session(session["id"])
    assert restored is not None
    assert restored["turn_count"] == session_store.MAX_TURNS_PER_SESSION
    assert restored["turns"][0]["question"] == "问题 3"


def test_delete_session_is_scoped_and_idempotent(isolated_sessions):
    first = session_store.create_session("会话一")
    second = session_store.create_session("会话二")
    assert session_store.delete_session(first["id"]) is True
    assert session_store.delete_session(first["id"]) is False
    assert session_store.get_session(first["id"]) is None
    assert session_store.get_session(second["id"]) is not None


def test_expired_session_is_pruned(isolated_sessions):
    session = session_store.create_session()
    import sqlite3
    with sqlite3.connect(isolated_sessions) as conn:
        conn.execute(
            "UPDATE sessions SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (session["id"],),
        )
    assert session_store.get_session(session["id"]) is None
