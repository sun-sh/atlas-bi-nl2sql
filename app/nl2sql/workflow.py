from __future__ import annotations

import time
import uuid
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.nl2sql import generator
from app.nl2sql.generator import generate_llm_repair_sql, generate_llm_sql, generate_rule_sql
from app.nl2sql.models import QueryPlan, QueryResult, SchemaHit
from app.nl2sql.planner import make_plan
from app.nl2sql.result_insight import (
    NarrativeResult,
    build_result_digest,
    narrative_warnings,
    narrate_result,
    rule_narrative,
)
from app.nl2sql.retrieval import HybridSchemaRetriever
from app.nl2sql.settings import get_settings
from app.nl2sql.sql_engine import execute_sql, explain_error, validate_sql


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
    mode: str
    context: dict[str, Any]
    narrate: bool
    allow_rule_fallback: bool
    narrative: NarrativeResult
    final: QueryResult


_retriever: HybridSchemaRetriever | None = None


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
    plan = make_plan(state["question"], state["hits"], state.get("context"))
    return {"plan": plan}


def generate_node(state: GraphState) -> GraphState:
    plan = state["plan"]
    sql = generate_llm_sql(state["question"], plan, state["hits"])
    mode = "llm"
    warnings = list(state.get("warnings", []))
    if not sql:
        if state.get("allow_rule_fallback", True):
            sql = generate_rule_sql(state["question"], plan, state["hits"])
            mode = "rule"
            if generator.LAST_LLM_ERROR:
                warnings.append("模型调用失败，已回退规则 SQL：" + generator.LAST_LLM_ERROR)
        else:
            reason = generator.LAST_LLM_ERROR or "模型返回空内容"
            warnings.append("模型未生成 SQL，严格模型评测不回退规则：" + reason)
            return {"sql": "", "mode": "model-failed", "warnings": warnings}
    return {"sql": sql, "mode": mode, "warnings": warnings}


def execute_node(state: GraphState) -> GraphState:
    started = time.perf_counter()
    sql = state["sql"]
    attempts = list(state.get("attempts", []))
    warnings = list(state.get("warnings", []))
    try:
        safe_sql = validate_sql(sql)
        result = execute_sql(safe_sql)
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
    if state.get("mode") in {"model-failed", "model-repair-failed"}:
        return "finalize"
    result = state.get("result")
    if result is not None:
        return "finish"
    attempts = state.get("attempts", [])
    if len(attempts) <= get_settings().max_repair_attempts:
        return "repair"
    return "finalize"


def finalize_node(state: GraphState) -> GraphState:
    result = state.get("result") or {"rows": [], "columns": [], "row_count": 0, "truncated": False, "elapsed_ms": 0}
    plan = state["plan"]
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
    warnings = list(state.get("warnings", [])) + narrative_warnings(narrative)
    mode = state.get("mode", "rule")
    if mode.startswith("rule"):
        warnings.append("当前离线模式使用规则 SQL 生成器；配置 LLM_ENABLED=true 可切换为模型生成。")
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


def run_query(
    question: str,
    context: dict[str, Any] | None = None,
    *,
    narrate: bool = True,
    allow_rule_fallback: bool = True,
) -> QueryResult:
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    initial: GraphState = {
        "question": question.strip(),
        "context": context or {},
        "started": time.perf_counter(),
        "trace_id": uuid.uuid4().hex[:12],
        "narrate": narrate,
        "allow_rule_fallback": allow_rule_fallback,
    }
    state = _GRAPH.invoke(initial)
    return state["final"]
