from __future__ import annotations

from app.nl2sql.models import ColumnInfo, TableInfo


SCHEMA: tuple[TableInfo, ...] = (
    TableInfo(
        name="orders",
        label="订单明细",
        description="电商平台订单主表，一行代表一个订单，包含下单时间、订单状态、渠道、地区、支付金额和优惠金额。",
        aliases=("订单", "销售", "销售额", "GMV", "成交", "营收", "收入", "下单", "渠道", "地区", "华东", "华南", "华北", "西南"),
        grain="order_id",
        columns=(
            ColumnInfo("order_id", "TEXT", "订单唯一编号"),
            ColumnInfo("order_date", "TEXT", "下单日期，格式 YYYY-MM-DD"),
            ColumnInfo("status", "TEXT", "订单状态：paid / refunded / cancelled"),
            ColumnInfo("channel", "TEXT", "销售渠道：App / MiniProgram / Web / Douyin"),
            ColumnInfo("region", "TEXT", "收货地区：华东 / 华南 / 华北 / 西南"),
            ColumnInfo("city", "TEXT", "收货城市"),
            ColumnInfo("category", "TEXT", "商品一级类目"),
            ColumnInfo("product_id", "TEXT", "商品编号"),
            ColumnInfo("quantity", "INTEGER", "购买件数"),
            ColumnInfo("paid_amount", "REAL", "用户实付金额，单位元，已扣除优惠"),
            ColumnInfo("discount_amount", "REAL", "优惠金额，单位元"),
            ColumnInfo("customer_id", "TEXT", "脱敏客户编号", "restricted"),
        ),
    ),
    TableInfo(
        name="order_items",
        label="订单商品行",
        description="订单商品明细，一行代表一个商品行，用于品类、SKU 和件数分析。",
        aliases=("商品", "品类", "SKU", "件数", "销量", "客单价", "连带率"),
        grain="order_id + line_no",
        columns=(
            ColumnInfo("order_id", "TEXT", "订单编号"),
            ColumnInfo("line_no", "INTEGER", "订单内商品行号"),
            ColumnInfo("product_id", "TEXT", "商品编号"),
            ColumnInfo("product_name", "TEXT", "商品名称"),
            ColumnInfo("category", "TEXT", "商品一级类目"),
            ColumnInfo("quantity", "INTEGER", "购买件数"),
            ColumnInfo("unit_price", "REAL", "商品单价，单位元"),
            ColumnInfo("cost_price", "REAL", "商品成本价，单位元", "restricted"),
        ),
    ),
    TableInfo(
        name="refunds",
        label="退款申请",
        description="退款申请与完成记录，一行代表一次退款申请，用于退款率、退款原因和售后分析。",
        aliases=("退款", "退货", "售后", "退款率", "退款原因", "质量问题", "物流慢"),
        grain="refund_id",
        columns=(
            ColumnInfo("refund_id", "TEXT", "退款编号"),
            ColumnInfo("order_id", "TEXT", "原订单编号"),
            ColumnInfo("request_date", "TEXT", "退款申请日期"),
            ColumnInfo("refund_amount", "REAL", "退款金额，单位元"),
            ColumnInfo("reason", "TEXT", "退款原因"),
            ColumnInfo("status", "TEXT", "退款状态：approved / rejected / processing"),
        ),
    ),
    TableInfo(
        name="marketing_spend",
        label="渠道投放费用",
        description="按日期和渠道汇总的广告投放花费、曝光、点击与归因订单，用于 ROI 和获客成本分析。",
        aliases=("投放", "广告", "花费", "ROI", "投产比", "曝光", "点击", "获客成本", "渠道"),
        grain="spend_date + channel",
        columns=(
            ColumnInfo("spend_date", "TEXT", "投放日期"),
            ColumnInfo("channel", "TEXT", "投放渠道"),
            ColumnInfo("campaign", "TEXT", "投放计划名称"),
            ColumnInfo("spend", "REAL", "当日投放花费，单位元"),
            ColumnInfo("impressions", "INTEGER", "曝光次数"),
            ColumnInfo("clicks", "INTEGER", "点击次数"),
            ColumnInfo("attributed_orders", "INTEGER", "归因订单数"),
        ),
    ),
    TableInfo(
        name="customers",
        label="客户画像",
        description="脱敏客户维度表，包含注册日期、会员等级、城市和首单渠道，用于分群与复购分析。",
        aliases=("客户", "用户", "会员", "新客", "老客", "复购", "客群"),
        grain="customer_id",
        columns=(
            ColumnInfo("customer_id", "TEXT", "脱敏客户编号", "restricted"),
            ColumnInfo("register_date", "TEXT", "注册日期"),
            ColumnInfo("member_level", "TEXT", "会员等级：普通 / 银卡 / 金卡"),
            ColumnInfo("city", "TEXT", "所在城市"),
            ColumnInfo("first_channel", "TEXT", "首单渠道"),
        ),
    ),
)


TABLE_BY_NAME = {table.name: table for table in SCHEMA}


def schema_prompt() -> str:
    lines: list[str] = []
    for table in SCHEMA:
        lines.append(f"- {table.name}（{table.label}）: {table.description}")
        for col in table.columns:
            sensitivity = " [敏感，禁止展示]" if col.sensitivity != "public" else ""
            lines.append(f"  - {col.name} {col.type}: {col.description}{sensitivity}")
    return "\n".join(lines)


def allowed_tables() -> set[str]:
    return {table.name for table in SCHEMA}
