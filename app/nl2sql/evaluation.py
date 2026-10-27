from __future__ import annotations

import math
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

from concurrent.futures import ProcessPoolExecutor, as_completed

from app.nl2sql.model_config import get_model_config, model_config_snapshot
from app.nl2sql.result_insight import METRIC_ALIASES, digest_from_result
from app.nl2sql.sql_engine import execute_sql
from app.nl2sql.workflow import run_query


DATASET_RANGE = "2025-10-01 至 2026-02-28"
MODEL_MODE = "model"
SUITE_VERSION = "strict-model-v3-100"
# Evaluation runs the strict model path and never invokes rule SQL fallback.
MODEL_SQL_MODES = {"llm", "llm-repair"}
JAN = "o.order_date BETWEEN '2026-01-01' AND '2026-01-31'"
FEB = "o.order_date BETWEEN '2026-02-01' AND '2026-02-28'"
LAST30 = "o.order_date BETWEEN '2026-01-30' AND '2026-02-28'"
LAST90 = "o.order_date BETWEEN '2025-12-01' AND '2026-02-28'"


def _sales_sql(filters: str = "1=1", dimension: str = "") -> str:
    select = f"o.{dimension} AS {dimension}, " if dimension else ""
    group = f" GROUP BY o.{dimension} ORDER BY 销售额 DESC" if dimension else ""
    return (
        f"SELECT {select}ROUND(SUM(o.paid_amount), 2) AS 销售额 FROM orders o "
        f"WHERE o.status = 'paid' AND {filters}{group}"
    )


def _refund_sql(filters: str = "1=1", top: bool = False) -> str:
    return (
        "SELECT o.category AS category, "
        "ROUND(COUNT(DISTINCT r.refund_id) * 100.0 / COUNT(DISTINCT o.order_id), 2) AS 退款率 "
        "FROM orders o LEFT JOIN refunds r ON r.order_id = o.order_id "
        f"WHERE {filters} GROUP BY o.category ORDER BY 退款率 DESC"
        + (" LIMIT 1" if top else "")
    )


def _roi_sql(start: str, end: str, channel: str = "") -> str:
    # Aggregate each table before joining: daily spend must not be repeated for
    # every matching order. These references never call the runtime generator.
    channel_filter = f" AND m.channel = '{channel}'" if channel else ""
    return (
        "SELECT m.channel AS channel, ROUND(s.revenue / m.spend, 2) AS ROI "
        "FROM (SELECT channel, SUM(spend) AS spend FROM marketing_spend "
        f"WHERE spend_date BETWEEN '{start}' AND '{end}' GROUP BY channel) m "
        "LEFT JOIN (SELECT channel, SUM(paid_amount) AS revenue FROM orders "
        f"WHERE status = 'paid' AND order_date BETWEEN '{start}' AND '{end}' "
        "GROUP BY channel) s ON s.channel = m.channel "
        f"WHERE 1=1{channel_filter} ORDER BY ROI DESC"
    )


def _case(
    case_id: str, question: str, intent: str, reference_sql: str,
    metric: str = "销售额", dimension: str = "", order: str = "",
    setup: str = "",
) -> dict[str, Any]:
    return {
        "id": case_id, "question": question, "intent": intent,
        "reference_sql": reference_sql, "metric": metric,
        "dimension": dimension, "order": order, "setup": setup,
    }


