from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, RLock
from typing import Any


POLICY_VERSION = "nl2sql-harness-v1"
MAX_JOINS = 3
MAX_SUBQUERY_DEPTH = 3
_CANCEL_EVENTS: dict[str, Event] = {}
_CANCEL_LOCK = RLock()


@dataclass(frozen=True)
class RunContext:
    trace_id: str
    deadline_seconds: float
    max_repair_attempts: int
    max_result_rows: int
    policy_version: str = POLICY_VERSION
    budget: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "deadline_seconds": self.deadline_seconds,
            "max_repair_attempts": self.max_repair_attempts,
            "max_result_rows": self.max_result_rows,
            "policy_version": self.policy_version,
            "budget": dict(self.budget),
        }


class HarnessPolicyError(ValueError):
    pass


def create_run_context(*, trace_id: str, timeout_seconds: float, max_repair_attempts: int, max_result_rows: int) -> RunContext:
    return RunContext(trace_id=trace_id, deadline_seconds=timeout_seconds, max_repair_attempts=max_repair_attempts, max_result_rows=max_result_rows, budget={"repairs": max_repair_attempts, "result_rows": max_result_rows})


def validate_policy(sql: str, parsed=None, *, dialect: str = "sqlite") -> None:
    """Reject query shapes that can exhaust the bounded query worker."""
    import sqlglot
    from sqlglot import exp
    parse_dialect = {"postgresql": "postgres", "mysql": "mysql", "sqlite": "sqlite"}.get(
        dialect, dialect
    )
    statement = parsed or sqlglot.parse_one(sql, read=parse_dialect)
    if any(cte.args.get("recursive") for cte in statement.find_all(exp.With)):
        raise HarnessPolicyError("递归 CTE 不在当前查询能力范围内")
    joins = list(statement.find_all(exp.Join))
    if len(joins) > MAX_JOINS:
        raise HarnessPolicyError(f"JOIN 数量超过上限 {MAX_JOINS}")
    if any(str(join.args.get("kind") or "").upper() == "CROSS" for join in joins):
        raise HarnessPolicyError("禁止 CROSS JOIN，请使用带关联条件的 JOIN")
    # Count actual nesting depth, not the total number of SELECT nodes. A CTE
    # or four sibling scalar subqueries is wide, not deeply nested, and should
    # not be rejected by a depth policy.
    def _select_depth(node: Any, parent_depth: int = 0) -> int:
        depth = parent_depth + 1 if isinstance(node, exp.Select) else parent_depth
        child_depths = [
            _select_depth(child, depth)
            for child in node.args.values()
            if isinstance(child, (exp.Expression, list))
            for child in (child if isinstance(child, list) else [child])
        ]
        return max([depth, *child_depths])

    deepest = _select_depth(statement)
    # The configured limit is the number of SELECT levels, including the outer
    # query. Express the error as nested subquery depth so it remains intuitive
    # for callers and tests.
    if deepest > MAX_SUBQUERY_DEPTH + 1:
        raise HarnessPolicyError(
            f"子查询嵌套深度超过上限 {MAX_SUBQUERY_DEPTH}"
            f"（实际 {max(0, deepest - 1)} 层）"
        )
    for select in statement.find_all(exp.Select):
        for item in select.expressions:
            target = item.this if isinstance(item, exp.Alias) else item
            if isinstance(target, exp.Star) or (isinstance(target, exp.Column) and isinstance(target.this, exp.Star)):
                raise HarnessPolicyError("禁止 SELECT *，请显式列出字段")


def register_cancel(trace_id: str) -> Event:
    event = Event()
    with _CANCEL_LOCK:
        _CANCEL_EVENTS[trace_id] = event
    return event


def request_cancel(trace_id: str) -> bool:
    with _CANCEL_LOCK:
        event = _CANCEL_EVENTS.get(trace_id)
    if event is None:
        return False
    event.set()
    return True


def is_cancelled(trace_id: str | None = None, event: Event | None = None) -> bool:
    if event is not None:
        return event.is_set()
    if not trace_id:
        return False
    with _CANCEL_LOCK:
        current = _CANCEL_EVENTS.get(trace_id)
    return bool(current and current.is_set())


def release_cancel(trace_id: str) -> None:
    with _CANCEL_LOCK:
        _CANCEL_EVENTS.pop(trace_id, None)
