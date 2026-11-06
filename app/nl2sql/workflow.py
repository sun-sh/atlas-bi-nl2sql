from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Iterator, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph

from app.nl2sql import generator
from app.nl2sql.harness import create_run_context, is_cancelled
from app.nl2sql.intent_guard import OUTCOME_ANSWERED, OUTCOME_CLARIFICATION, OUTCOME_UNSUPPORTED, assess_question
from app.nl2sql.model_config import get_model_config
from app.nl2sql.generator import generate_llm_repair_sql, generate_llm_sql, generate_rule_sql
from app.nl2sql.models import QueryPlan, QueryResult, SchemaHit
from app.nl2sql.planner import make_plan
from app.nl2sql.result_insight import (
    NarrativeResult,
    build_result_digest,
    narrative_warnings,
    narrate_result,
    rule_narrative,
    validate_narrative,
)
from app.nl2sql.retrieval import HybridSchemaRetriever
from app.nl2sql.settings import get_settings
from app.nl2sql.sql_engine import QueryCancelled, execute_sql, explain_error, validate_sql


class GraphState(TypedDict, total=False):
    question: str
    retrieval_question: str
    hits: list[SchemaHit]
    plan: QueryPlan
    sql: str
    result: dict[str, Any]
    attempts: list[dict[str, Any]]
    warnings: list[str]
    started: float
    trace_id: str
    run_context: dict[str, Any]
    mode: str
    context: dict[str, Any]
    narrate: bool
    allow_rule_fallback: bool
    narrative: NarrativeResult
    generation_retries: int
    decision: dict[str, Any]
    db_path: str
    final: QueryResult


_retriever: HybridSchemaRetriever | None = None


def _emit_stage(stage: str) -> None:
    try:
        get_stream_writer()({"stage": stage})
    except RuntimeError:
        pass


def get_retriever() -> HybridSchemaRetriever:
    global _retriever
    if _retriever is None:
        _retriever = HybridSchemaRetriever()
    return _retriever


def _context_retrieval_question(question: str, context: dict[str, Any] | None) -> str:
    if not context:
        return question
    previous = str(context.get("question") or "").strip()
    if not previous:
        return question
    compact_question = question.strip()
    if compact_question == previous or compact_question in previous:
        return previous
    return f"{previous} {compact_question}"


def retrieve_node(state: GraphState) -> GraphState:
    _emit_stage("retrieve")
    retrieval_question = _context_retrieval_question(
        state["question"], state.get("context")
    )
    hits = get_retriever().search(retrieval_question, limit=4)
    return {
        "retrieval_question": retrieval_question,
        "hits": hits,
        "attempts": [],
        "warnings": [],
    }


def plan_node(state: GraphState) -> GraphState:
    decision = assess_question(state["question"], state.get("context"))
    if not decision.answerable:
        # A clarification/refusal is not an executable query plan. Keep the
        # outcome visible without inventing a default metric or table intent.
        plan = QueryPlan(
            intent="",
            tables=[],
            metrics=[],
            dimensions=[],
            filters=[],
            time_range="",
            chart_type="table",
            notes=["问题在可答性判定阶段停止，未进入 SQL 规划。"],
            outcome=decision.outcome,
            reason_code=decision.reason_code,
            decision=decision.as_dict(),
        )
        return {"plan": plan, "decision": decision.as_dict()}
    _emit_stage("plan")
    plan = make_plan(state["question"], state["hits"], state.get("context"))
    plan.outcome = decision.outcome
    plan.reason_code = decision.reason_code
    plan.decision = decision.as_dict()
    return {"plan": plan, "decision": decision.as_dict()}


def generate_node(state: GraphState) -> GraphState:
    if state.get("decision", {}).get("outcome") not in (None, OUTCOME_ANSWERED):
        return {"sql": "", "mode": "unsupported", "warnings": [], "generation_retries": 0}
    _emit_stage("generate")
    plan = state["plan"]
    sql = generate_llm_sql(state["question"], plan, state["hits"])
    mode = "llm"
    warnings = list(state.get("warnings", []))
    if not sql:
        reason = generator.LAST_LLM_ERROR or "模型返回空内容"
        retry_count = 0
        if get_settings().max_repair_attempts > 0 and (get_model_config().enabled or not state.get("allow_rule_fallback", True)):
            _emit_stage("repair")
            retry_count = 1
            sql = generate_llm_repair_sql(
                state["question"], plan, state["hits"], "",
                f"首次SQL未产出：{reason}。重新生成完整SELECT，保留原查询口径。",
            )
            warnings.append("首次 SQL 为空，已触发模型重试")
            if sql:
                return {
                    "sql": sql, "mode": "llm-repair", "generation_retries": retry_count,
                    "warnings": warnings + ["修复来源：llm-repair"],
                }
        if state.get("allow_rule_fallback", True):
            sql = generate_rule_sql(state["question"], plan, state["hits"])
            mode = "rule"
            if generator.LAST_LLM_ERROR:
                warnings.append("模型调用失败，已回退规则 SQL：" + generator.LAST_LLM_ERROR)
        else:
            warnings.append("模型未生成 SQL，严格模型评测不回退规则：" + reason)
            return {"sql": "", "mode": "model-failed", "warnings": warnings, "generation_retries": retry_count}
        return {"sql": sql, "mode": mode, "warnings": warnings, "generation_retries": retry_count}
    return {"sql": sql, "mode": mode, "warnings": warnings, "generation_retries": 0}


