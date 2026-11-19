from __future__ import annotations

import math
from dataclasses import dataclass

from app.nl2sql.models import ColumnInfo, TableInfo
from app.nl2sql.retrieval import HybridSchemaRetriever
from app.nl2sql.schema_catalog import SCHEMA as CORE_SCHEMA


@dataclass(frozen=True)
class BenchmarkTable:
    name: str
    domain: str
    label: str
    description: str
    columns: tuple[str, ...]


_DISTRACTORS = (
    ("warehouse_inventory", "仓储", "仓库库存", "SKU 在仓库中的实时库存与安全库存", ("product_id", "warehouse_id", "on_hand", "reserved", "updated_at")),
    ("warehouse_locations", "仓储", "仓库地点", "仓库、区域和配送覆盖范围", ("warehouse_id", "region", "address", "capacity")),
    ("shipment_orders", "物流", "发运单", "订单对应的包裹发运与签收状态", ("shipment_id", "order_id", "carrier_id", "tracking_no", "shipped_at", "delivered_at")),
    ("shipment_events", "物流", "物流事件", "包裹在运输网络中的扫描事件", ("event_id", "shipment_id", "status", "event_at", "location")),
    ("carriers", "物流", "承运商", "承运商服务等级和结算信息", ("carrier_id", "name", "service_level", "sla_hours")),
    ("coupon_definitions", "营销", "优惠券定义", "优惠券类型、面额、门槛和有效期", ("coupon_id", "name", "discount_type", "face_value", "min_spend")),
    ("coupon_redemptions", "营销", "优惠券核销", "优惠券在订单上的领取和核销记录", ("redemption_id", "coupon_id", "order_id", "customer_id", "redeemed_at")),
    ("promotion_campaigns", "营销", "促销活动", "促销活动预算、周期和渠道", ("campaign_id", "name", "budget", "start_at", "end_at", "channel")),
    ("finance_invoices", "财务", "财务发票", "订单结算发票及税额", ("invoice_id", "order_id", "invoice_amount", "tax_amount", "issued_at")),
    ("finance_settlements", "财务", "结算单", "渠道账单与平台结算周期", ("settlement_id", "channel", "settle_date", "gross_amount", "fee_amount")),
    ("finance_payments", "财务", "支付流水", "第三方支付流水与退款冲正", ("payment_id", "order_id", "method", "amount", "paid_at", "status")),
    ("finance_ledger", "财务", "总账分录", "财务总账借贷分录", ("ledger_id", "account_code", "debit", "credit", "posted_at")),
    ("support_tickets", "客服", "客服工单", "客户咨询、投诉和售后工单", ("ticket_id", "customer_id", "order_id", "category", "status", "created_at")),
    ("support_messages", "客服", "工单消息", "工单内客服与客户的往来消息", ("message_id", "ticket_id", "sender_type", "content", "created_at")),
    ("support_agents", "客服", "客服坐席", "客服团队分组和在线状态", ("agent_id", "team", "status", "shift")),
    ("app_events", "埋点", "App 埋点", "App 页面浏览、点击和曝光事件", ("event_id", "customer_id", "event_name", "page", "event_at")),
    ("app_sessions", "埋点", "App 会话", "用户 App 会话时长与来源", ("app_session_id", "customer_id", "started_at", "ended_at", "source")),
    ("web_events", "埋点", "Web 埋点", "网页浏览与搜索事件", ("event_id", "visitor_id", "event_name", "url", "event_at")),
    ("product_catalog", "商品", "商品目录", "商品主数据与上下架状态", ("product_id", "category", "brand", "status")),
    ("product_prices", "商品", "价格历史", "商品历史售价与成本", ("price_id", "product_id", "price", "effective_at")),
    ("product_suppliers", "供应链", "供应商商品", "商品与供应商采购关系", ("supplier_id", "product_id", "lead_time_days", "purchase_price")),
    ("purchase_orders", "供应链", "采购订单", "采购下单与到货计划", ("purchase_order_id", "supplier_id", "status", "ordered_at", "expected_at")),
    ("purchase_lines", "供应链", "采购明细", "采购订单商品行", ("line_id", "purchase_order_id", "product_id", "quantity", "unit_cost")),
    ("hr_employees", "HR", "员工档案", "员工组织、岗位和在职状态", ("employee_id", "department", "role", "hire_date", "status")),
    ("hr_attendance", "HR", "考勤记录", "员工打卡与请假记录", ("attendance_id", "employee_id", "work_date", "hours", "leave_type")),
    ("payroll_records", "HR", "薪资记录", "员工月度薪资与奖金", ("payroll_id", "employee_id", "month", "salary", "bonus")),
    ("tax_rates", "税务", "税率配置", "地区与商品税率配置", ("tax_rate_id", "region", "category", "rate")),
    ("audit_logs", "审计", "审计日志", "后台操作与权限审计", ("log_id", "actor", "action", "resource", "created_at")),
    ("schema_versions", "元数据", "Schema 版本", "语义层和表结构版本", ("version_id", "table_name", "version", "changed_at")),
    ("data_quality_runs", "元数据", "质量检查运行", "数据质量规则执行记录", ("run_id", "rule_id", "status", "checked_at")),
    ("data_lineage_edges", "元数据", "血缘边", "表与字段的上游下游关系", ("edge_id", "source_table", "target_table", "column_name")),
    ("marketing_creatives", "营销", "广告素材", "广告素材和审核状态", ("creative_id", "campaign_id", "format", "status")),
    ("marketing_attribution", "营销", "归因明细", "触点与订单归因路径", ("attribution_id", "order_id", "channel", "touch_at", "weight")),
    ("customer_segments", "客户", "客户分群", "客户分群的规则和人数", ("segment_id", "name", "rule", "customer_count")),
    ("customer_service_scores", "客户", "服务评分", "客服满意度评分", ("score_id", "ticket_id", "score", "comment")),
)