# Relative dates use the demo dataset's reference date, 2026-02-28.
# Wording states the business grain explicitly to avoid ambiguous gold labels.
BASE_CASES = (
    _case("sales-east", "上个月华东地区已支付订单销售额是多少？", "sales_overview",
          _sales_sql(JAN + " AND o.region = '华东'")),
    _case("category-sales", "近90天各品类已支付订单销售额排名，按订单主表品类分组，销售额降序", "category_analysis",
          _sales_sql(LAST90, "category"), dimension="category", order="desc"),
    _case("refund-top", "退款率最高的一个品类是什么？退款率为退款申请单数除以全部订单数，以百分数返回", "refund_analysis",
          _refund_sql(top=True), "退款率", "category"),
    _case("roi-channels", "上个月各渠道ROI：已支付销售额除以投放花费，两表各自按渠道汇总后计算，按ROI降序", "marketing_efficiency",
          _roi_sql("2026-01-01", "2026-01-31"), "ROI", "channel", "desc"),
    _case("daily-sales", "按日看本月已支付订单销售额趋势，日期升序", "sales_overview",
          "SELECT o.order_date AS order_date, ROUND(SUM(o.paid_amount), 2) AS 销售额 "
          f"FROM orders o WHERE o.status = 'paid' AND {FEB} GROUP BY o.order_date ORDER BY o.order_date",
          dimension="order_date", order="date"),
    _case("channel-aov", "各渠道已支付订单客单价，按客单价降序", "sales_overview",
          "SELECT o.channel AS channel, ROUND(AVG(o.paid_amount), 2) AS 客单价 "
          "FROM orders o WHERE o.status = 'paid' GROUP BY o.channel ORDER BY 客单价 DESC",
          "客单价", "channel", "desc"),
    _case("sales-south", "上个月华南地区已支付订单销售额是多少？", "sales_overview",
          _sales_sql(JAN + " AND o.region = '华南'")),
    _case("sales-north-web", "本月华北地区Web渠道已支付订单销售额是多少？", "sales_overview",
          _sales_sql(FEB + " AND o.region = '华北' AND o.channel = 'Web'")),
    _case("sales-last30-channels", "近30天各渠道已支付订单销售额，按销售额降序", "sales_overview",
          _sales_sql(LAST30, "channel"), dimension="channel", order="desc"),
    _case("sales-last90-regions", "近90天各地区已支付订单销售额，按销售额降序", "sales_overview",
          _sales_sql(LAST90, "region"), dimension="region", order="desc"),
    _case("category-units", "近90天各品类已支付商品销量排名，按商品行的品类汇总件数，销量降序", "category_analysis",
          "SELECT oi.category AS category, SUM(oi.quantity) AS 销量 FROM order_items oi "
          f"JOIN orders o ON o.order_id = oi.order_id WHERE o.status = 'paid' AND {LAST90} "
          "GROUP BY oi.category ORDER BY 销量 DESC", "销量", "category", "desc"),
    _case("food-units", "本月食品饮料已支付商品销量是多少？按商品行的品类统计件数", "category_analysis",
          "SELECT SUM(oi.quantity) AS 销量 FROM order_items oi JOIN orders o ON o.order_id = oi.order_id "
          f"WHERE o.status = 'paid' AND {FEB} AND oi.category = '食品饮料'", "销量"),
    _case("refund-month", "上个月各品类退款率，退款申请单数除以全部订单数，以百分数返回并按退款率降序", "refund_analysis",
          _refund_sql(JAN), "退款率", "category", "desc"),
    _case("refund-reasons", "本月各退款原因的退款申请单数，按原订单下单日期筛选，申请单数降序", "refund_analysis",
          "SELECT r.reason AS reason, COUNT(*) AS 退款单数 FROM refunds r "
          f"JOIN orders o ON o.order_id = r.order_id WHERE {FEB} "
          "GROUP BY r.reason ORDER BY 退款单数 DESC", "退款单数", "reason", "desc"),
    _case("roi-douyin", "本月抖音渠道ROI：已支付销售额除以投放花费，两表分别汇总再计算", "marketing_efficiency",
          _roi_sql("2026-02-01", "2026-02-28", "Douyin"), "ROI", "channel"),
    _case("followup-region", "那华南呢？", "sales_overview",
          _sales_sql(JAN + " AND o.region = '华南'"), setup="sales-east"),
    _case("followup-month", "那本月呢？", "sales_overview",
          _sales_sql(FEB + " AND o.region = '华南'"), setup="sales-south"),
    _case("followup-channel", "换成小程序渠道呢？", "sales_overview",
          _sales_sql(FEB + " AND o.region = '华北' AND o.channel = 'MiniProgram'"), setup="sales-north-web"),
)


PERIODS = {
    "jan": ("上个月", "2026-01-01", "2026-01-31", JAN),
    "feb": ("本月", "2026-02-01", "2026-02-28", FEB),
    "last30": ("近30天", "2026-01-30", "2026-02-28", LAST30),
    "last90": ("近90天", "2025-12-01", "2026-02-28", LAST90),
}
REGION_LABELS = ("华东", "华南", "华北", "西南")
CHANNEL_LABELS = (("App", "App"), ("MiniProgram", "小程序"), ("Web", "Web"), ("Douyin", "抖音"))
CATEGORY_LABELS = ("运动鞋服", "食品饮料", "数码家电", "美妆个护")


