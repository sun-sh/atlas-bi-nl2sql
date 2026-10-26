from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.nl2sql.models import QueryResult
from app.nl2sql.settings import get_settings


SESSION_TTL_DAYS = 7
MAX_TURNS_PER_SESSION = 20
DEFAULT_TITLE = "新的分析会话"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _db_path() -> Path:
    override = os.getenv("ATLAS_SESSION_DB_PATH")
    if override:
        return Path(override)
    return get_settings().data_dir / "sessions.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_session_store() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_turns_session
                ON turns(session_id, id);
            CREATE INDEX IF NOT EXISTS idx_sessions_updated
                ON sessions(updated_at DESC);
            """
        )


def _prune_expired(conn: sqlite3.Connection, now: datetime | None = None) -> None:
    current = _iso(now or _now())
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (current,))


def _derive_title(question: str) -> str:
    compact = " ".join(question.strip().split())
    if len(compact) > 24:
        return compact[:24] + "…"
    return compact or DEFAULT_TITLE


def create_session(title: str | None = None) -> dict[str, Any]:
    init_session_store()
    now = _now()
    session_id = "s_" + uuid.uuid4().hex[:12]
    normalized_title = (title or DEFAULT_TITLE).strip()[:40] or DEFAULT_TITLE
    with _connect() as conn:
        _prune_expired(conn, now)
        conn.execute(
            "INSERT INTO sessions(id, title, created_at, updated_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, normalized_title, _iso(now), _iso(now), _iso(now + timedelta(days=SESSION_TTL_DAYS))),
        )
    session = get_session(session_id)
    if session is None:
        raise RuntimeError("Failed to create session")
    return session


def list_sessions(limit: int = 30) -> list[dict[str, Any]]:
    init_session_store()
    with _connect() as conn:
        _prune_expired(conn)
        rows = conn.execute(
            """
            SELECT s.id, s.title, s.created_at, s.updated_at, s.expires_at,
                   COUNT(t.id) AS turn_count,
                   MAX(t.question) FILTER (WHERE t.id = (
                       SELECT MAX(t2.id) FROM turns t2 WHERE t2.session_id = s.id
                   )) AS last_question
            FROM sessions s
            LEFT JOIN turns t ON t.session_id = s.id
            GROUP BY s.id
            ORDER BY s.updated_at DESC
            LIMIT ?
            """,
            (max(1, min(limit, 100)),),
        ).fetchall()
    return [dict(row) for row in rows]


def get_session(session_id: str) -> dict[str, Any] | None:
    init_session_store()
    with _connect() as conn:
        _prune_expired(conn)
        session = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if session is None:
            return None
        turns = conn.execute(
            "SELECT id, question, answer, result_json, created_at FROM turns WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    payload_turns: list[dict[str, Any]] = []
    for row in turns:
        try:
            result = json.loads(row["result_json"])
        except json.JSONDecodeError:
            result = None
        payload_turns.append(
            {
                "id": row["id"],
                "question": row["question"],
                "answer": row["answer"],
                "created_at": row["created_at"],
                "result": result,
            }
        )
    latest_plan = {}
    if payload_turns:
        latest_result = payload_turns[-1].get("result") or {}
        latest_plan = dict(latest_result.get("plan") or {})
        latest_plan["previous_question"] = payload_turns[-1]["question"]
        latest_plan.update(dict(latest_plan.get("context") or {}))
    return {
        **dict(session),
        "turn_count": len(payload_turns),
        "turns": payload_turns,
        "context": latest_plan,
    }


def append_turn(session_id: str, question: str, result: QueryResult) -> dict[str, Any] | None:
    init_session_store()
    now = _now()
    payload = result.as_dict()
    with _connect() as conn:
        session = conn.execute("SELECT id, title FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if session is None:
            return None
        conn.execute(
            "INSERT INTO turns(session_id, question, answer, result_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, question, result.answer, json.dumps(payload, ensure_ascii=False), _iso(now)),
        )
        conn.execute(
            "UPDATE sessions SET updated_at = ?, expires_at = ? WHERE id = ?",
            (_iso(now), _iso(now + timedelta(days=SESSION_TTL_DAYS)), session_id),
        )
        if session["title"] == DEFAULT_TITLE:
            conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ?",
                (_derive_title(question), session_id),
            )
        conn.execute(
            """
            DELETE FROM turns
            WHERE session_id = ?
              AND id NOT IN (
                  SELECT id FROM turns WHERE session_id = ? ORDER BY id DESC LIMIT ?
              )
            """,
            (session_id, session_id, MAX_TURNS_PER_SESSION),
        )
    return get_session(session_id)


def delete_session(session_id: str) -> bool:
    init_session_store()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    return cursor.rowcount > 0
