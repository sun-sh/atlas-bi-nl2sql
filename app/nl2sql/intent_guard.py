from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


OUTCOME_ANSWERED = "answered"
OUTCOME_CLARIFICATION = "clarification"
OUTCOME_UNSUPPORTED = "unsupported"

_METRIC_TERMS = (
    "销售额", "销量", "客单价", "退款率", "退款金额", "退款单数",
    "roi", "投产比", "投放花费", "归因订单数", "订单数", "订单量",
    "gmv", "营收", "收入", "最赚钱", "赚钱", "回报", "值不值",
    "卖得最多", "卖得多", "每单平均", "平均花", "卖得怎么样",
    "售后占比",
)
_DIMENSION_TERMS = (
    "渠道", "地区", "区域", "品类", "类目", "商品", "城市", "会员",
    "时间", "日期", "趋势", "排名",
)
_GENERIC_BUSINESS_TERMS = (
    "生意", "经营", "表现", "情况", "分析一下", "怎么样", "好不好",
)
_OUT_OF_DOMAIN_TERMS = (
    "天气", "股票", "基金", "新闻", "翻译", "笑话", "菜谱", "旅游",
    "写代码", "python", "java", "javascript", "sql注入", "电影",
    "满意度", "投诉", "客服", "库存", "供应商", "员工", "财务", "税务",
)
_SENSITIVE_TERMS = (
    "手机号", "身份证", "密码", "密钥", "token", "api key", "个人信息",
    "银行卡", "住址", "客户编号", "顾客编号", "成本价",
)
_MUTATION_RE = re.compile(
    r"(删除|删掉|清空|更新|修改|改掉|改成|改为|新增|插入|写入|导出|下载|"
    r"drop|delete|update|insert|alter|truncate|create|attach|vacuum)",
    re.I,
)
_CAUSAL_RE = re.compile(r"(为什么|为何|原因|导致|因为).*(下降|上升|增长|减少|变化|波动)", re.I)


def _has_metric(text: str) -> bool:
    q = text.lower()
    if any(term in q for term in _METRIC_TERMS):
        return True
    # “退款原因/退款分布”本身就是一个明确的分析指标。
    return "退款" in q and any(term in q for term in ("原因", "分布", "单数", "申请数"))


def _context_has_metric(context: dict[str, Any] | None) -> bool:
    if not context:
        return False
    return bool(context.get("metrics") or context.get("question"))


def _suggestions(kind: str) -> list[str]:
    if kind == OUTCOME_UNSUPPORTED:
        return [
            "当前系统只提供只读的电商经营数据查询，不能新增、修改或删除数据。",
            "可以问：上个月各渠道销售额是多少？",
        ]
    if kind == OUTCOME_CLARIFICATION:
        return [
            "请补充指标，例如销售额、销量、退款率、客单价或 ROI。",
            "也可以补充时间范围和地区、渠道、品类等筛选条件。",
        ]
    return []


@dataclass(frozen=True)
class QuestionDecision:
    outcome: str = OUTCOME_ANSWERED
    reason_code: str = ""
    message: str = ""
    suggestions: list[str] = field(default_factory=list)

    @property
    def answerable(self) -> bool:
        return self.outcome == OUTCOME_ANSWERED

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reason_code": self.reason_code,
            "message": self.message,
            "suggestions": list(self.suggestions),
        }