def _matrix_cases() -> tuple[dict[str, Any], ...]:
    cases: list[dict[str, Any]] = []

    # 14 region/time scalar sales cases. Each combination has a distinct
    # question and exercises a different time boundary and region filter.
    for period_id, (period_label, start, end, _) in PERIODS.items():
        for region in REGION_LABELS:
            case_id = f"matrix-sales-{period_id}-{region}"
            if (period_id == "jan" and region in {"华东", "华南"}):
                continue
            cases.append(_case(
                case_id,
                f"{period_label}{region}地区已支付订单销售额是多少？",
                "sales_overview",
                f"SELECT ROUND(SUM(o.paid_amount), 2) AS 销售额 FROM orders o "
                f"WHERE o.status = 'paid' AND o.order_date BETWEEN '{start}' AND '{end}' AND o.region = '{region}'",
            ))

    # 16 channel/time scalar sales cases, including Chinese channel aliases.
    for period_id, (period_label, start, end, _) in PERIODS.items():
        for channel, label in CHANNEL_LABELS:
            cases.append(_case(
                f"matrix-channel-sales-{period_id}-{channel.lower()}",
                f"{period_label}{label}渠道已支付订单销售额是多少？",
                "sales_overview",
                f"SELECT ROUND(SUM(o.paid_amount), 2) AS 销售额 FROM orders o "
                f"WHERE o.status = 'paid' AND o.order_date BETWEEN '{start}' AND '{end}' AND o.channel = '{channel}'",
            ))

    # Eight order-level category sales cases. The wording fixes the business
    # grain so the model cannot silently switch to order_items category.
    for period_id, (period_label, start, end, _) in PERIODS.items():
        if period_id not in {"jan", "feb"}:
            continue
        for category in CATEGORY_LABELS:
            cases.append(_case(
                f"matrix-category-sales-{period_id}-{category}",
                f"{period_label}{category}订单主表品类的已支付销售额是多少？",
                "category_analysis",
                f"SELECT ROUND(SUM(o.paid_amount), 2) AS 销售额 FROM orders o "
                f"WHERE o.status = 'paid' AND o.order_date BETWEEN '{start}' AND '{end}' AND o.category = '{category}'",
            ))

    # Eight item-level quantity cases, which deliberately use order_items and
    # catch the common mistake of using the order's primary category.
    for period_id, (period_label, start, end, _) in PERIODS.items():
        if period_id not in {"last30", "last90"}:
            continue
        for category in CATEGORY_LABELS:
            cases.append(_case(
                f"matrix-category-units-{period_id}-{category}",
                f"{period_label}{category}商品行的已支付商品销量是多少？按商品明细件数统计",
                "category_analysis",
                f"SELECT SUM(oi.quantity) AS 销量 FROM order_items oi JOIN orders o ON o.order_id = oi.order_id "
                f"WHERE o.status = 'paid' AND o.order_date BETWEEN '{start}' AND '{end}' AND oi.category = '{category}'",
                "销量",
            ))

    # Eight scalar ROI cases supplement the two base ROI shapes.
    for period_id, (period_label, start, end, _) in PERIODS.items():
        if period_id not in {"last30", "last90"}:
            continue
        for channel, label in CHANNEL_LABELS:
            cases.append(_case(
                f"matrix-roi-{period_id}-{channel.lower()}",
                f"{period_label}{label}渠道ROI：已支付销售额除以投放花费，分别汇总两张表后计算",
                "marketing_efficiency",
                _roi_sql(start, end, channel),
                "ROI", "channel",
            ))

    # Refund rates/reasons, AOV and follow-ups balance the scalar matrices.
    for period_id, (period_label, start, end, filters) in PERIODS.items():
        refund_filters = filters + (" AND o.channel = 'App'" if period_id == "jan" else "")
        cases.append(_case(
            f"matrix-refund-rate-{period_id}",
            f"{period_label}{'App渠道' if period_id == 'jan' else ''}各品类退款率，退款申请数除以全部订单数乘100，退款率降序",
            "refund_analysis", _refund_sql(refund_filters), "退款率", "category", "desc",
        ))
        amount = period_id == "feb"
        metric = "退款金额" if amount else "退款单数"
        aggregate = "ROUND(SUM(r.refund_amount), 2)" if amount else "COUNT(*)"
        cases.append(_case(
            f"matrix-refund-reasons-{period_id}",
            f"{period_label}各退款原因的{metric}，按原订单下单日期筛选，{metric}降序",
            "refund_analysis",
            f"SELECT r.reason AS reason, {aggregate} AS {metric} FROM refunds r "
            f"JOIN orders o ON o.order_id = r.order_id WHERE {filters} "
            f"GROUP BY r.reason ORDER BY {metric} DESC", metric, "reason", "desc",
        ))
        for dimension, label in (("channel", "渠道"), ("region", "地区")):
            cases.append(_case(
                f"matrix-aov-{period_id}-{dimension}",
                f"{period_label}各{label}已支付订单客单价，客单价降序",
                "sales_overview",
                f"SELECT o.{dimension} AS {dimension}, ROUND(AVG(o.paid_amount), 2) AS 客单价 "
                f"FROM orders o WHERE o.status = 'paid' AND {filters} "
                f"GROUP BY o.{dimension} ORDER BY 客单价 DESC", "客单价", dimension, "desc",
            ))

    for region in ("华东", "华北", "西南"):
        cases.append(_case(
            f"followup-south-to-{region}", f"同样的上月已支付销售额，改成{region}地区呢？",
            "sales_overview", _sales_sql(JAN + f" AND o.region = '{region}'"), setup="sales-south",
        ))
    for channel, label in (("App", "App"), ("Douyin", "抖音"), ("Web", "网页")):
        cases.append(_case(
            f"followup-app-to-{channel.lower()}", f"保留上月条件，改成{label}渠道的已支付销售额呢？",
            "sales_overview", _sales_sql(JAN + f" AND o.channel = '{channel}'"), setup="matrix-channel-sales-jan-miniprogram",
        ))
    for period_id in ("last30", "last90"):
        label, _, _, filters = PERIODS[period_id]
        cases.append(_case(
            f"followup-period-{period_id}", f"保留华南地区，改为{label}已支付销售额呢？",
            "sales_overview", _sales_sql(filters + " AND o.region = '华南'"), setup="sales-south",
        ))

    # Additional daily and refund-amount result shapes.
    cases.append(_case(
        "matrix-daily-east",
        "本月华东地区按日查看已支付订单销售额趋势，日期升序",
        "sales_overview",
        "SELECT o.order_date AS order_date, ROUND(SUM(o.paid_amount), 2) AS 销售额 "
        f"FROM orders o WHERE o.status = 'paid' AND {FEB} AND o.region = '华东' "
        "GROUP BY o.order_date ORDER BY o.order_date",
        dimension="order_date", order="date",
    ))
    cases.append(_case(
        "matrix-daily-south",
        "上个月华南地区按日查看已支付订单销售额趋势，日期升序",
        "sales_overview",
        "SELECT o.order_date AS order_date, ROUND(SUM(o.paid_amount), 2) AS 销售额 "
        f"FROM orders o WHERE o.status = 'paid' AND {JAN} AND o.region = '华南' "
        "GROUP BY o.order_date ORDER BY o.order_date",
        dimension="order_date", order="date",
    ))
    cases.append(_case(
        "matrix-refund-amount-reasons",
        "上个月各退款原因的退款金额，按退款金额降序",
        "refund_analysis",
        "SELECT r.reason AS reason, ROUND(SUM(r.refund_amount), 2) AS 退款金额 "
        f"FROM refunds r JOIN orders o ON o.order_id = r.order_id WHERE {JAN} "
        "GROUP BY r.reason ORDER BY 退款金额 DESC",
        "退款金额", "reason", "desc",
    ))
    cases.append(_case(
        "matrix-refund-amount-category",
        "本月各品类退款金额，按退款金额降序",
        "refund_analysis",
        "SELECT o.category AS category, ROUND(SUM(r.refund_amount), 2) AS 退款金额 "
        f"FROM refunds r JOIN orders o ON o.order_id = r.order_id WHERE {FEB} "
        "GROUP BY o.category ORDER BY 退款金额 DESC",
        "退款金额", "category", "desc",
    ))
    return tuple(cases)