def benchmark_tables() -> list[TableInfo]:
    tables = list(CORE_SCHEMA)
    for name, domain, label, description, columns in _DISTRACTORS:
        tables.append(TableInfo(
            name=name,
            label=f"{domain} · {label}",
            description=description,
            grain="row",
            aliases=(domain, label, name),
            columns=tuple(ColumnInfo(c, "TEXT", f"{label}字段 {c}") for c in columns),
        ))
    return tables


# Explicit synthetic foreign keys used by the benchmark closure. These are
# catalog-level relationships only; no warehouse data is generated here.
BENCHMARK_RELATIONSHIPS = (
    ("order_items", "order_id", "orders", "order_id"),
    ("refunds", "order_id", "orders", "order_id"),
    ("orders", "customer_id", "customers", "customer_id"),
    ("shipment_orders", "order_id", "orders", "order_id"),
    ("shipment_events", "shipment_id", "shipment_orders", "shipment_id"),
    ("shipment_orders", "carrier_id", "carriers", "carrier_id"),
    ("coupon_redemptions", "order_id", "orders", "order_id"),
    ("coupon_redemptions", "coupon_id", "coupon_definitions", "coupon_id"),
    ("finance_invoices", "order_id", "orders", "order_id"),
    ("finance_payments", "order_id", "orders", "order_id"),
    ("support_tickets", "order_id", "orders", "order_id"),
    ("support_tickets", "customer_id", "customers", "customer_id"),
    ("support_messages", "ticket_id", "support_tickets", "ticket_id"),
    ("app_events", "customer_id", "customers", "customer_id"),
    ("app_sessions", "customer_id", "customers", "customer_id"),
    ("product_catalog", "product_id", "order_items", "product_id"),
    ("product_suppliers", "product_id", "product_catalog", "product_id"),
    ("purchase_lines", "purchase_order_id", "purchase_orders", "purchase_order_id"),
    ("purchase_lines", "product_id", "product_catalog", "product_id"),
    ("warehouse_inventory", "warehouse_id", "warehouse_locations", "warehouse_id"),
    ("warehouse_inventory", "product_id", "product_catalog", "product_id"),
    ("hr_attendance", "employee_id", "hr_employees", "employee_id"),
    ("payroll_records", "employee_id", "hr_employees", "employee_id"),
    ("data_quality_runs", "rule_id", "quality_rules", "rule_id"),
)


