from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.nl2sql.evaluation import run_evaluation
from app.nl2sql.model_config import get_model_config, model_config_snapshot
from app.nl2sql.settings import get_settings


ROOT = Path(__file__).resolve().parents[2]
ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
SENSITIVE_KEYS = {"api_key", "authorization", "password", "secret", "token", "base_url"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _db_path() -> Path:
    override = os.getenv("ATLAS_EVALUATION_DB_PATH")
    if override:
        return Path(override)
    return get_settings().data_dir / "evaluation_tasks.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize(item)
            for key, item in value.items()
            if key.lower() not in SENSITIVE_KEYS
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _summary_from_report(report: dict[str, Any]) -> dict[str, Any]:
    model = report.get("model") or {}
    return {
        "suite_version": report.get("suite_version"),
        "model_name": model.get("model") or "",
        "provider": model.get("provider") or "",
        "total": int(report.get("total") or 0),
        "passed": int(report.get("passed") or 0),
        "hit_rate": float(report.get("hit_rate") or 0),
        "execution_rate": float(report.get("execution_rate") or 0),
        "model_rate": float(report.get("model_rate") or 0),
        "avg_elapsed_ms": float(report.get("avg_elapsed_ms") or 0),
        "available": bool(report.get("available")),
    }


def _insert_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    label: str,
    status: str,
    source: str,
    created_at: str,
    workers: int,
    report: dict[str, Any] | None = None,
    error: str = "",
    progress_total: int = 0,
    progress_completed: int = 0,
) -> None:
    summary = _summary_from_report(report or {})
    conn.execute(
        """
        INSERT OR IGNORE INTO evaluation_tasks (
            id, label, status, source, created_at, started_at, finished_at,
            workers, progress_total, progress_completed, suite_version,
            model_name, provider, total, passed, hit_rate, execution_rate,
            model_rate, avg_elapsed_ms, available, report_json, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            label,
            status,
            source,
            created_at,
            created_at if status in {"completed", "failed"} else None,
            created_at if status in {"completed", "failed"} else None,
            workers,
            progress_total,
            progress_completed,
            summary["suite_version"],
            summary["model_name"],
            summary["provider"],
            summary["total"],
            summary["passed"],
            summary["hit_rate"],
            summary["execution_rate"],
            summary["model_rate"],
            summary["avg_elapsed_ms"],
            1 if summary["available"] else 0,
            json.dumps(_sanitize(report), ensure_ascii=False) if report else None,
            error,
        ),
    )


def _seed_archived_reports(conn: sqlite3.Connection) -> None:
    seeded = conn.execute(
        "SELECT value FROM evaluation_meta WHERE key = 'archives_seeded'"
    ).fetchone()
    if seeded and seeded["value"] == "1":
        return
    archive_dir = ROOT / "docs" / "evaluations"
    entries = (
        ("ev_archive_v3_final", "归档 · strict-model-v3-100 最终", "evaluation-v3-100-2026-10-27.json"),
        ("ev_archive_v3_initial", "归档 · strict-model-v3-100 首轮", "evaluation-v3-100-initial-2026-10-27.json"),
        ("ev_archive_v2", "归档 · strict-model-v2 旧版", "evaluation-v2-2026-10-27.json"),
    )
    for task_id, label, filename in entries:
        path = archive_dir / filename
        if not path.exists():
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        _insert_task(
            conn,
            task_id=task_id,
            label=label,
            status="completed",
            source="archive",
            created_at=str(report.get("evaluated_at") or _now_iso()),
            workers=int((report.get("runtime") or {}).get("workers") or 1),
            report=report,
            progress_total=int(report.get("total") or 0),
            progress_completed=int(report.get("total") or 0),
        )
    conn.execute(
        "INSERT OR REPLACE INTO evaluation_meta(key, value) VALUES ('archives_seeded', '1')"
    )


class ActiveEvaluationTaskError(RuntimeError):
    pass


class ModelUnavailableError(RuntimeError):
    pass


class TaskStateError(RuntimeError):
    pass


class DatasetNotFound(ValueError):
    pass


class FeedbackNotFound(ValueError):
    pass


def init_task_store() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS evaluation_tasks (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                status TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                workers INTEGER NOT NULL,
                progress_total INTEGER NOT NULL DEFAULT 0,
                progress_completed INTEGER NOT NULL DEFAULT 0,
                suite_version TEXT,
                model_name TEXT,
                provider TEXT,
                total INTEGER NOT NULL DEFAULT 0,
                passed INTEGER NOT NULL DEFAULT 0,
                hit_rate REAL NOT NULL DEFAULT 0,
                execution_rate REAL NOT NULL DEFAULT 0,
                model_rate REAL NOT NULL DEFAULT 0,
                avg_elapsed_ms REAL NOT NULL DEFAULT 0,
                available INTEGER NOT NULL DEFAULT 0,
                report_json TEXT,
                error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS evaluation_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_evaluation_tasks_created
                ON evaluation_tasks(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_evaluation_tasks_status
                ON evaluation_tasks(status, created_at DESC);
            CREATE TABLE IF NOT EXISTS evaluation_datasets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                case_ids_json TEXT NOT NULL,
                case_count INTEGER NOT NULL,
                checksum TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'published',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evaluation_experiments (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                hypothesis TEXT NOT NULL DEFAULT '',
                dataset_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evaluation_feedback (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                category TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(task_id, case_id)
            );
            """
        )
        conn.execute("ALTER TABLE evaluation_tasks ADD COLUMN dataset_id TEXT NOT NULL DEFAULT 'dataset_v3_100'") if not _has_column(conn, 'evaluation_tasks', 'dataset_id') else None
        conn.execute("ALTER TABLE evaluation_tasks ADD COLUMN experiment_id TEXT") if not _has_column(conn, 'evaluation_tasks', 'experiment_id') else None
        conn.execute("ALTER TABLE evaluation_tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 5") if not _has_column(conn, 'evaluation_tasks', 'priority') else None
        conn.execute("ALTER TABLE evaluation_tasks ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0") if not _has_column(conn, 'evaluation_tasks', 'cancel_requested') else None
        conn.execute("ALTER TABLE evaluation_tasks ADD COLUMN retry_of TEXT") if not _has_column(conn, 'evaluation_tasks', 'retry_of') else None
        conn.execute("ALTER TABLE evaluation_tasks ADD COLUMN task_kind TEXT NOT NULL DEFAULT 'evaluation'") if not _has_column(conn, 'evaluation_tasks', 'task_kind') else None
        _seed_archived_reports(conn)
        _seed_datasets(conn)


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row['name'] == column for row in conn.execute(f'PRAGMA table_info({table})').fetchall())