CASES = BASE_CASES + _matrix_cases()

DIMENSION_ALIASES = {
    "category": {"category", "品类", "类目", "商品品类"},
    "channel": {"channel", "渠道", "销售渠道"},
    "region": {"region", "地区", "区域"},
    "order_date": {"order_date", "date", "日期", "时间", "下单日期"},
    "reason": {"reason", "refund_reason", "退款原因"},
}

METRIC_NAME_ALIASES = {
    "销售额": {"销售额", "总销售额", "销售总额", "成交额", "sales_amount", "revenue", "total_sales", "total_revenue", "total_amount", "sum_sales"},
    "客单价": {"客单价", "平均客单价", "avg_order_value", "average_order_value", "aov"},
    "销量": {"销量", "总销量", "商品销量", "销售件数", "件数", "quantity", "total_quantity", "quantity_sum", "units", "total_units", "units_sold", "sales_volume"},
    "退款率": {"退款率", "refund_rate", "return_rate"},
    "ROI": {"roi", "投产比", "投资回报率"},
    "退款单数": {"退款单数", "退款申请单数", "退款申请数", "退款订单数", "退款数", "refund_count", "refund_orders", "refunds", "total_refunds"},
    "退款金额": {"退款金额", "退款总额", "refund_amount", "refund_total", "total_refund_amount"},
}


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value).strip().lower())


