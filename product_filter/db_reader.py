"""
数据库读取：从 ozon_selection 库的 sku_products 表中读取产品数据
"""

from decimal import Decimal
from typing import Any, Generator, Optional

from .config import settings

try:
    import pymysql
    _HAS_PYMYSQL = True
except ImportError:
    _HAS_PYMYSQL = False


def _get_connection():
    """建立数据库连接（含公网防御性编程配置）"""
    if not _HAS_PYMYSQL:
        raise ImportError("pymysql 未安装，请执行: pip install pymysql")

    return pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
        charset="utf8mb4",
        connect_timeout=60000,
        read_timeout=600,
        write_timeout=600,
        cursorclass=pymysql.cursors.DictCursor,
    )


# ── FBS 过滤 SQL ──
# 筛选条件: 有重量、有价格、FBS 发货模式、非品牌
FBS_PRODUCTS_SQL = """
    SELECT
        p.sku,
        p.title,
        p.price,
        p.card_price,
        p.original_price,
        p.currency,
        p.custom_weight_g,
        p.category,
        p.sold_count,
        p.sales_schema
    FROM sku_products p
    INNER JOIN sku_plugin_metrics m ON p.sku = m.sku
    WHERE
        p.custom_weight_g IS NOT NULL
        AND p.custom_weight_g > 0
        AND p.price IS NOT NULL
        AND p.price > 0
        AND (m.sales_schema LIKE '%FBS%' OR m.sales_schema LIKE '%fbs%')
        AND (p.brand IS NULL OR p.brand = '' OR LOWER(p.brand) IN ('无品牌','未填写品牌','无','none','no brand','нет бренда','без бренда'))
    ORDER BY p.sold_count DESC
"""


def fetch_products(
    limit: Optional[int] = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """分页获取符合条件的 FBS 产品"""
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            sql = FBS_PRODUCTS_SQL
            if limit is not None:
                sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
            cur.execute(sql)
            rows = cur.fetchall()
        return rows
    finally:
        conn.close()


def fetch_product_by_sku(sku: str) -> Optional[dict[str, Any]]:
    """按 SKU 获取单个产品"""
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT sku, title, price, card_price, original_price,
                          currency, custom_weight_g, category, sold_count, sales_schema
                   FROM sku_products WHERE sku = %s""",
                (sku,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def fetch_products_stream(
    batch_size: int = 1000,
) -> Generator[list[dict[str, Any]], None, None]:
    """
    流式分批读取全部 FBS 产品，避免一次性全量加载撑爆内存/带宽
    每次 yield 一个 batch
    """
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(FBS_PRODUCTS_SQL)
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                yield rows
    finally:
        conn.close()