def execute_node(state: GraphState) -> GraphState:
    if state.get("decision", {}).get("outcome") not in (None, OUTCOME_ANSWERED):
        return {"attempts": [], "warnings": []}
    _emit_stage("execute")
    started = time.perf_counter()
    sql = state["sql"]
    attempts = list(state.get("attempts", []))
    warnings = list(state.get("warnings", []))
    try:
        safe_sql = validate_sql(sql)
        result = execute_sql(
            safe_sql,
            cancel_trace_id=state.get("trace_id"),
            db_path=state.get("db_path"),
        )
        if result["rows"] and all(
            value is None
            for row in result["rows"]
            for value in row.values()
        ):
            raise ValueError("查询返回全为空的聚合结果；请检查时间、筛选条件和聚合字段")
        attempts.append(
            {
                "attempt": len(attempts) + 1,
                "status": "success",
                "sql": safe_sql,
                "error": None,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        )
        return {"sql": safe_sql, "result": result, "attempts": attempts, "warnings": warnings}
    except QueryCancelled:
        raise
    except Exception as exc:
        message = explain_error(exc, sql)
        attempts.append(
            {
                "attempt": len(attempts) + 1,
                "status": "failed",
                "sql": sql,
                "error": message,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        )
        warnings.append(message)
        return {"attempts": attempts, "warnings": warnings}


def repair_node(state: GraphState) -> GraphState:
    _emit_stage("repair")
    plan = state["plan"]
    question = state["question"]
    attempts = state.get("attempts", [])
    last_error = attempts[-1]["error"] if attempts else ""
    repaired = generate_llm_repair_sql(
        question,
        plan,
        state["hits"],
        state.get("sql", ""),
        last_error,
    )
    warning = f"第 {len(attempts)} 次执行失败：{last_error}"
    if repaired:
        sql = repaired
        mode = "llm-repair"
        extra = ["修复来源：" + mode]
    elif state.get("allow_rule_fallback", True):
        sql = generate_rule_sql(question, plan, state["hits"])
        mode = "rule-repair"
        extra = ["修复来源：" + mode]
        if generator.LAST_LLM_ERROR:
            extra.append("模型修复失败：" + generator.LAST_LLM_ERROR)
    else:
        sql = ""
        mode = "model-repair-failed"
        extra = ["严格模型评测不回退规则"]
        if generator.LAST_LLM_ERROR:
            extra.append("模型修复失败：" + generator.LAST_LLM_ERROR)
    return {
        "sql": sql,
        "mode": mode,
        "warnings": state.get("warnings", []) + [warning, *extra],
    }


def should_repair(state: GraphState) -> str:
    if state.get("decision", {}).get("outcome") not in (None, OUTCOME_ANSWERED):
        return "finalize"
    if state.get("mode") in {"model-failed", "model-repair-failed"}:
        return "finalize"
    result = state.get("result")
    if result is not None:
        return "finish"
    attempts = state.get("attempts", [])
    if len(attempts) + state.get("generation_retries", 0) <= get_settings().max_repair_attempts:
        return "repair"
    return "finalize"


def finalize_node(state: GraphState) -> GraphState:
    _emit_stage("finalize")
    plan = state["plan"]
    decision = state.get("decision") or {}
    if decision.get("outcome") not in (None, OUTCOME_ANSWERED):
        answer = str(decision.get("message") or "当前问题不在可回答范围内。")
        suggestions = list(decision.get("suggestions") or [])
        if suggestions:
            answer += "\n建议：\n" + "\n".join(f"- {item}" for item in suggestions)
        final = QueryResult(
            question=state["question"],
            sql="",
            rows=[],
            columns=[],
            chart_type="table",
            answer=answer,
            plan=plan,
            retrieval=state.get("hits", []),
            attempts=[],
            elapsed_ms=(time.perf_counter() - state["started"]) * 1000,
            mode=("clarification" if decision.get("outcome") == OUTCOME_CLARIFICATION else "unsupported"),
            narrative_model="",
            warnings=[],
            trace_id=state["trace_id"],
            narrative_mode="guard",
            run_context=state.get("run_context", {}),
            outcome=str(decision.get("outcome") or "unsupported"),
            narrative_validation={"passed": True, "errors": [], "warnings": [], "contract": "intent-guard-v1"},
        )
        return {"final": final}
    result = state.get("result") or {"rows": [], "columns": [], "row_count": 0, "truncated": False, "elapsed_ms": 0}
    rows = result.get("rows", [])
    columns = result.get("columns", [])
    digest = build_result_digest(
        state["question"],
        plan,
        rows,
        columns,
        int(result.get("row_count", len(rows))),
        bool(result.get("truncated", False)),
    )
    if state.get("narrate", True):
        narrative = narrate_result(state["question"], plan, digest)
    else:
        narrative = NarrativeResult(text=rule_narrative(digest), mode="digest")
    narrative_check = validate_narrative(narrative.text, digest, mode=narrative.mode)
    if narrative.mode == "model" and not narrative_check["passed"]:
        narrative = NarrativeResult(
            text=rule_narrative(digest),
            mode="digest",
            fallback_reason="解读事实校验未通过：" + "；".join(narrative_check["errors"]),
            retries=narrative.retries,
            elapsed_ms=narrative.elapsed_ms,
        )
        narrative_check = validate_narrative(narrative.text, digest, mode="digest")
        narrative_check["fallback_from"] = "model"
    warnings = list(state.get("warnings", [])) + narrative_warnings(narrative)
    mode = state.get("mode", "rule")
    if mode.startswith("rule"):
        warnings.append("当前离线模式使用规则 SQL 生成器；可在模型配置中启用模型生成。")
    final = QueryResult(
        question=state["question"],
        sql=state.get("sql", ""),
        rows=rows,
        columns=columns,
        chart_type=plan.chart_type,
        answer=narrative.text,
        plan=plan,
        retrieval=state["hits"],
        attempts=state.get("attempts", []),
        elapsed_ms=(time.perf_counter() - state["started"]) * 1000,
        mode=mode,
        warnings=warnings,
        trace_id=state["trace_id"],
        narrative_mode=narrative.mode,
        narrative_model=narrative.model,
        narrative_retries=narrative.retries,
        generation_retries=state.get("generation_retries", 0),
        run_context=state.get("run_context", {}),
        outcome=OUTCOME_ANSWERED,
        narrative_validation=narrative_check,
    )
    return {"final": final}


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("plan", plan_node)
    graph.add_node("generate", generate_node)
    graph.add_node("execute", execute_node)
    graph.add_node("repair", repair_node)
    graph.add_node("finalize", finalize_node)
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "plan")
    graph.add_edge("plan", "generate")
    graph.add_edge("generate", "execute")
    graph.add_conditional_edges("execute", should_repair, {"finish": "finalize", "repair": "repair", "finalize": "finalize"})
    graph.add_edge("repair", "execute")
    graph.add_edge("finalize", END)
    return graph.compile()


_GRAPH = None


def _initial_state(
    question: str,
    context: dict[str, Any] | None,
    *,
    narrate: bool,
    allow_rule_fallback: bool,
    trace_id: str | None = None,
    db_path: str | Path | None = None,
) -> GraphState:
    trace_id = trace_id or uuid.uuid4().hex[:12]
    return {
        "question": question.strip(),
        "context": context or {},
        "started": time.perf_counter(),
        "trace_id": trace_id,
        "run_context": create_run_context(
            trace_id=trace_id,
            timeout_seconds=get_settings().query_timeout_seconds,
            max_repair_attempts=get_settings().max_repair_attempts,
            max_result_rows=get_settings().max_rows,
        ).as_dict(),
        "narrate": narrate,
        "allow_rule_fallback": allow_rule_fallback,
        "db_path": str(db_path) if db_path else "",
    }


def query_stream_events(
    question: str,
    context: dict[str, Any] | None = None,
    *,
    narrate: bool = False,
    allow_rule_fallback: bool = True,
    trace_id: str | None = None,
    db_path: str | Path | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield graph stage updates followed by the finalized QueryResult."""
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    initial = _initial_state(
        question,
        context,
        narrate=narrate,
        allow_rule_fallback=allow_rule_fallback,
        trace_id=trace_id,
        db_path=db_path,
    )
    state: GraphState = {}
    try:
        for mode, chunk in _GRAPH.stream(
                initial,
                stream_mode=["custom", "updates"],
            ):
            if is_cancelled(initial["trace_id"]):
                yield {"type": "cancelled", "trace_id": initial["trace_id"]}
                return
            if mode == "custom":
                if isinstance(chunk, dict) and chunk.get("stage"):
                    yield {
                        "type": "stage",
                        "node": chunk["stage"],
                        "state": state,
                    }
                continue
            if mode == "updates" and isinstance(chunk, dict):
                for values in chunk.values():
                    if isinstance(values, dict):
                        state.update(values)
    except QueryCancelled:
        yield {"type": "cancelled", "trace_id": initial["trace_id"]}
        return
    yield {"type": "result", "result": state["final"]}


def run_query(
    question: str,
    context: dict[str, Any] | None = None,
    *,
    narrate: bool = True,
    allow_rule_fallback: bool = True,
    trace_id: str | None = None,
    db_path: str | Path | None = None,
) -> QueryResult:
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    initial = _initial_state(
        question,
        context,
        narrate=narrate,
        allow_rule_fallback=allow_rule_fallback,
        trace_id=trace_id,
        db_path=db_path,
    )
    state = _GRAPH.invoke(initial)
    return state["final"]