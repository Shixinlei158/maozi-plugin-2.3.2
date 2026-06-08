"""数据仓库层 —— 精简版。

提供四张核心表的CRUD操作：
- ozon_categories: 类目树读写
- seed_pool_skus: 榜单种子池读写
- seller_shops: 卖家表读写（含来源追踪）
- sku_products: 合格SKU表读写
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from . import db
from .config import settings
from .rules import evaluate_selection_rule


# ============================================================
# 工具函数
# ============================================================
def seller_key(home_url: str) -> str:
    return hashlib.sha1(home_url.strip().rstrip("/").encode()).hexdigest()


def to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def json_dumps(obj: Any) -> str | None:
    if obj is None:
        return None
    return json.dumps(obj, ensure_ascii=False)


def percent_text_to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip().replace("%", "").replace("％", "")
    return to_decimal(text)


def grams_from_text(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    for unit in ("г", "g", "克"):
        text = text.replace(unit, "")
    return to_decimal(text)


def query_key_from_filters(filters: dict[str, Any]) -> str:
    normalized = json.dumps(filters, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


# ============================================================
# ozon_categories
# ============================================================
def list_categories_by_level(level: int) -> list[dict[str, Any]]:
    return db.fetch_all(
        "SELECT category_id, name_zh, name_en, parent_id, level FROM ozon_categories WHERE level=%(level)s ORDER BY category_id",
        {"level": level},
    )


def upsert_category(
    category_id: int,
    name_zh: str = "",
    name_en: str = "",
    parent_id: int = 0,
    level: int = 1,
) -> int:
    return db.execute(
        """
        INSERT INTO ozon_categories (category_id, name_zh, name_en, parent_id, level)
        VALUES (%(category_id)s, %(name_zh)s, %(name_en)s, %(parent_id)s, %(level)s)
        ON DUPLICATE KEY UPDATE
          name_zh=IF(VALUES(name_zh) != '', VALUES(name_zh), name_zh),
          name_en=IF(VALUES(name_en) != '', VALUES(name_en), name_en),
          parent_id=IF(VALUES(parent_id) != 0, VALUES(parent_id), parent_id)
        """,
        {"category_id": category_id, "name_zh": name_zh, "name_en": name_en, "parent_id": parent_id, "level": level},
    )


def bulk_upsert_categories_from_items(items: list[dict[str, Any]]) -> int:
    """从榜单商品列表中提取类目ID，批量写入 ozon_categories（自增机制）

    每页榜单数据都会携带 cate1_id/cate2_id/cate3_id，本函数提取去重后
    批量 upsert 到类目树表，实现采集中类目的自动增长。
    返回写入记录数。
    """
    from . import db as _db_module

    if not items:
        return 0

    category_rows: list[dict[str, Any]] = []
    seen_categories: set[int] = set()
    for item in items:
        for c_id, c_name_zh, c_name_en, c_parent, c_level in [
            (item.get("cate1_id"), item.get("cate1") or "", item.get("category1") or "", 0, 1),
            (item.get("cate2_id"), item.get("cate2") or "", item.get("category2") or "", item.get("cate1_id") or 0, 2),
            (item.get("cate3_id"), item.get("cate3") or "", item.get("category3") or "", item.get("cate2_id") or 0, 3),
        ]:
            if c_id and int(c_id) not in seen_categories:
                seen_categories.add(int(c_id))
                category_rows.append({
                    "category_id": int(c_id),
                    "name_zh": c_name_zh or "",
                    "name_en": c_name_en or "",
                    "parent_id": int(c_parent) if c_parent else 0,
                    "level": c_level,
                })

    if not category_rows:
        return 0

    return _db_module.execute_insert_many(
        "INSERT INTO ozon_categories (category_id, name_zh, name_en, parent_id, level) "
        "VALUES (%(category_id)s, %(name_zh)s, %(name_en)s, %(parent_id)s, %(level)s) "
        "ON DUPLICATE KEY UPDATE "
        "name_zh=IF(VALUES(name_zh)!='',VALUES(name_zh),name_zh), "
        "name_en=IF(VALUES(name_en)!='',VALUES(name_en),name_en), "
        "parent_id=IF(VALUES(parent_id)!=0,VALUES(parent_id),parent_id)",
        category_rows,
        batch_size=200,
    )


# ============================================================
# seed_pool_skus
# ============================================================
def bulk_upsert_seed_pool(items: list[dict[str, Any]], query_key: str) -> int:
    """批量写入榜单种子池"""
    rows = []
    for item in items:
        sku = str(item.get("sku") or "").strip()
        if not sku:
            continue
        fingerprint = {
            "sku": sku,
            "brand": item.get("brand"),
            "sold_count": to_int(item.get("sold_count")),
            "sold_sum": str(to_decimal(item.get("sold_sum")) or ""),
            "avg_price": str(to_decimal(item.get("avg_price")) or ""),
            "sales_schema": item.get("sales_schema"),
            "weight": str(to_decimal(item.get("weight")) or ""),
            "nullable_create_date": item.get("nullable_create_date"),
        }
        snapshot_hash = hashlib.sha1(
            json.dumps(fingerprint, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

        rows.append({
            "source_type": "top_list",
            "query_key": query_key,
            "sku": sku,
            "page_no": int(item.get("page_no", 1)),
            "page_rank": int(item.get("page_rank", 0)),
            "name": item.get("name"),
            "brand": item.get("brand"),
            "link": item.get("link"),
            "photo": item.get("photo"),
            "cate1": item.get("cate1") or item.get("category1"),
            "cate2": item.get("cate2") or item.get("category2"),
            "cate3": item.get("cate3") or item.get("category3"),
            "sold_count": to_int(item.get("sold_count")),
            "sold_sum": to_decimal(item.get("sold_sum")),
            "avg_price": to_decimal(item.get("avg_price")),
            "sales_dynamics": to_decimal(item.get("sales_dynamics")),
            "sales_schema": item.get("sales_schema"),
            "weight": to_decimal(item.get("weight")),
            "nullable_create_date": item.get("nullable_create_date") or None,
            "snapshot_hash": snapshot_hash,
            "raw_json": json_dumps(item),
        })

    if not rows:
        return 0

    return db.execute_insert_many(
        """
        INSERT INTO seed_pool_skus
          (source_type, query_key, sku, page_no, page_rank, name, brand, link, photo,
           cate1, cate2, cate3, sold_count, sold_sum, avg_price, sales_dynamics,
           sales_schema, weight, nullable_create_date, snapshot_hash, raw_json, first_seen_at, last_seen_at)
        VALUES
          (%(source_type)s, %(query_key)s, %(sku)s, %(page_no)s, %(page_rank)s,
           %(name)s, %(brand)s, %(link)s, %(photo)s,
           %(cate1)s, %(cate2)s, %(cate3)s, %(sold_count)s, %(sold_sum)s, %(avg_price)s, %(sales_dynamics)s,
           %(sales_schema)s, %(weight)s, %(nullable_create_date)s, %(snapshot_hash)s, %(raw_json)s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON DUPLICATE KEY UPDATE
          name=VALUES(name), brand=VALUES(brand),
          sold_count=VALUES(sold_count), sold_sum=VALUES(sold_sum), avg_price=VALUES(avg_price),
          sales_dynamics=VALUES(sales_dynamics), sales_schema=VALUES(sales_schema),
          weight=VALUES(weight), nullable_create_date=VALUES(nullable_create_date),
          snapshot_hash=VALUES(snapshot_hash), raw_json=VALUES(raw_json),
          last_seen_at=CURRENT_TIMESTAMP
        """,
        rows,
        batch_size=200,
    )


def list_pending_seeds(limit: int = 500) -> list[dict[str, Any]]:
    """获取待处理的种子（按last_processed_at排序，优先处理未处理过的）"""
    return db.fetch_all(
        """
        SELECT * FROM seed_pool_skus
        WHERE last_process_status IS NULL OR last_process_status = 'pending'
        ORDER BY last_processed_at IS NULL DESC, last_processed_at ASC
        LIMIT %(limit)s
        """,
        {"limit": limit},
    )


def mark_seed_status(
    sku: str,
    query_key: str,
    status: str,
    reason: str | None = None,
    offer_count: int | None = None,
) -> int:
    return db.execute(
        """
        UPDATE seed_pool_skus
        SET last_process_status=%(status)s,
            last_process_reason=%(reason)s,
            last_offer_count=%(offer_count)s,
            last_processed_at=CURRENT_TIMESTAMP
        WHERE sku=%(sku)s AND query_key=%(query_key)s
        """,
        {
            "sku": sku,
            "query_key": query_key,
            "status": status,
            "reason": (reason or "")[:512],
            "offer_count": offer_count,
        },
    )


# ============================================================
# seller_shops
# ============================================================
def upsert_seller_shop(
    home_url: str,
    name: str | None = None,
    source_sku: str | None = None,
    source_table: str | None = None,
) -> str:
    """写入或更新卖家记录，返回seller_key"""
    key = seller_key(home_url)
    db.execute(
        """
        INSERT INTO seller_shops (seller_key, name, home_url, source_sku, source_table)
        VALUES (%(key)s, %(name)s, %(url)s, %(source_sku)s, %(source_table)s)
        ON DUPLICATE KEY UPDATE
          name=COALESCE(VALUES(name), name),
          source_sku=COALESCE(VALUES(source_sku), source_sku),
          source_table=COALESCE(VALUES(source_table), source_table)
        """,
        {"key": key, "name": name, "url": home_url, "source_sku": source_sku, "source_table": source_table},
    )
    return key


def list_due_sellers(limit: int = 20) -> list[dict[str, Any]]:
    """获取到期可采集的卖家列表（按入库时间倒序，新入库优先）"""
    return db.fetch_all(
        """
        SELECT * FROM seller_shops
        WHERE next_collect_after IS NULL OR next_collect_after <= NOW()
        ORDER BY created_at DESC
        LIMIT %(limit)s
        """,
        {"limit": limit},
    )


def freeze_seller(seller_key: str, months: int) -> int:
    """冻结卖家指定月数（设置next_collect_after）"""
    months = max(0, months)
    return db.execute(
        """
        UPDATE seller_shops
        SET next_collect_after=%(after)s
        WHERE seller_key=%(key)s
        """,
        {
            "key": seller_key,
            "after": (datetime.now() + timedelta(days=months * 30)).strftime("%Y-%m-%d %H:%M:%S"),
        },
    )


def freeze_seller_permanent(seller_key: str) -> int:
    """永久冻结卖家"""
    return db.execute(
        """
        UPDATE seller_shops
        SET next_collect_after='2099-12-31 23:59:59'
        WHERE seller_key=%(key)s
        """,
        {"key": seller_key},
    )


def mark_seller_collected(seller_key: str, qualified_count: int = 0) -> int:
    """标记卖家已采集，同时更新达标数和采集时间"""
    return db.execute(
        """
        UPDATE seller_shops
        SET last_collected_at=CURRENT_TIMESTAMP, qualified_sku_count=%(count)s
        WHERE seller_key=%(key)s
        """,
        {"key": seller_key, "count": qualified_count},
    )


# ============================================================
# sku_products
# ============================================================
def parse_sku3_response(sku: str, response: dict[str, Any]) -> dict[str, Any]:
    """解析毛子SKU3接口返回数据"""
    payload = response.get("data") or {}
    data = payload.get("data") or {}
    status = payload.get("status") or {}
    commission = data.get("category_commission") or {}

    return {
        "sku": str(data.get("sku") or sku),
        "variant_id": str(data.get("variantId") or "") or None,
        "brand": data.get("brand"),
        "category": data.get("category"),
        "category_ids": json_dumps(data.get("category_ids")),
        "rfbs_leq_1500": to_decimal(commission.get("rfbs_leq_1500")),
        "rfbs_leq_5000": to_decimal(commission.get("rfbs_leq_5000")),
        "rfbs_gt_5000": to_decimal(commission.get("rfbs_gt_5000")),
        "fbp_leq_1500": to_decimal(commission.get("fbp_leq_1500")),
        "fbp_leq_5000": to_decimal(commission.get("fbp_leq_5000")),
        "fbp_gt_5000": to_decimal(commission.get("fbp_gt_5000")),
        "sold_count": to_int(data.get("soldCount")),
        "sold_sum_text": data.get("soldSum"),
        "sold_sum_rub": to_decimal(data.get("soldSumRub")),
        "sold_sum_cny": to_decimal(data.get("soldSumCny")),
        "sales_dynamics": to_decimal(data.get("salesDynamics")),
        "sales_schema": data.get("salesSchema"),
        "nullable_redemption_rate": to_decimal(data.get("nullableRedemptionRate")),
        "custom_weight_g": grams_from_text(data.get("custom_weight")),
        "create_days": to_int(data.get("createDays")),
        "status_update_sales": 1 if status.get("update_sales") else 0,
        "status_update_variant": 1 if status.get("update_variant") else 0,
        "status_version": to_int(status.get("v")),
        "raw_json": json_dumps(response),
    }


def upsert_sku_product(
    metric: dict[str, Any],
    product_snapshot: dict[str, Any] | None = None,
    seller_offer_count: int | None = None,
    source_sku: str | None = None,
    source_table: str | None = None,
) -> int:
    """将合格SKU写入sku_products表"""
    product_snapshot = product_snapshot or {}
    params = {
        "sku": metric["sku"],
        "variant_id": metric.get("variant_id"),
        "product_url": product_snapshot.get("product_url"),
        "title": product_snapshot.get("title"),
        "brand": metric.get("brand") or product_snapshot.get("brand"),
        "category": metric.get("category"),
        "category_ids": metric.get("category_ids"),
        "price": product_snapshot.get("price"),
        "currency": product_snapshot.get("currency"),
        "main_image_url": product_snapshot.get("main_image_url"),
        "rfbs_leq_1500": metric.get("rfbs_leq_1500"),
        "rfbs_leq_5000": metric.get("rfbs_leq_5000"),
        "rfbs_gt_5000": metric.get("rfbs_gt_5000"),
        "fbp_leq_1500": metric.get("fbp_leq_1500"),
        "fbp_leq_5000": metric.get("fbp_leq_5000"),
        "fbp_gt_5000": metric.get("fbp_gt_5000"),
        "sold_count": metric.get("sold_count"),
        "sold_sum_text": metric.get("sold_sum_text"),
        "sold_sum_rub": metric.get("sold_sum_rub"),
        "sold_sum_cny": metric.get("sold_sum_cny"),
        "sales_dynamics": metric.get("sales_dynamics"),
        "sales_schema": metric.get("sales_schema"),
        "nullable_redemption_rate": metric.get("nullable_redemption_rate"),
        "custom_weight_g": metric.get("custom_weight_g"),
        "create_days": metric.get("create_days"),
        "status_update_sales": metric.get("status_update_sales") or 0,
        "status_update_variant": metric.get("status_update_variant") or 0,
        "status_version": metric.get("status_version"),
        "raw_maozi_json": metric.get("raw_json"),
        "raw_frontend_json": json_dumps(product_snapshot.get("raw")),
        "source_sku": source_sku,
        "source_table": source_table,
    }

    return db.execute(
        """
        INSERT INTO sku_products
          (sku, variant_id, product_url, title, brand, category, category_ids,
           price, currency, main_image_url,
           rfbs_leq_1500, rfbs_leq_5000, rfbs_gt_5000, fbp_leq_1500, fbp_leq_5000, fbp_gt_5000,
           sold_count, sold_sum_text, sold_sum_rub, sold_sum_cny, sales_dynamics,
           sales_schema, nullable_redemption_rate, custom_weight_g, create_days,
           status_update_sales, status_update_variant, status_version,
           raw_maozi_json, raw_frontend_json, source_sku, source_table,
           maozi_collected_at, last_seen_at)
        VALUES
          (%(sku)s, %(variant_id)s, %(product_url)s, %(title)s, %(brand)s, %(category)s, %(category_ids)s,
           %(price)s, %(currency)s, %(main_image_url)s,
           %(rfbs_leq_1500)s, %(rfbs_leq_5000)s, %(rfbs_gt_5000)s, %(fbp_leq_1500)s, %(fbp_leq_5000)s, %(fbp_gt_5000)s,
           %(sold_count)s, %(sold_sum_text)s, %(sold_sum_rub)s, %(sold_sum_cny)s, %(sales_dynamics)s,
           %(sales_schema)s, %(nullable_redemption_rate)s, %(custom_weight_g)s, %(create_days)s,
           %(status_update_sales)s, %(status_update_variant)s, %(status_version)s,
           %(raw_maozi_json)s, %(raw_frontend_json)s, %(source_sku)s, %(source_table)s,
           NOW(), CURRENT_TIMESTAMP)
        ON DUPLICATE KEY UPDATE
          variant_id=COALESCE(VALUES(variant_id), variant_id),
          product_url=COALESCE(VALUES(product_url), product_url),
          title=COALESCE(VALUES(title), title),
          brand=COALESCE(VALUES(brand), brand),
          category=COALESCE(VALUES(category), category),
          price=COALESCE(VALUES(price), price),
          currency=COALESCE(VALUES(currency), currency),
          main_image_url=COALESCE(VALUES(main_image_url), main_image_url),
          sold_count=COALESCE(VALUES(sold_count), sold_count),
          sold_sum_rub=COALESCE(VALUES(sold_sum_rub), sold_sum_rub),
          sold_sum_cny=COALESCE(VALUES(sold_sum_cny), sold_sum_cny),
          sales_dynamics=COALESCE(VALUES(sales_dynamics), sales_dynamics),
          sales_schema=COALESCE(VALUES(sales_schema), sales_schema),
          nullable_redemption_rate=COALESCE(VALUES(nullable_redemption_rate), nullable_redemption_rate),
          custom_weight_g=COALESCE(VALUES(custom_weight_g), custom_weight_g),
          create_days=COALESCE(VALUES(create_days), create_days),
          raw_maozi_json=COALESCE(VALUES(raw_maozi_json), raw_maozi_json),
          raw_frontend_json=COALESCE(VALUES(raw_frontend_json), raw_frontend_json),
          source_sku=COALESCE(VALUES(source_sku), source_sku),
          source_table=COALESCE(VALUES(source_table), source_table),
          maozi_collected_at=NOW(),
          last_seen_at=CURRENT_TIMESTAMP
        """,
        params,
    )


def count_sku_products() -> int:
    row = db.fetch_one("SELECT COUNT(*) AS cnt FROM sku_products")
    return row["cnt"] if row else 0


def count_seed_pool() -> int:
    row = db.fetch_one("SELECT COUNT(*) AS cnt FROM seed_pool_skus")
    return row["cnt"] if row else 0


def count_seller_shops() -> int:
    row = db.fetch_one("SELECT COUNT(*) AS cnt FROM seller_shops")
    return row["cnt"] if row else 0