def _column_value(row: dict[str, Any], column: str) -> Any:
    aliases = [column, *sorted(DIMENSION_ALIASES.get(column, set())), *sorted(METRIC_NAME_ALIASES.get(column, set()))]
    aliases.extend(sorted(METRIC_ALIASES.get(column, set())))
    if column == "ROI":
        aliases.extend(sorted(METRIC_ALIASES["roi"]))
    names = {_normalize(alias) for alias in aliases}
    matches = [(key, value) for key, value in row.items() if _normalize(key) in names]
    if not matches:
        # Common scalar aliases emitted by SQLite or a model when no explicit
        # business alias is supplied. They are accepted only for scalar cases.
        scalar_aliases = {
            "销售额": {"total", "value", "result", "sum"},
            "销量": {"total", "value", "result", "sum"},
            "退款单数": {"count", "total_count"},
        }
        scalar_names = {_normalize(alias) for alias in scalar_aliases.get(column, set())}
        matches = [(key, value) for key, value in row.items() if _normalize(key) in scalar_names]
    if not matches:
        raise ValueError(f"缺少或无法唯一识别验收列：{column}")
    if len(matches) > 1:
        try:
            values = [float(value) for _, value in matches]
            if not all(math.isclose(values[0], value, rel_tol=0, abs_tol=0.010001) for value in values[1:]):
                raise ValueError(f"等价验收列的数值不一致：{column}")
        except (TypeError, ValueError):
            if len({str(value) for _, value in matches}) != 1:
                raise ValueError(f"等价验收列的值不一致：{column}")
    return matches[0][1]


def _dimension_value(value: Any, dimension: str) -> str:
    text = str(value)
    if dimension == "channel":
        return {"小程序": "MiniProgram", "抖音": "Douyin", "网页": "Web"}.get(text, text).lower()
    return text