def _relationship_neighbors(table_names: set[str], *, priority: list[str] | None = None, max_tables: int = 8) -> list[str]:
    """Expand a seed set through declared foreign keys, deterministically.

    This is a catalog graph traversal, not a keyword rule. Seeds are expanded
    breadth-first in the order retrieval ranked them, then in the order each
    explicit relationship appears in the synthetic catalog. The cap keeps the
    prompt bounded and makes the reported token comparison reproducible.
    """
    catalog = benchmark_tables()
    known = {table.name for table in catalog}
    adjacency: dict[str, set[str]] = {name: set() for name in known}
    for source, _source_column, target, _target_column in BENCHMARK_RELATIONSHIPS:
        if source in known and target in known:
            adjacency[source].add(target)
            adjacency[target].add(source)
    ordered = [name for name in (priority or []) if name in table_names and name in known]
    ordered.extend(name for name in table_names if name in known and name not in ordered)
    visited: list[str] = []
    queue = list(ordered)
    while queue and len(visited) < max_tables:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.append(current)
        if len(visited) >= max_tables:
            break
        neighbors = sorted(adjacency.get(current, ()))
        queue.extend(name for name in neighbors if name not in visited and name not in queue)
    return visited


BENCHMARK_CASES = (
    {"question": "上个月各渠道销售额是多少？", "expected": {"orders"}},
    {"question": "按投放渠道看 ROI 排名", "expected": {"orders", "marketing_spend"}},
    {"question": "各品类退款率排名", "expected": {"orders", "refunds"}},
    {"question": "商品行销量按品类汇总", "expected": {"order_items", "orders"}},
    {"question": "不同会员等级客户贡献多少销售额", "expected": {"customers", "orders"}},
    {"question": "发货后多久送到，承运商表现如何", "expected": {"shipment_orders", "shipment_events", "carriers"}},
    {"question": "优惠券核销带来的订单金额", "expected": {"coupon_redemptions", "orders"}},
    {"question": "客服工单最多的品类", "expected": {"support_tickets", "orders"}},
    {"question": "App 事件中页面点击转化", "expected": {"app_events", "app_sessions"}},
    {"question": "财务结算各渠道手续费", "expected": {"finance_settlements"}},
    {"question": "采购订单到货延迟", "expected": {"purchase_orders", "purchase_lines"}},
    {"question": "仓库 SKU 安全库存告警", "expected": {"warehouse_inventory", "warehouse_locations"}},
)


def _estimate_tokens(tables: list[TableInfo]) -> int:
    # Deliberately simple, deterministic estimate: one token per four chars.
    text = " ".join(
        " ".join([table.name, table.label, table.description, *[c.name for c in table.columns]])
        for table in tables
    )
    return max(1, math.ceil(len(text) / 4))


def _seed_tables(question: str) -> set[str]:
    q = question.lower()
    seeds = {"orders"}
    groups = [
        (("roi", "投放", "广告", "花费"), {"marketing_spend"}),
        (("退款", "售后", "退货"), {"refunds"}),
        (("商品", "品类", "类目", "销量", "件数"), {"order_items"}),
        (("会员", "客户", "城市"), {"customers"}),
        (("发货", "物流", "承运"), {"shipment_orders", "shipment_events", "carriers"}),
        (("优惠券", "核销"), {"coupon_redemptions"}),
        (("客服", "工单"), {"support_tickets", "support_messages"}),
        (("app", "埋点", "页面", "点击"), {"app_events", "app_sessions"}),
        (("财务", "结算", "手续费"), {"finance_settlements"}),
        (("采购", "到货"), {"purchase_orders", "purchase_lines"}),
        (("库存", "仓库", "sku"), {"warehouse_inventory", "warehouse_locations"}),
    ]
    for terms, names in groups:
        if any(term in q for term in terms):
            seeds.update(names)
    return seeds


