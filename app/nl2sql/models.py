from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type: str
    description: str
    sensitivity: str = "public"


@dataclass(frozen=True)
class TableInfo:
    name: str
    label: str
    description: str
    columns: tuple[ColumnInfo, ...]
    aliases: tuple[str, ...] = ()
    grain: str = ""


@dataclass(frozen=True)
class SchemaHit:
    table: str
    label: str
    description: str
    score: float
    vector_score: float
    lexical_score: float
    matched_terms: tuple[str, ...] = ()
    columns: tuple[ColumnInfo, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "label": self.label,
            "description": self.description,
            "score": round(self.score, 4),
            "vector_score": round(self.vector_score, 4),
            "lexical_score": round(self.lexical_score, 4),
            "matched_terms": list(self.matched_terms),
            "columns": [
                {
                    "name": c.name,
                    "type": c.type,
                    "description": c.description,
                    "sensitivity": c.sensitivity,
                }
                for c in self.columns
            ],
        }


@dataclass
class QueryPlan:
    intent: str
    tables: list[str]
    metrics: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    time_range: str = ""
    chart_type: str = "table"
    notes: list[str] = field(default_factory=list)
    inherited: list[str] = field(default_factory=list)
    source_question: str = ""
    context: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "tables": self.tables,
            "metrics": self.metrics,
            "dimensions": self.dimensions,
            "filters": self.filters,
            "time_range": self.time_range,
            "chart_type": self.chart_type,
            "notes": self.notes,
            "inherited": self.inherited,
            "source_question": self.source_question,
            "context": self.context,
        }


@dataclass
class QueryResult:
    question: str
    sql: str
    rows: list[dict[str, Any]]
    columns: list[str]
    chart_type: str
    answer: str
    plan: QueryPlan
    retrieval: list[SchemaHit]
    attempts: list[dict[str, Any]]
    elapsed_ms: float
    mode: str = "rule"
    warnings: list[str] = field(default_factory=list)
    trace_id: str = ""
    narrative_mode: str = "digest"
    narrative_model: str = ""
    generation_retries: int = 0
    narrative_retries: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "sql": self.sql,
            "rows": self.rows,
            "columns": self.columns,
            "chart_type": self.chart_type,
            "answer": self.answer,
            "plan": self.plan.as_dict(),
            "retrieval": [h.as_dict() for h in self.retrieval],
            "attempts": self.attempts,
            "generation_retries": self.generation_retries,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "mode": self.mode,
            "warnings": self.warnings,
            "trace_id": self.trace_id,
            "narrative": {
                "mode": self.narrative_mode,
                "model": self.narrative_model,
                "retries": self.narrative_retries,
            },
        }
