from __future__ import annotations

from app.nl2sql.retrieval_benchmark import (
    BENCHMARK_CASES,
    BENCHMARK_RELATIONSHIPS,
    benchmark_tables,
    run_large_schema_ablation,
)


def test_benchmark_has_forty_tables_and_labelled_cases():
    tables = benchmark_tables()
    report = run_large_schema_ablation()
    assert len(tables) == 40
    assert report["table_count"] == 40
    assert report["case_count"] == len(BENCHMARK_CASES) == 12


def test_large_schema_ablation_reports_token_and_closure_curve():
    report = run_large_schema_ablation()
    assert report["full_schema"]["estimated_tokens"] > report["retrieved_top4"]["estimated_tokens"]
    assert report["retrieved_top4"]["full_recall_rate"] == 66.7
    # One three-table logistics question does not retrieve its bridge table
    # in Top-4, so a bounded FK closure cannot recover the missing node.
    assert report["closure_top10"]["full_recall_rate"] == 91.7
    assert report["passed"] == 11
    assert report["hit_rate"] == 91.7
    assert report["closure_top10"]["estimated_tokens"] < report["full_schema"]["estimated_tokens"]


def test_closure_uses_declared_relationship_edges_from_top4_only():
    report = run_large_schema_ablation()
    assert report["closure_top10"]["relationship_edges"] == len(BENCHMARK_RELATIONSHIPS)
    by_question = {case["question"]: case for case in report["cases"]}
    support = by_question["客服工单最多的品类"]
    assert support["top4_tables"] == [
        "support_tickets",
        "support_messages",
        "customer_service_scores",
        "support_agents",
    ]
    assert "orders" not in support["top4_tables"]
    assert "orders" in support["closure_tables"]
    logistics = by_question["发货后多久送到，承运商表现如何"]
    assert "shipment_events" not in logistics["top4_tables"]
    assert logistics["full_recall_with_closure"] is False


def test_large_schema_benchmark_never_initializes_shared_vector_backends(monkeypatch):
    from app.nl2sql import retrieval

    def fail(*args, **kwargs):
        raise AssertionError("shared retrieval backend must not be initialized")

    monkeypatch.setattr(retrieval.HybridSchemaRetriever, "_load_elastic", fail)
    monkeypatch.setattr(retrieval.HybridSchemaRetriever, "_load_semantic", fail)
    report = run_large_schema_ablation()
    assert report["passed"] == 11