def _seed_datasets(conn: sqlite3.Connection) -> None:
    from app.nl2sql.evaluation import CASES
    ids = [c['id'] for c in CASES]
    payload = {
        'dataset_v3_100': ('strict-model-v3-100', 'published', ids, 'Strict model full regression set'),
        'dataset_v3_initial': ('strict-model-v3-100', 'archived', ids, 'Original 100-case diagnostic run'),
    }
    for dataset_id, (name, status, case_ids, description) in payload.items():
        checksum = __import__('hashlib').sha256(__import__('json').dumps(case_ids, sort_keys=True).encode()).hexdigest()
        conn.execute('INSERT OR IGNORE INTO evaluation_datasets(id,name,version,description,case_ids_json,case_count,checksum,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                     (dataset_id, name, dataset_id.rsplit('_',1)[-1], description, json.dumps(case_ids), len(case_ids), checksum, status, _now_iso()))


def list_datasets() -> list[dict[str, Any]]:
    init_task_store()
    with _connect() as conn:
        rows = conn.execute('SELECT * FROM evaluation_datasets ORDER BY created_at DESC').fetchall()
    result=[]
    for row in rows:
        result.append({'id':row['id'],'name':row['name'],'version':row['version'],'description':row['description'],'case_count':row['case_count'],'checksum':row['checksum'],'status':row['status'],'created_at':row['created_at']})
    return result


def create_dataset(*, name: str, version: str, case_ids: list[str], description: str = '') -> dict[str, Any]:
    init_task_store()
    from app.nl2sql.evaluation import CASES
    known = {c['id'] for c in CASES}
    invalid = sorted(set(case_ids)-known)
    if invalid:
        raise ValueError('未知评测用例：' + ', '.join(invalid[:5]))
    clean=[]
    for cid in case_ids:
        if cid not in clean: clean.append(cid)
    if not clean:
        raise ValueError('数据集至少包含一条用例')
    did='dataset_'+__import__('uuid').uuid4().hex[:10]
    checksum=__import__('hashlib').sha256(json.dumps(clean,sort_keys=True).encode()).hexdigest()
    with _connect() as conn:
        conn.execute('INSERT INTO evaluation_datasets(id,name,version,description,case_ids_json,case_count,checksum,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                     (did,name.strip()[:80] or '自定义数据集',version.strip()[:40] or 'v1',description.strip()[:240],json.dumps(clean),len(clean),checksum,'published',_now_iso()))
    return next(d for d in list_datasets() if d['id']==did)


def create_experiment(*, name: str, dataset_id: str, hypothesis: str = '') -> dict[str, Any]:
    init_task_store()
    with _connect() as conn:
        if not conn.execute('SELECT 1 FROM evaluation_datasets WHERE id=?',(dataset_id,)).fetchone():
            raise DatasetNotFound('数据集不存在')
        eid='exp_'+__import__('uuid').uuid4().hex[:10]
        conn.execute('INSERT INTO evaluation_experiments(id,name,hypothesis,dataset_id,created_at) VALUES (?,?,?,?,?)',(eid,name.strip()[:80] or '模型实验',hypothesis.strip()[:240],dataset_id,_now_iso()))
    return get_experiment(eid)


def get_experiment(eid: str) -> dict[str, Any]:
    init_task_store()
    with _connect() as conn:
        row=conn.execute('SELECT * FROM evaluation_experiments WHERE id=?',(eid,)).fetchone()
        tasks=conn.execute('SELECT * FROM evaluation_tasks WHERE experiment_id=? ORDER BY created_at DESC',(eid,)).fetchall()
    if row is None: raise ValueError('实验不存在')
    return {**dict(row), 'tasks':[_row_to_task(t) for t in tasks]}


def list_experiments() -> list[dict[str, Any]]:
    init_task_store()
    with _connect() as conn:
        rows=conn.execute('SELECT * FROM evaluation_experiments ORDER BY created_at DESC').fetchall()
    return [get_experiment(row['id']) for row in rows]


def _row_to_task(row: sqlite3.Row, *, include_report: bool = False) -> dict[str, Any]:
    payload = {
        "id": row["id"],
        "label": row["label"],
        "status": row["status"],
        "source": row["source"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "workers": row["workers"],
        "progress": {
            "completed": row["progress_completed"],
            "total": row["progress_total"],
            "percent": round(
                row["progress_completed"] * 100 / max(1, row["progress_total"]), 1
            )
            if row["progress_total"]
            else 0,
        },
        "suite_version": row["suite_version"],
        "model_name": row["model_name"],
        "provider": row["provider"],
        "total": row["total"],
        "passed": row["passed"],
        "hit_rate": row["hit_rate"],
        "execution_rate": row["execution_rate"],
        "model_rate": row["model_rate"],
        "avg_elapsed_ms": row["avg_elapsed_ms"],
        "available": bool(row["available"]),
        "error": row["error"],
        "dataset_id": row["dataset_id"],
        "experiment_id": row["experiment_id"],
        "priority": row["priority"],
        "cancel_requested": bool(row["cancel_requested"]),
        "retry_of": row["retry_of"],
        "task_kind": row["task_kind"],
    }
    if include_report:
        try:
            payload["report"] = json.loads(row["report_json"]) if row["report_json"] else None
        except json.JSONDecodeError:
            payload["report"] = None
    return payload


def list_evaluation_tasks(limit: int = 20) -> dict[str, Any]:
    init_task_store()
    with _connect() as conn:
        active = conn.execute(
            "SELECT * FROM evaluation_tasks WHERE status IN ('queued', 'running') "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        rows = conn.execute(
            "SELECT * FROM evaluation_tasks ORDER BY created_at DESC LIMIT ?",
            (max(1, min(limit, 50)),),
        ).fetchall()
    return {
        "tasks": [_row_to_task(row) for row in rows],
        "active": _row_to_task(active) if active else None,
    }


def get_evaluation_task(task_id: str, *, include_report: bool = True) -> dict[str, Any] | None:
    init_task_store()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM evaluation_tasks WHERE id = ?", (task_id,)
        ).fetchone()
    return _row_to_task(row, include_report=include_report) if row else None


def create_evaluation_task(*, label: str | None = None, workers: int = 3, dataset_id: str = "dataset_v3_100", experiment_id: str | None = None, priority: int = 5) -> dict[str, Any]:
    if workers not in (1, 2, 3):
        raise ValueError("workers must be 1, 2 or 3")
    config = get_model_config()
    if not config.enabled or not config.active:
        raise ModelUnavailableError("模型未启用或密钥不完整，无法创建严格模型评测任务")

    task_id = "ev_" + uuid.uuid4().hex[:12]
    now = _now_iso()
    default_label = f"模型评测 · {now[:16].replace('T', ' ')}"
    normalized_label = " ".join((label or default_label).split())[:80] or default_label
    init_task_store()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM evaluation_datasets WHERE id=?", (dataset_id,)).fetchone():
            raise DatasetNotFound("数据集不存在")
        if experiment_id and not conn.execute("SELECT 1 FROM evaluation_experiments WHERE id=?", (experiment_id,)).fetchone():
            raise ValueError("实验不存在")
        _insert_task(
            conn,
            task_id=task_id,
            label=normalized_label,
            status="queued",
            source="runtime",
            created_at=now,
            workers=workers,
        )
        conn.execute("UPDATE evaluation_tasks SET dataset_id=?, experiment_id=?, priority=? WHERE id=?", (dataset_id, experiment_id, min(max(priority,1),9), task_id))
    task = get_evaluation_task(task_id)
    if task is None:
        raise RuntimeError("Failed to create evaluation task")
    return task


def _task_cancelled(task_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT cancel_requested, status FROM evaluation_tasks WHERE id=?", (task_id,)).fetchone()
    return bool(row and (row["cancel_requested"] or row["status"] == "cancelled"))


def cancel_evaluation_task(task_id: str) -> dict[str, Any]:
    init_task_store()
    with _connect() as conn:
        row = conn.execute("SELECT status FROM evaluation_tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError("评测任务不存在")
        if row["status"] == "queued":
            conn.execute("UPDATE evaluation_tasks SET status='cancelled', cancel_requested=1, finished_at=? WHERE id=?", (_now_iso(), task_id))
        elif row["status"] == "running":
            conn.execute("UPDATE evaluation_tasks SET cancel_requested=1 WHERE id=?", (task_id,))
        elif row["status"] not in TERMINAL_STATUSES:
            raise TaskStateError("任务当前不可取消")
    return get_evaluation_task(task_id)


def retry_evaluation_task(task_id: str, *, label: str | None = None) -> dict[str, Any]:
    original = get_evaluation_task(task_id)
    if original is None:
        raise ValueError("评测任务不存在")
    if original["status"] not in TERMINAL_STATUSES:
        raise TaskStateError("只能重试已结束的任务")
    task = create_evaluation_task(label=label or f"{original['label']} · retry", workers=int(original["workers"]), dataset_id=original["dataset_id"], experiment_id=original["experiment_id"], priority=int(original["priority"]))
    with _connect() as conn:
        conn.execute("UPDATE evaluation_tasks SET retry_of=? WHERE id=?", (task_id, task["id"]))
    return get_evaluation_task(task["id"]) or task


def claim_next_evaluation_task(*, worker_id: str = "local") -> dict[str, Any] | None:
    init_task_store()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        running = conn.execute("SELECT 1 FROM evaluation_tasks WHERE status='running' LIMIT 1").fetchone()
        if running:
            return None
        row = conn.execute("SELECT * FROM evaluation_tasks WHERE status='queued' ORDER BY priority, created_at LIMIT 1").fetchone()
        if row is None:
            return None
        conn.execute("UPDATE evaluation_tasks SET status='running', started_at=?, error='' WHERE id=? AND status='queued'", (_now_iso(), row["id"]))
    return get_evaluation_task(row["id"])


def claim_task(task_id: str) -> dict[str, Any]:
    """Atomically claim one named queued task without blocking other queues."""
    init_task_store()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM evaluation_tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError("评测任务不存在")
        if row["status"] == "queued":
            conn.execute(
                "UPDATE evaluation_tasks SET status='running', started_at=?, error='' "
                "WHERE id=? AND status='queued'",
                (_now_iso(), task_id),
            )
    return get_evaluation_task(task_id)


def cancel_all_pending() -> int:
    init_task_store()
    with _connect() as conn:
        cur = conn.execute("UPDATE evaluation_tasks SET status='cancelled', finished_at=? WHERE status='queued'", (_now_iso(),))
    return cur.rowcount


def add_feedback(*, task_id: str, case_id: str, category: str, note: str = "") -> dict[str, Any]:
    if category not in {"wrong_result", "wrong_sql", "bad_narrative", "retrieval", "other"}:
        raise ValueError("不支持的反馈分类")
    if get_evaluation_task(task_id) is None:
        raise ValueError("评测任务不存在")
    init_task_store()
    now = _now_iso()
    with _connect() as conn:
        conn.execute("INSERT INTO evaluation_feedback(id,task_id,case_id,category,note,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(task_id,case_id) DO UPDATE SET category=excluded.category,note=excluded.note,status='open',updated_at=excluded.updated_at", ("fb_"+uuid.uuid4().hex[:10],task_id,case_id,category,note[:500],'open',now,now))
        row=conn.execute("SELECT * FROM evaluation_feedback WHERE task_id=? AND case_id=?",(task_id,case_id)).fetchone()
    return dict(row)


def list_feedback(status: str | None = None) -> list[dict[str, Any]]:
    init_task_store()
    with _connect() as conn:
        if status:
            rows=conn.execute("SELECT * FROM evaluation_feedback WHERE status=? ORDER BY updated_at DESC",(status,)).fetchall()
        else:
            rows=conn.execute("SELECT * FROM evaluation_feedback ORDER BY updated_at DESC").fetchall()
    return [dict(row) for row in rows]


def _advance_task(task_id: str) -> None:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE evaluation_tasks "
                "SET progress_completed = MIN(progress_total, progress_completed + 1) "
                "WHERE id = ? AND status = 'running'",
                (task_id,),
            )
    except sqlite3.Error:
        # Progress persistence must not fail an otherwise valid evaluation.
        pass


def _redact_report(report: dict[str, Any], config: Any) -> dict[str, Any]:
    text = json.dumps(_sanitize(report), ensure_ascii=False)
    for secret in (getattr(config, "api_key", ""), getattr(config, "base_url", "")):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return json.loads(text)


def execute_evaluation_task(task_id: str | None = None) -> dict[str, Any] | None:
    """Execute one explicitly requested task, or drain the queue when omitted.

    The database-backed claim is the concurrency boundary. An explicit task id
    must return only that task's result; a background drain continues until the
    queue is empty. This separation prevents a background worker from
    accidentally completing and returning a different queued task.
    """
    if task_id:
        current = get_evaluation_task(task_id)
        if current is None:
            raise ValueError("评测任务不存在")
        if current["status"] in TERMINAL_STATUSES:
            return current
        if current["status"] == "queued":
            current = claim_task(task_id)
        if current["status"] == "running":
            return _execute_claimed_task(current)
        return current

    last_result: dict[str, Any] | None = None
    while True:
        task = claim_next_evaluation_task()
        if task is None:
            return last_result
        try:
            last_result = _execute_claimed_task(task)
        except Exception:
            last_result = get_evaluation_task(task["id"]) or task



def _execute_claimed_task(task: dict[str, Any]) -> dict[str, Any]:
    """Execute a task already atomically transitioned to ``running``."""
    import hashlib
    import time
    from app.nl2sql.evaluation import CASES

    task_id = task["id"]
    with _connect() as conn:
        dataset_row = conn.execute(
            "SELECT * FROM evaluation_datasets WHERE id=?", (task["dataset_id"],)
        ).fetchone()
    if dataset_row is None:
        with _connect() as conn:
            conn.execute(
                "UPDATE evaluation_tasks SET status='failed', finished_at=?, error=? WHERE id=?",
                (_now_iso(), "任务数据集不存在", task_id),
            )
        return get_evaluation_task(task_id) or task
    selected_ids = set(json.loads(dataset_row["case_ids_json"]))
    selected_cases = tuple(case for case in CASES if case["id"] in selected_ids)
    started = time.perf_counter()
    with _connect() as conn:
        conn.execute(
            "UPDATE evaluation_tasks SET progress_total = ?, progress_completed = 0, error = '' WHERE id = ?",
            (len(selected_cases), task_id),
        )
    try:
        config = get_model_config()
        with model_config_snapshot(config):
            report = run_evaluation(
                on_case=lambda _case: _advance_task(task_id),
                workers=int(task["workers"]),
                cases=selected_cases,
                cancel_check=lambda: _task_cancelled(task_id),
            )
        source_root = ROOT / "app"
        source_hash = hashlib.sha256()
        for path in sorted(source_root.rglob("*.py")):
            source_hash.update(path.relative_to(source_root).as_posix().encode())
            source_hash.update(path.read_bytes())
        manifest = dataset_row["case_ids_json"].encode("utf-8")
        report["runtime"] = {
            "workers": int(task["workers"]),
            "wall_elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "source_sha256": source_hash.hexdigest(),
        }
        report["evaluation_contract"] = "strict-v3-single-channel-scalar-v1"
        report["case_manifest_sha256"] = hashlib.sha256(manifest).hexdigest()
        report = _redact_report(report, config)
        summary = _summary_from_report(report)
        finished = _now_iso()
        with _connect() as conn:
            conn.execute(
                """
                UPDATE evaluation_tasks SET
                    status = 'completed', finished_at = ?, progress_completed = progress_total,
                    suite_version = ?, model_name = ?, provider = ?, total = ?, passed = ?,
                    hit_rate = ?, execution_rate = ?, model_rate = ?, avg_elapsed_ms = ?,
                    available = ?, report_json = ?, error = ''
                WHERE id = ?
                """,
                (
                    finished, summary["suite_version"], summary["model_name"],
                    summary["provider"], summary["total"], summary["passed"],
                    summary["hit_rate"], summary["execution_rate"], summary["model_rate"],
                    summary["avg_elapsed_ms"], 1 if summary["available"] else 0,
                    json.dumps(report, ensure_ascii=False), task_id,
                ),
            )
        return get_evaluation_task(task_id) or task
    except Exception as exc:
        cancelled = "cancelled" in str(exc).lower() or _task_cancelled(task_id)
        with _connect() as conn:
            conn.execute(
                "UPDATE evaluation_tasks SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                ("cancelled" if cancelled else "failed", _now_iso(), str(exc)[:1000], task_id),
            )
        if cancelled:
            return get_evaluation_task(task_id) or task
        raise


def _case_map(report: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        str(case.get("id")): case
        for case in ((report or {}).get("cases") or [])
        if isinstance(case, dict) and case.get("id")
    }


def compare_evaluation_tasks(baseline_id: str, candidate_id: str) -> dict[str, Any]:
    baseline = get_evaluation_task(baseline_id)
    candidate = get_evaluation_task(candidate_id)
    if baseline is None or candidate is None:
        missing = baseline_id if baseline is None else candidate_id
        raise KeyError(missing)
    baseline_cases = _case_map(baseline.get("report"))
    candidate_cases = _case_map(candidate.get("report"))
    shared = sorted(set(baseline_cases) & set(candidate_cases))
    regressed = []
    fixed = []
    for case_id in shared:
        before = bool(baseline_cases[case_id].get("hit"))
        after = bool(candidate_cases[case_id].get("hit"))
        item = {
            "id": case_id,
            "question": candidate_cases[case_id].get("question") or baseline_cases[case_id].get("question"),
            "before": before,
            "after": after,
        }
        if before and not after:
            regressed.append(item)
        elif not before and after:
            fixed.append(item)
    return {
        "baseline": {key: baseline.get(key) for key in (
            "id", "label", "created_at", "model_name", "total", "passed", "hit_rate",
            "execution_rate", "model_rate", "avg_elapsed_ms",
        )},
        "candidate": {key: candidate.get(key) for key in (
            "id", "label", "created_at", "model_name", "total", "passed", "hit_rate",
            "execution_rate", "model_rate", "avg_elapsed_ms",
        )},
        "delta": {
            "passed": candidate["passed"] - baseline["passed"],
            "hit_rate": round(candidate["hit_rate"] - baseline["hit_rate"], 1),
            "execution_rate": round(candidate["execution_rate"] - baseline["execution_rate"], 1),
            "model_rate": round(candidate["model_rate"] - baseline["model_rate"], 1),
            "avg_elapsed_ms": round(candidate["avg_elapsed_ms"] - baseline["avg_elapsed_ms"], 1),
        },
        "shared_cases": len(shared),
        "fixed": fixed,
        "regressed": regressed,
        "unchanged_passed": sum(
            1 for case_id in shared
            if baseline_cases[case_id].get("hit") and candidate_cases[case_id].get("hit")
        ),
        "unchanged_failed": sum(
            1 for case_id in shared
            if not baseline_cases[case_id].get("hit") and not candidate_cases[case_id].get("hit")
        ),
    }
