from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from app.nl2sql.settings import get_settings


PRODUCTS = (
    ("P001", "云感跑步鞋", "运动鞋服", 399.0, 155.0),
    ("P002", "轻量防风外套", "运动鞋服", 529.0, 208.0),
    ("P003", "高弹训练裤", "运动鞋服", 269.0, 96.0),
    ("P004", "冷萃咖啡豆", "食品饮料", 89.0, 34.0),
    ("P005", "低糖燕麦棒", "食品饮料", 49.0, 17.0),
    ("P006", "山野乌龙茶", "食品饮料", 119.0, 46.0),
    ("P007", "智能体脂秤", "数码家电", 199.0, 88.0),
    ("P008", "降噪蓝牙耳机", "数码家电", 699.0, 302.0),
    ("P009", "便携榨汁杯", "数码家电", 159.0, 62.0),
    ("P010", "氨基酸洁面", "美妆个护", 109.0, 31.0),
    ("P011", "修护面霜", "美妆个护", 239.0, 72.0),
    ("P012", "头皮护理精华", "美妆个护", 169.0, 49.0),
)

CHANNELS = ("App", "MiniProgram", "Web", "Douyin")
REGIONS = {
    "华东": ("上海", "杭州", "南京", "苏州"),
    "华南": ("广州", "深圳", "厦门"),
    "华北": ("北京", "天津", "青岛"),
    "西南": ("成都", "重庆", "昆明"),
}
REFUND_REASONS = ("尺码不合适", "质量问题", "物流太慢", "不喜欢", "商品与描述不符")
MEMBER_LEVELS = ("普通", "银卡", "金卡")


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS refunds;
        DROP TABLE IF EXISTS order_items;
        DROP TABLE IF EXISTS orders;
        DROP TABLE IF EXISTS marketing_spend;
        DROP TABLE IF EXISTS customers;

        CREATE TABLE orders (
            order_id TEXT PRIMARY KEY,
            order_date TEXT NOT NULL,
            status TEXT NOT NULL,
            channel TEXT NOT NULL,
            region TEXT NOT NULL,
            city TEXT NOT NULL,
            category TEXT NOT NULL,
            product_id TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            paid_amount REAL NOT NULL,
            discount_amount REAL NOT NULL,
            customer_id TEXT NOT NULL
        );

        CREATE TABLE order_items (
            order_id TEXT NOT NULL,
            line_no INTEGER NOT NULL,
            product_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            category TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price REAL NOT NULL,
            cost_price REAL NOT NULL,
            PRIMARY KEY (order_id, line_no)
        );

        CREATE TABLE refunds (
            refund_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            request_date TEXT NOT NULL,
            refund_amount REAL NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL
        );

        CREATE TABLE marketing_spend (
            spend_date TEXT NOT NULL,
            channel TEXT NOT NULL,
            campaign TEXT NOT NULL,
            spend REAL NOT NULL,
            impressions INTEGER NOT NULL,
            clicks INTEGER NOT NULL,
            attributed_orders INTEGER NOT NULL,
            PRIMARY KEY (spend_date, channel)
        );

        CREATE TABLE customers (
            customer_id TEXT PRIMARY KEY,
            register_date TEXT NOT NULL,
            member_level TEXT NOT NULL,
            city TEXT NOT NULL,
            first_channel TEXT NOT NULL
        );
        """
    )


def generate_database(path: Path | None = None, seed: int = 20260214) -> dict[str, int]:
    settings = get_settings()
    db_path = path or settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    start = date(2025, 10, 1)
    end = date(2026, 2, 28)

    customers = []
    for idx in range(1, 1801):
        city = rng.choice([city for cities in REGIONS.values() for city in cities])
        register = start - timedelta(days=rng.randint(0, 420))
        customers.append(
            (
                f"C{idx:05d}",
                register.isoformat(),
                rng.choices(MEMBER_LEVELS, weights=[65, 25, 10], k=1)[0],
                city,
                rng.choice(CHANNELS),
            )
        )

    conn = sqlite3.connect(db_path)
    try:
        _create_tables(conn)
        conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?)", customers)
        orders = []
        items = []
        refunds = []
        order_no = 1
        refund_no = 1
        current = start
        while current <= end:
            seasonal = 1.35 if current.month in (11, 12) else 1.0
            daily_orders = int(rng.gauss(26 * seasonal, 5))
            for _ in range(max(8, daily_orders)):
                customer = rng.choice(customers)
                region = next((r for r, cities in REGIONS.items() if customer[3] in cities), "华东")
                channel = rng.choices(CHANNELS, weights=[42, 24, 19, 15], k=1)[0]
                order_id = f"O{order_no:07d}"
                order_no += 1
                line_count = rng.choices([1, 2], weights=[74, 26], k=1)[0]
                selected = rng.sample(PRODUCTS, line_count)
                gross = 0.0
                order_items = []
                for line_no, product in enumerate(selected, start=1):
                    product_id, product_name, category, price, cost = product
                    qty = rng.choices([1, 2, 3], weights=[76, 19, 5], k=1)[0]
                    gross += price * qty
                    order_items.append((order_id, line_no, product_id, product_name, category, qty, price, cost))
                discount = round(gross * rng.choice([0.0, 0.05, 0.1, 0.15]), 2)
                paid = round(gross - discount, 2)
                status = rng.choices(["paid", "refunded", "cancelled"], weights=[91, 6, 3], k=1)[0]
                primary = selected[0]
                orders.append(
                    (
                        order_id,
                        current.isoformat(),
                        status,
                        channel,
                        region,
                        customer[3],
                        primary[2],
                        primary[0],
                        sum(item[5] for item in order_items),
                        paid,
                        discount,
                        customer[0],
                    )
                )
                items.extend(order_items)
                if status == "refunded":
                    refund_amount = round(paid * rng.choice([0.3, 0.5, 1.0]), 2)
                    refunds.append(
                        (
                            f"R{refund_no:06d}",
                            order_id,
                            (current + timedelta(days=rng.randint(1, 7))).isoformat(),
                            refund_amount,
                            rng.choices(REFUND_REASONS, weights=[28, 24, 18, 16, 14], k=1)[0],
                            "approved" if rng.random() > 0.12 else "processing",
                        )
                    )
                    refund_no += 1
            current += timedelta(days=1)

        conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", orders)
        conn.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?, ?, ?, ?, ?)", items)
        conn.executemany("INSERT INTO refunds VALUES (?, ?, ?, ?, ?, ?)", refunds)

        spend_rows = []
        current = start
        while current <= end:
            for channel in CHANNELS:
                base = {"App": 1800, "MiniProgram": 1200, "Web": 900, "Douyin": 2600}[channel]
                spend = round(base * rng.uniform(0.75, 1.3), 2)
                impressions = int(spend * rng.uniform(22, 38))
                clicks = int(impressions * rng.uniform(0.025, 0.06))
                attributed = int(clicks * rng.uniform(0.008, 0.018))
                spend_rows.append(
                    (current.isoformat(), channel, f"{channel}-品牌拉新", spend, impressions, clicks, attributed)
                )
            current += timedelta(days=1)
        conn.executemany("INSERT INTO marketing_spend VALUES (?, ?, ?, ?, ?, ?, ?)", spend_rows)
        conn.commit()
    finally:
        conn.close()

    return {
        "customers": len(customers),
        "orders": len(orders),
        "order_items": len(items),
        "refunds": len(refunds),
        "marketing_spend": len(spend_rows),
    }


if __name__ == "__main__":
    print(generate_database())