def run_large_schema_ablation() -> dict:
    tables = benchmark_tables()
    table_by_name = {table.name: table for table in tables}
    # A synthetic 40-table catalog must never be indexed into a real user's
    # shared Elasticsearch/Qdrant stores. This benchmark is local-IDF only.
    retriever = HybridSchemaRetriever(tables=tables, local_only=True)
    rows = []
    full_hits = 0
    top4_hits = 0
    closure_hits = 0
    seen_top4: set[str] = set()
    seen_closure: set[str] = set()
    top4_token_total = 0.0
    closure_token_spend = 0.0
    same_budget_topk_hits = 0
    same_budget_topk_token_total = 0.0
    for case in BENCHMARK_CASES:
        expected = set(case["expected"])
        # A single Top-4 retrieval is the baseline. The closure arm uses only
        # those retrieved seeds, then expands the declared foreign-key graph.
        # It never adds gold tables or a wider retrieval candidate list.
        candidates = retriever.search(case["question"], limit=4)
        returned = [hit.table for hit in candidates]
        closure = _relationship_neighbors(set(returned), priority=returned, max_tables=10)

        full_recall = expected.issubset(returned)
        closure_recall = expected.issubset(closure)
        closure_tokens = _estimate_tokens(
            [table_by_name[name] for name in dict.fromkeys(closure)]
        )
        ranked = [hit.table for hit in retriever.search(case["question"], limit=len(tables))]
        same_budget: list[str] = []
        for table_name in ranked:
            candidate = list(dict.fromkeys([*same_budget, table_name]))
            if same_budget and _estimate_tokens(
                [table_by_name[name] for name in candidate]
            ) > closure_tokens:
                break
            same_budget = candidate
        same_budget_hit = expected.issubset(same_budget)
        same_budget_topk_hits += int(same_budget_hit)
        same_budget_topk_token_total += _estimate_tokens(
            [table_by_name[name] for name in same_budget]
        )
        full_hits += int(full_recall)
        top4_hits += int(bool(returned) and returned[0] in expected)
        closure_hits += int(closure_recall)
        top4_token_total += _estimate_tokens(
            [table_by_name[name] for name in dict.fromkeys(returned)]
        )
        # Count the complete schema text sent to the model, not just newly
        # admitted tables. This is the only valid basis for a same-budget
        # comparison against pure Top-K retrieval.
        closure_token_spend += _estimate_tokens(
            [table_by_name[name] for name in dict.fromkeys(closure)]
        )
        seen_top4.update(returned)
        seen_closure.update(closure)
        rows.append({
            "question": case["question"],
            "expected_tables": sorted(expected),
            "top4_tables": returned,
            "closure_tables": closure,
            "same_budget_topk_tables": same_budget,
            "same_budget_estimated_tokens": _estimate_tokens(
                [table_by_name[name] for name in same_budget]
            ),
            "full_recall_at_4": full_recall,
            "full_recall_with_closure": closure_recall,
            "full_recall_same_budget_topk": same_budget_hit,
        })
    total = len(rows)
    return {
        "suite_version": "large-schema-retrieval-v1",
        "available": True,
        "total": total,
        "passed": closure_hits,
        "hit_rate": round(closure_hits * 100 / total, 1),
        "table_count": len(tables),
        "case_count": total,
        "full_schema": {"tables": len(tables), "estimated_tokens": _estimate_tokens(tables), "requires_schema_selection": True},
        "retrieved_top4": {"tables": 4, "estimated_tokens": round(top4_token_total / max(1, total), 1), "full_recall_rate": round(full_hits * 100 / total, 1), "top1_hit_rate": round(top4_hits * 100 / total, 1), "unique_tables": len(seen_top4)},
        "closure_top10": {
            "tables": 10,
            "estimated_tokens": round(closure_token_spend / max(1, total), 1),
            "full_recall_rate": round(closure_hits * 100 / total, 1),
            "relationship_edges": len(BENCHMARK_RELATIONSHIPS),
            "unique_tables": len(seen_closure),
            "seed_policy": "top4 retrieval only",
        },
        "same_budget_topk": {
            "estimated_tokens": round(same_budget_topk_token_total / max(1, total), 1),
            "full_recall_rate": round(same_budget_topk_hits * 100 / total, 1),
            "comparison": "pure retrieval ranked to the closure arm's prompt token budget",
        },
        "cases": rows,
        "limitations": "Synthetic catalog and labelled questions; validates schema-selection behavior and relationship closure, not SQL accuracy. Closure uses a declared synthetic foreign-key graph. Same-budget Top-K is the fair comparison; closure is not claimed to improve recall.",
    }