def assess_question(question: str, context: dict[str, Any] | None = None) -> QuestionDecision:
    """Decide whether a question belongs in the safe, bounded SQL path.

    This is deliberately conservative about destructive and out-of-domain
    requests, but keeps follow-up questions answerable when the previous turn
    already established a metric and filters.
    """
    text = " ".join(str(question or "").split())
    if not text:
        return QuestionDecision(
            OUTCOME_CLARIFICATION,
            "empty_question",
            "请先输入一个具体的经营问题。",
            _suggestions(OUTCOME_CLARIFICATION),
        )

    q = text.lower()
    context_metrics = _context_has_metric(context)

    if any(term in q for term in _SENSITIVE_TERMS):
        return QuestionDecision(
            OUTCOME_UNSUPPORTED,
            "sensitive_field",
            "这个请求涉及敏感字段，当前只读分析服务不会读取或展示这类信息。",
            _suggestions(OUTCOME_UNSUPPORTED),
        )

    if (
        ("广告" in q and any(term in q for term in ("曝光", "点击")))
        or ("点击" in q and any(term in q for term in ("下单", "转化", "成交", "购买")))
    ):
        return QuestionDecision(
            OUTCOME_CLARIFICATION,
            "missing_metric",
            "曝光、点击和转化是不同链路指标，当前固定口径只支持投放花费、归因订单数和 ROI。请指定要看投放花费、归因订单数还是 ROI。",
            _suggestions(OUTCOME_CLARIFICATION),
        )

    if any(term in q for term in _OUT_OF_DOMAIN_TERMS):
        return QuestionDecision(
            OUTCOME_UNSUPPORTED,
            "out_of_domain",
            "这个问题不在当前电商经营数据的分析范围内。",
            _suggestions(OUTCOME_UNSUPPORTED),
        )

    if _MUTATION_RE.search(q) and any(
        term in q
        for term in ("表", "订单", "数据", "记录", "数据库", "字段", "用户", "客户", "商品", "退款")
    ):
        return QuestionDecision(
            OUTCOME_UNSUPPORTED,
            "write_request",
            "我不能执行写操作。当前 SQL 链路只允许单条只读 SELECT，不会修改任何数据。",
            _suggestions(OUTCOME_UNSUPPORTED),
        )

    if (
        ("退款" in q and any(term in q for term in ("原因", "分布")))
        or ("退的钱" in q and any(term in q for term in ("原因", "哪些", "花在")))
        or ("广告" in q and "值不值" in q)
        or ("售后" in q and any(term in q for term in ("占比", "比例", "多大")))
        or ("卖得怎么样" in q and any(term in q for term in ("每天", "按日", "趋势")))
    ):
        return QuestionDecision()

    if _CAUSAL_RE.search(text):
        return QuestionDecision(
            OUTCOME_CLARIFICATION,
            "causal_question",
            "我可以查询趋势和拆分维度，但不能仅凭当前 SQL 结果断言因果原因。请说明想拆解的时间、渠道或品类。",
            _suggestions(OUTCOME_CLARIFICATION),
        )

    has_metric = _has_metric(text)
    has_dimension = any(term in q for term in _DIMENSION_TERMS)
    generic = any(term in q for term in _GENERIC_BUSINESS_TERMS)
    vague_combined_concepts = (
        ("单价" in q and any(term in q for term in ("卖得", "销量")))
        or (
            any(term in q for term in ("新客", "新客户"))
            and any(term in q for term in ("老客", "老客户"))
        )
    )

    if vague_combined_concepts:
        return QuestionDecision(
            OUTCOME_CLARIFICATION,
            "missing_metric",
            "这个问题混合了多个指标或客户分层口径。请指定要先看销售额、销量、客单价还是客户数。",
            _suggestions(OUTCOME_CLARIFICATION),
        )

    if not has_metric and not context_metrics and (generic or has_dimension):
        return QuestionDecision(
            OUTCOME_CLARIFICATION,
            "missing_metric",
            "这个问题还缺少明确的业务指标。请补充要看的指标，例如销售额、销量、退款率、客单价或 ROI。",
            _suggestions(OUTCOME_CLARIFICATION),
        )

    # A completely open-ended business question should not silently become a
    # sales query. Explicit metric questions continue on the normal path.
    if not has_metric and not context_metrics and not any(
        term in q
        for term in ("销售额", "销量", "退款", "订单", "渠道", "地区", "品类")
    ):
        return QuestionDecision(
            OUTCOME_CLARIFICATION,
            "missing_scope",
            "我还不能确定要分析的指标或范围。请给出指标、时间或维度中的至少一项。",
            _suggestions(OUTCOME_CLARIFICATION),
        )

    return QuestionDecision()