def _check_rows(case: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """Compare model output with an independent, read-only business reference.

    Extra columns and recognized SQL aliases are allowed. Missing/duplicate
    groups, incorrect numbers and incorrect ordering are not. Rates are in
    percentage points; all numeric tolerances are absolute (0.01).
    """
    expected = execute_sql(case["reference_sql"])
    if expected["truncated"]:
        return "验收基准被行数上限截断"
    gold = expected["rows"]
    if not gold or len(rows) != len(gold):
        return f"结果行数不符：期望 {len(gold)}，实际 {len(rows)}"
    metric, dimension = case["metric"], case["dimension"]
    try:
        expected_values = {
            _dimension_value(row[dimension], dimension) if dimension else "total": float(row[metric])
            for row in gold
        }
        scalar_dimension_allowed = (
            bool(dimension)
            and len(gold) == 1
            and case["intent"] == "marketing_efficiency"
        )
        seen: set[str] = set()
        values: list[float] = []
        dimensions: list[str] = []
        for row in rows:
            if dimension:
                try:
                    key = _dimension_value(_column_value(row, dimension), dimension)
                except ValueError as exc:
                    if not scalar_dimension_allowed or not str(exc).startswith("缺少或无法唯一识别验收列"):
                        raise
                    key = "__scalar__"
            else:
                key = "total"
            expected_key = key
            if key == "__scalar__":
                expected_key = next(iter(expected_values))
            if expected_key not in expected_values or key in seen:
                return f"分组缺失、重复或不匹配：{key}"
            seen.add(key)
            raw = _column_value(row, metric)
            if raw is None or isinstance(raw, bool):
                return f"{key} 的{metric}不是有效数值"
            value = float(raw)
            if not math.isfinite(value) or not math.isclose(value, expected_values[expected_key], rel_tol=0, abs_tol=0.010001):
                return f"{expected_key} 的{metric}不符：期望 {expected_values[expected_key]:g}，实际 {value:g}"
            values.append(value)
            dimensions.append(key)
        if case["order"] == "desc" and values != sorted(values, reverse=True):
            return f"{metric}未按降序排列"
        if case["order"] == "date" and dimensions != sorted(dimensions):
            return "日期未按升序排列"
    except (TypeError, ValueError) as exc:
        return str(exc)
    return ""


def _model_state() -> dict[str, Any]:
    config = get_model_config()
    return {
        "configured": bool(config.enabled),
        "active": bool(config.enabled and config.active),
        "model": config.model if config.enabled else "",
        "provider": config.provider if config.enabled else "",
    }


def _model_grounded(result: Any) -> bool:
    if not result or not result.rows:
        return False
    digest = digest_from_result(result)
    return bool(digest.get("facts")) and digest.get("row_count", 0) > 0


def _result_error(result: Any, case: dict[str, Any]) -> str:
    if result.mode == "model-failed":
        return "模型未生成 SQL；严格模型评测未使用其他生成器补位"
    if result.mode == "model-repair-failed":
        return "模型修复未生成 SQL；严格模型评测未使用其他生成器补位"
    if not result.attempts or result.attempts[-1]["status"] != "success":
        return "模型 SQL 未成功执行"
    if result.mode not in MODEL_SQL_MODES:
        return "该用例未由模型完成 SQL 生成"
    if getattr(result, "narrative_mode", "") != "model":
        return "模型解读回退为确定性摘要，严格模型评测不通过"
    if not str(getattr(result, "answer", "") or "").strip():
        return "模型解读为空，严格模型评测不通过"
    mismatch = _check_rows(case, result.rows)
    if mismatch:
        return mismatch
    if not _model_grounded(result):
        return "模型结果缺少可核对的查询摘要"
    return ""


def _diagnostics(cases: list[dict[str, Any]]) -> dict[str, Any]:
    by_intent = []
    for intent in sorted({case["intent"] for case in cases}):
        items = [case for case in cases if case["intent"] == intent]
        passed = sum(bool(item["hit"]) for item in items)
        by_intent.append({"intent": intent, "total": len(items), "passed": passed,
                          "hit_rate": round(passed * 100 / len(items), 1)})
    failures: dict[str, int] = {}
    for case in cases:
        if case["hit"]:
            continue
        error = case.get("error") or ""
        if error.startswith("前置问题"):
            category = "setup"
        elif case.get("sql_mode") == "unavailable":
            category = "unavailable"
        elif case.get("sql_mode") in {"model-failed", "model-repair-failed"}:
            category = "generation"
        elif not case.get("executed"):
            category = "execution"
        elif case.get("narrative_mode") != "model" or "解读" in error:
            category = "narrative"
        else:
            category = "result"
        failures[category] = failures.get(category, 0) + 1
    repaired = [case for case in cases if case.get("repair_used") or case.get("sql_mode") == "llm-repair"]
    return {
        "by_intent": by_intent, "failures": failures,
        "self_healing": {"attempted_cases": len(repaired),
                         "passed_cases": sum(bool(case["hit"]) for case in repaired)},
    }




def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    error = ""
    results = []
    result = None
    warnings: list[str] = []
    executed = False
    model_used = False
    hit = False
    setup = next((item for item in CASES if item["id"] == case["setup"]), None)
    try:
        context = None
        for step in ([setup, case] if setup else [case]):
            result = run_query(step["question"], context=context, allow_rule_fallback=False)
            results.append(result)
            warnings.extend(result.warnings)
            error = _result_error(result, step)
            if error:
                if step is setup:
                    error = "前置问题未通过：" + error
                break
            context = {**result.plan.as_dict(), **result.plan.context, "question": step["question"]}
        complete = len(results) == (2 if setup else 1)
        executed = complete and all(item.attempts and item.attempts[-1]["status"] == "success" for item in results)
        model_used = complete and all(item.mode in MODEL_SQL_MODES and getattr(item, "narrative_mode", "") == "model" for item in results)
        hit = not error
    except Exception as exc:
        error = str(exc)

    elapsed = (time.perf_counter() - started) * 1000

    return {
            "id": case["id"],
            "setup_question": setup["question"] if setup else None,
            "question": case["question"],
            "intent": case["intent"],
            "executed": executed,
            "hit": hit,
            "model_used": model_used,
            "narrative_mode": getattr(result, "narrative_mode", "unavailable") if result else "error",
            "elapsed_ms": round(elapsed, 1),
            "attempts": sum(len(item.attempts) for item in results),
            "generation_retries": sum(getattr(item, "generation_retries", 0) for item in results),
            "narrative_retries": sum(getattr(item, "narrative_retries", 0) for item in results),
            "repair_used": any(item.mode in {"llm-repair", "model-repair-failed"}
                               or getattr(item, "generation_retries", 0)
                               or getattr(item, "narrative_retries", 0)
                               or any(attempt.get("status") == "failed" for attempt in item.attempts)
                               for item in results),
            "steps": [
                {"question": item.question, "sql": item.sql, "columns": item.columns,
                 "row_count": len(item.rows), "sample_rows": item.rows[:5],
                 "sql_mode": item.mode, "narrative_mode": item.narrative_mode,
                 "attempts": item.attempts, "generation_retries": getattr(item, "generation_retries", 0),
                 "narrative_retries": getattr(item, "narrative_retries", 0)}
                for item in results if hasattr(item, "sql")
            ],
            "sql_mode": result.mode if result else "error",
            "warnings": warnings,
            "error": error or None,
    }


def _evaluate_with_config(case, config):
    with model_config_snapshot(config):
        return _evaluate_case(case)


def run_evaluation(on_case: Callable[[dict[str, Any]], None] | None = None, *, workers: int = 1) -> dict[str, Any]:
    """Run every golden case through the strict model path.

    The evaluation never invokes the runtime rule fallback. If the model does
    not return SQL, the case is recorded as a model failure instead of being
    masked by a deterministic query.
    """
    if workers not in (1, 2, 3):
        raise ValueError("workers must be 1, 2 or 3")
    model = _model_state()
    cases: list[dict[str, Any]] = []
    executed_count = 0
    hit_count = 0
    total_elapsed = 0.0

    if not model["active"]:
        total = len(CASES)
        report = {
            "suite_version": SUITE_VERSION,
            "mode": MODEL_MODE,
            "model": model,
            "available": False,
            "dataset_range": DATASET_RANGE,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "total": total,
            "passed": 0,
            "execution_rate": 0.0,
            "hit_rate": 0.0,
            "model_rate": 0.0,
            "avg_elapsed_ms": 0.0,
            "intents": len({case["intent"] for case in CASES}),
            "cases": [
                {
                    "id": case["id"],
                    "setup_question": next((item["question"] for item in CASES if item["id"] == case["setup"]), None),
                    "question": case["question"],
                    "intent": case["intent"],
                    "executed": False,
                    "hit": False,
                    "model_used": False,
                    "narrative_mode": "unavailable",
                    "elapsed_ms": 0.0,
                    "attempts": 0,
                    "sql_mode": "unavailable",
                    "warnings": ["模型未配置或未启用"],
                    "error": "模型未配置或未启用，评测未运行",
                }
                for case in CASES
            ],
        }
        report.update(_diagnostics(report["cases"]))
        return report

    config = get_model_config()
    if workers == 1:
        for case in CASES:
            item = _evaluate_with_config(case, config)
            cases.append(item)
            if on_case:
                on_case(item)
    else:
        ordered = [None] * len(CASES)
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_evaluate_with_config, case, config): index for index, case in enumerate(CASES)}
            for future in as_completed(futures):
                item = future.result()
                ordered[futures[future]] = item
                if on_case:
                    on_case(item)
        cases = ordered
    executed_count = sum(bool(case['executed']) for case in cases)
    hit_count = sum(bool(case['hit']) for case in cases)
    total_elapsed = sum(case['elapsed_ms'] for case in cases)

    total = len(cases)
    model_count = sum(1 for case in cases if case["model_used"])
    return {
        **_diagnostics(cases),
        "suite_version": SUITE_VERSION,
        "mode": MODEL_MODE,
        "model": model,
        "available": True,
        "dataset_range": DATASET_RANGE,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "passed": hit_count,
        "execution_rate": round(executed_count * 100 / total, 1),
        "hit_rate": round(hit_count * 100 / total, 1),
        "model_rate": round(model_count * 100 / total, 1),
        "avg_elapsed_ms": round(total_elapsed / total, 1),
        "intents": len({case["intent"] for case in CASES}),
        "cases": cases,
    }
