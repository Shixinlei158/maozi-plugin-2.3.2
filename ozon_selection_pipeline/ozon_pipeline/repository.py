from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from . import db
from .config import settings
from .rules import ProductSelectionResult, evaluate_selection_rule
from .util import (
    grams_from_text,
    json_dumps,
    percent_text_to_decimal,
    seller_key,
    to_decimal,
    to_int,
)


def upsert_seed_sku(sku: str, source: str = "manual") -> None:
    db.execute(
        """
        INSERT INTO seed_skus (sku, source)
        VALUES (%(sku)s, %(source)s)
        ON DUPLICATE KEY UPDATE source = VALUES(source), updated_at = CURRENT_TIMESTAMP
        """,
        {"sku": str(sku), "source": source},
    )


def top_list_query_key(filters: dict[str, Any]) -> str:
    normalized = json.dumps(filters, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def top_list_snapshot_hash(item: dict[str, Any]) -> str:
    fingerprint = {
        "sku": str(item.get("sku") or ""),
        "brand": item.get("brand"),
        "sold_count": to_int(item.get("sold_count")),
        "sold_sum": str(to_decimal(item.get("sold_sum")) or ""),
        "avg_price": str(to_decimal(item.get("avg_price")) or ""),
        "sales_schema": item.get("sales_schema"),
        "weight": str(to_decimal(item.get("weight")) or ""),
        "nullable_create_date": item.get("nullable_create_date"),
        "update_time": item.get("update_time"),
        "blocked_by_seller": bool(item.get("blocked_by_seller")),
    }
    normalized = json.dumps(fingerprint, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def top_list_item_params(query_key: str, run_id: int, page_no: int, page_rank: int, item: dict[str, Any]) -> dict[str, Any]:
    sku = str(item.get("sku") or "").strip()
    if not sku:
        return {}
    return {
        "query_key": query_key,
        "run_id": int(run_id),
        "sku": sku,
        "page_no": int(page_no),
        "page_rank": int(page_rank),
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
        "conv_to_cart_pdp": to_decimal(item.get("conv_to_cart_pdp")),
        "conv_to_cart_search": to_decimal(item.get("conv_to_cart_search")),
        "conv_view_to_order": to_decimal(item.get("conv_view_to_order")),
        "qty_view_pdp": to_int(item.get("qty_view_pdp")),
        "views": to_int(item.get("views")),
        "avg_delivery_days": to_decimal(item.get("avg_delivery_days")),
        "volume": to_decimal(item.get("volume")),
        "weight": to_decimal(item.get("weight")),
        "seller_id": str(item.get("seller_id") or "") or None,
        "sales_schema": item.get("sales_schema"),
        "is_china": 1 if item.get("is_china") else 0,
        "blocked_by_seller": 1 if item.get("blocked_by_seller") else 0,
        "nullable_create_date": item.get("nullable_create_date") or None,
        "upstream_update_time": item.get("update_time") or None,
        "snapshot_hash": top_list_snapshot_hash(item),
        "raw_json": json_dumps(item),
    }


def parse_sku3_response(sku: str, response: dict[str, Any]) -> dict[str, Any]:
    payload = response.get("data") or {}
    data = payload.get("data") or {}
    status = payload.get("status") or {}
    commission = data.get("category_commission") or {}
    metric = {
        "sku": str(data.get("sku") or sku),
        "variant_id": str(data.get("variantId") or "") or None,
        "brand": data.get("brand"),
        "category": data.get("category"),
        "category_ids": json_dumps(data.get("category_ids")) if data.get("category_ids") is not None else None,
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
        "avg_orders_on_acc_days": to_decimal(data.get("avgOrdersOnAccDays")),
        "avg_gmv_on_acc_days": to_decimal(data.get("avgGmvOnAccDays")),
        "avg_gmv_on_acc_days_cny": to_decimal(data.get("avgGmvOnAccDaysCny")),
        "sales_dynamics": to_decimal(data.get("salesDynamics")),
        "drr": to_decimal(data.get("drr")),
        "days_in_promo": to_int(data.get("daysInPromo")),
        "discount": to_decimal(data.get("discount")),
        "promo_revenue_share": to_decimal(data.get("promoRevenueShare")),
        "days_with_trafarets": to_int(data.get("daysWithTrafarets")),
        "qty_view_pdp": to_int(data.get("qtyViewPdp")),
        "conv_to_cart_pdp": to_decimal(data.get("convToCartPdp")),
        "session_count_search": to_int(data.get("sessionCountSearch")),
        "conv_to_cart_search": to_decimal(data.get("convToCartSearch")),
        "conv_view_to_order": to_decimal(data.get("convViewToOrder")),
        "sales_schema": data.get("salesSchema"),
        "nullable_redemption_rate": to_decimal(data.get("nullableRedemptionRate")),
        "custom_click_rate_text": data.get("custom_click_rate"),
        "custom_click_rate": percent_text_to_decimal(data.get("custom_click_rate")),
        "custom_volume_text": data.get("custom_volume"),
        "custom_weight_text": data.get("custom_weight"),
        "custom_weight_g": grams_from_text(data.get("custom_weight")),
        "nullable_create_date_text": data.get("nullableCreateDate"),
        "create_days": to_int(data.get("createDays")),
        "status_update_sales": 1 if status.get("update_sales") else 0,
        "status_update_variant": 1 if status.get("update_variant") else 0,
        "status_version": to_int(status.get("v")),
        "raw_json": json_dumps(response),
    }
    return metric


def upsert_sku3_response(
    sku: str,
    response: dict[str, Any],
    product_data: dict[str, Any] | None = None,
    seller_offer_count: int | None = None,
    metric_overrides: dict[str, Any] | None = None,
    apply_selection_rule: bool = False,
) -> dict[str, Any]:
    metric = parse_sku3_response(sku, response)
    for key, value in (metric_overrides or {}).items():
        if value is None or value == "":
            continue
        metric[key] = value
    selection_result: ProductSelectionResult | None = None
    if apply_selection_rule:
        selection_result = evaluate_selection_rule(metric, product_data or {}, seller_offer_count)
        mark_seed_rule_status(metric["sku"], selection_result)
        if not selection_result.matched:
            return {
                **metric,
                "qualified": False,
                "rule_name": selection_result.rule_name,
                "rule_reason": selection_result.summary,
            }

    columns = list(metric.keys())
    updates = [f"{col}=VALUES({col})" for col in columns if col != "sku"]
    sql = f"""
        INSERT INTO sku_plugin_metrics ({",".join(columns)}, collected_at)
        VALUES ({",".join("%(" + col + ")s" for col in columns)}, CURRENT_TIMESTAMP)
        ON DUPLICATE KEY UPDATE {",".join(updates)}, collected_at=CURRENT_TIMESTAMP
    """
    db.execute(sql, metric)
    upsert_sku_product(metric, product_data=product_data)
    if metric["status_update_sales"] or metric["status_update_variant"]:
        enqueue_task(
            "playwright_refresh",
            f"playwright_refresh:{sku}",
            {"sku": str(sku), "reason": "sku3 requested fresh seller data"},
            priority=50,
        )
    if selection_result is not None:
        return {
            **metric,
            "qualified": True,
            "rule_name": selection_result.rule_name,
            "rule_reason": selection_result.summary,
        }
    return metric


def upsert_sku_product(metric: dict[str, Any], product_data: dict[str, Any] | None = None) -> None:
    product_data = product_data or {}
    params = {
        "sku": metric["sku"],
        "variant_id": metric.get("variant_id"),
        "product_url": product_data.get("product_url"),
        "title": product_data.get("title"),
        "brand": metric.get("brand") or product_data.get("brand"),
        "category": metric.get("category"),
        "category_ids": metric.get("category_ids"),
        "price": product_data.get("price"),
        "currency": product_data.get("currency"),
        "main_image_url": product_data.get("main_image_url"),
        "raw_frontend_json": json_dumps(product_data.get("raw")) if product_data.get("raw") is not None else None,
    }
    db.execute(
        """
        INSERT INTO sku_products
          (sku, variant_id, product_url, title, brand, category, category_ids,
           price, currency, main_image_url, raw_frontend_json, last_seen_at)
        VALUES
          (%(sku)s, %(variant_id)s, %(product_url)s, %(title)s, %(brand)s, %(category)s, %(category_ids)s,
           %(price)s, %(currency)s, %(main_image_url)s, %(raw_frontend_json)s, CURRENT_TIMESTAMP)
        ON DUPLICATE KEY UPDATE
          variant_id=VALUES(variant_id),
          product_url=COALESCE(VALUES(product_url), product_url),
          title=COALESCE(VALUES(title), title),
          brand=COALESCE(VALUES(brand), brand),
          category=COALESCE(VALUES(category), category),
          category_ids=COALESCE(VALUES(category_ids), category_ids),
          price=COALESCE(VALUES(price), price),
          currency=COALESCE(VALUES(currency), currency),
          main_image_url=COALESCE(VALUES(main_image_url), main_image_url),
          raw_frontend_json=COALESCE(VALUES(raw_frontend_json), raw_frontend_json),
          last_seen_at=CURRENT_TIMESTAMP
        """,
        params,
    )


def mark_seed_rule_status(sku: str, result: ProductSelectionResult) -> None:
    mark_seed_status(
        str(sku),
        status="qualified" if result.matched else "rejected",
        reason=result.summary,
        score=1 if result.matched else 0,
    )


def mark_seed_status(sku: str, *, status: str, reason: str | None = None, score: int | None = None) -> None:
    db.execute(
        """
        INSERT INTO seed_skus (sku, source)
        VALUES (%(sku)s, 'rule_check')
        ON DUPLICATE KEY UPDATE updated_at=CURRENT_TIMESTAMP
        """,
        {"sku": str(sku)},
    )
    db.execute(
        """
        UPDATE seed_skus
        SET status=%(status)s,
            reason=%(reason)s,
            score=%(score)s,
            last_checked_at=CURRENT_TIMESTAMP
        WHERE sku=%(sku)s
        """,
        {
            "sku": str(sku),
            "status": status,
            "reason": (reason or "")[:512] or None,
            "score": score,
        },
    )


def start_top_list_run(
    query_key: str,
    main_type: str,
    filters: dict[str, Any],
    page_from: int,
    page_to: int,
    page_size: int,
) -> int:
    return db.insert_and_get_id(
        """
        INSERT INTO top_list_runs
          (query_key, main_type, filters_json, page_from, page_to, page_size, status)
        VALUES
          (%(query_key)s, %(main_type)s, %(filters_json)s, %(page_from)s, %(page_to)s, %(page_size)s, 'running')
        """,
        {
            "query_key": query_key,
            "main_type": main_type,
            "filters_json": json_dumps(filters),
            "page_from": page_from,
            "page_to": page_to,
            "page_size": page_size,
        },
    )


def finish_top_list_run(
    run_id: int,
    *,
    status: str,
    pages_fetched: int,
    items_fetched: int,
    due_skus: int = 0,
    processed_skus: int = 0,
    qualified_skus: int = 0,
    rejected_skus: int = 0,
    seller_expansions: int = 0,
    error_message: str | None = None,
) -> None:
    db.execute(
        """
        UPDATE top_list_runs
        SET status=%(status)s,
            pages_fetched=%(pages_fetched)s,
            items_fetched=%(items_fetched)s,
            due_skus=%(due_skus)s,
            processed_skus=%(processed_skus)s,
            qualified_skus=%(qualified_skus)s,
            rejected_skus=%(rejected_skus)s,
            seller_expansions=%(seller_expansions)s,
            error_message=%(error_message)s,
            finished_at=CURRENT_TIMESTAMP
        WHERE id=%(run_id)s
        """,
        {
            "run_id": int(run_id),
            "status": status,
            "pages_fetched": pages_fetched,
            "items_fetched": items_fetched,
            "due_skus": due_skus,
            "processed_skus": processed_skus,
            "qualified_skus": qualified_skus,
            "rejected_skus": rejected_skus,
            "seller_expansions": seller_expansions,
            "error_message": error_message[:65535] if error_message else None,
        },
    )


def get_recent_top_list_run(query_key: str, within_hours: int) -> dict[str, Any] | None:
    return db.fetch_one(
        """
        SELECT id, query_key, main_type, filters_json, page_from, page_to, page_size, status,
               pages_fetched, items_fetched, due_skus, processed_skus, qualified_skus,
               rejected_skus, seller_expansions, started_at, finished_at
        FROM top_list_runs
        WHERE query_key=%(query_key)s
          AND status='success'
          AND started_at >= DATE_SUB(CURRENT_TIMESTAMP, INTERVAL %(hours)s HOUR)
        ORDER BY started_at DESC
        LIMIT 1
        """,
        {"query_key": query_key, "hours": int(within_hours)},
    )


def upsert_top_list_item(query_key: str, run_id: int, page_no: int, page_rank: int, item: dict[str, Any]) -> str:
    params = top_list_item_params(query_key, run_id, page_no, page_rank, item)
    sku = str(params.get("sku") or "")
    if not sku:
        return ""
    db.execute(
        """
        INSERT INTO top_list_skus
          (query_key, run_id, sku, page_no, page_rank, name, brand, link, photo,
           cate1, cate2, cate3, sold_count, sold_sum, avg_price, sales_dynamics,
           conv_to_cart_pdp, conv_to_cart_search, conv_view_to_order, qty_view_pdp,
           views, avg_delivery_days, volume, weight, seller_id, sales_schema,
           is_china, blocked_by_seller, nullable_create_date, upstream_update_time,
           snapshot_hash, raw_json, last_seen_at)
        VALUES
          (%(query_key)s, %(run_id)s, %(sku)s, %(page_no)s, %(page_rank)s, %(name)s, %(brand)s, %(link)s, %(photo)s,
           %(cate1)s, %(cate2)s, %(cate3)s, %(sold_count)s, %(sold_sum)s, %(avg_price)s, %(sales_dynamics)s,
           %(conv_to_cart_pdp)s, %(conv_to_cart_search)s, %(conv_view_to_order)s, %(qty_view_pdp)s,
           %(views)s, %(avg_delivery_days)s, %(volume)s, %(weight)s, %(seller_id)s, %(sales_schema)s,
           %(is_china)s, %(blocked_by_seller)s, %(nullable_create_date)s, %(upstream_update_time)s,
           %(snapshot_hash)s, %(raw_json)s, CURRENT_TIMESTAMP)
        ON DUPLICATE KEY UPDATE
          run_id=VALUES(run_id),
          page_no=VALUES(page_no),
          page_rank=VALUES(page_rank),
          name=VALUES(name),
          brand=VALUES(brand),
          link=VALUES(link),
          photo=VALUES(photo),
          cate1=VALUES(cate1),
          cate2=VALUES(cate2),
          cate3=VALUES(cate3),
          sold_count=VALUES(sold_count),
          sold_sum=VALUES(sold_sum),
          avg_price=VALUES(avg_price),
          sales_dynamics=VALUES(sales_dynamics),
          conv_to_cart_pdp=VALUES(conv_to_cart_pdp),
          conv_to_cart_search=VALUES(conv_to_cart_search),
          conv_view_to_order=VALUES(conv_view_to_order),
          qty_view_pdp=VALUES(qty_view_pdp),
          views=VALUES(views),
          avg_delivery_days=VALUES(avg_delivery_days),
          volume=VALUES(volume),
          weight=VALUES(weight),
          seller_id=VALUES(seller_id),
          sales_schema=VALUES(sales_schema),
          is_china=VALUES(is_china),
          blocked_by_seller=VALUES(blocked_by_seller),
          nullable_create_date=VALUES(nullable_create_date),
          upstream_update_time=VALUES(upstream_update_time),
          snapshot_hash=VALUES(snapshot_hash),
          raw_json=VALUES(raw_json),
          last_seen_at=CURRENT_TIMESTAMP
        """,
        params,
    )
    return sku


def upsert_seed_pool_item(query_key: str, run_id: int, page_no: int, page_rank: int, item: dict[str, Any]) -> str:
    params = top_list_item_params(query_key, run_id, page_no, page_rank, item)
    sku = str(params.get("sku") or "")
    if not sku:
        return ""
    db.execute(
        """
        INSERT INTO seed_pool_skus
          (source_type, query_key, source_run_id, sku, page_no, page_rank, name, brand, link, photo,
           cate1, cate2, cate3, sold_count, sold_sum, avg_price, sales_dynamics,
           conv_to_cart_pdp, conv_to_cart_search, conv_view_to_order, qty_view_pdp,
           views, avg_delivery_days, volume, weight, seller_id, sales_schema,
           is_china, blocked_by_seller, nullable_create_date, upstream_update_time,
           snapshot_hash, raw_json, last_seen_at)
        VALUES
          ('top_list', %(query_key)s, %(run_id)s, %(sku)s, %(page_no)s, %(page_rank)s, %(name)s, %(brand)s, %(link)s, %(photo)s,
           %(cate1)s, %(cate2)s, %(cate3)s, %(sold_count)s, %(sold_sum)s, %(avg_price)s, %(sales_dynamics)s,
           %(conv_to_cart_pdp)s, %(conv_to_cart_search)s, %(conv_view_to_order)s, %(qty_view_pdp)s,
           %(views)s, %(avg_delivery_days)s, %(volume)s, %(weight)s, %(seller_id)s, %(sales_schema)s,
           %(is_china)s, %(blocked_by_seller)s, %(nullable_create_date)s, %(upstream_update_time)s,
           %(snapshot_hash)s, %(raw_json)s, CURRENT_TIMESTAMP)
        ON DUPLICATE KEY UPDATE
          source_run_id=VALUES(source_run_id),
          page_no=VALUES(page_no),
          page_rank=VALUES(page_rank),
          name=VALUES(name),
          brand=VALUES(brand),
          link=VALUES(link),
          photo=VALUES(photo),
          cate1=VALUES(cate1),
          cate2=VALUES(cate2),
          cate3=VALUES(cate3),
          sold_count=VALUES(sold_count),
          sold_sum=VALUES(sold_sum),
          avg_price=VALUES(avg_price),
          sales_dynamics=VALUES(sales_dynamics),
          conv_to_cart_pdp=VALUES(conv_to_cart_pdp),
          conv_to_cart_search=VALUES(conv_to_cart_search),
          conv_view_to_order=VALUES(conv_view_to_order),
          qty_view_pdp=VALUES(qty_view_pdp),
          views=VALUES(views),
          avg_delivery_days=VALUES(avg_delivery_days),
          volume=VALUES(volume),
          weight=VALUES(weight),
          seller_id=VALUES(seller_id),
          sales_schema=VALUES(sales_schema),
          is_china=VALUES(is_china),
          blocked_by_seller=VALUES(blocked_by_seller),
          nullable_create_date=VALUES(nullable_create_date),
          upstream_update_time=VALUES(upstream_update_time),
          snapshot_hash=VALUES(snapshot_hash),
          raw_json=VALUES(raw_json),
          last_seen_at=CURRENT_TIMESTAMP
        """,
        params,
    )
    return sku


def list_top_list_skus(query_key: str) -> list[dict[str, Any]]:
    return db.fetch_all(
        """
        SELECT t.sku, t.page_no, t.page_rank, t.snapshot_hash, t.last_processed_snapshot_hash,
               t.last_process_status, t.last_process_reason, t.last_selected_at, t.last_processed_at,
               t.name, t.brand, t.link, t.photo, t.sold_count, t.sold_sum, t.avg_price, t.sales_dynamics,
               t.sales_schema, t.weight, t.nullable_create_date, t.upstream_update_time,
               s.status AS seed_status, s.last_checked_at AS seed_last_checked_at
        FROM top_list_skus t
        LEFT JOIN seed_skus s ON s.sku = t.sku
        WHERE t.query_key=%(query_key)s
        ORDER BY t.page_no ASC, t.page_rank ASC, t.sku ASC
        """,
        {"query_key": query_key},
    )


def list_seed_pool_skus(query_key: str, source_type: str = "top_list") -> list[dict[str, Any]]:
    return db.fetch_all(
        """
        SELECT source_type, query_key, sku, page_no, page_rank, snapshot_hash, last_processed_snapshot_hash,
               last_process_status, last_process_reason, last_offer_count, last_selected_at, last_processed_at,
               name, brand, link, photo, sold_count, sold_sum, avg_price, sales_dynamics,
               sales_schema, weight, nullable_create_date, upstream_update_time
        FROM seed_pool_skus
        WHERE source_type=%(source_type)s
          AND query_key=%(query_key)s
        ORDER BY page_no ASC, page_rank ASC, sku ASC
        """,
        {"source_type": source_type, "query_key": query_key},
    )


def top_list_sku_due_state(item: dict[str, Any]) -> tuple[bool, str]:
    now = datetime.now()
    last_processed_at = item.get("last_processed_at")
    snapshot_hash = item.get("snapshot_hash")
    last_processed_snapshot_hash = item.get("last_processed_snapshot_hash")

    if not item.get("seed_last_checked_at"):
        return True, "new_seed"
    if not last_processed_at or not last_processed_snapshot_hash:
        return True, "never_processed_in_top_list"
    if snapshot_hash and snapshot_hash != last_processed_snapshot_hash:
        if last_processed_at <= now - timedelta(hours=settings.top_list_recheck_changed_hours):
            return True, "top_list_snapshot_changed"

    status = item.get("seed_status") or "pending"
    last_checked_at = item.get("seed_last_checked_at")
    if status == "qualified":
        if last_checked_at <= now - timedelta(days=settings.top_list_recheck_qualified_days):
            return True, "qualified_ttl_expired"
        return False, "qualified_ttl_not_due"
    if status == "failed":
        if last_checked_at <= now - timedelta(hours=settings.top_list_recheck_failed_hours):
            return True, "failed_retry_due"
        return False, "failed_retry_not_due"
    if status in {"rejected", "done"}:
        if last_checked_at <= now - timedelta(days=settings.top_list_recheck_rejected_days):
            return True, "rejected_ttl_expired"
        return False, "rejected_ttl_not_due"
    return True, "pending_or_unknown"


def seed_pool_sku_due_state(item: dict[str, Any]) -> tuple[bool, str]:
    now = datetime.now()
    last_processed_at = item.get("last_processed_at")
    snapshot_hash = item.get("snapshot_hash")
    last_processed_snapshot_hash = item.get("last_processed_snapshot_hash")

    if not last_processed_at or not last_processed_snapshot_hash:
        return True, "new_seed"
    if snapshot_hash and snapshot_hash != last_processed_snapshot_hash:
        if last_processed_at <= now - timedelta(hours=settings.top_list_recheck_changed_hours):
            return True, "seed_snapshot_changed"
        return False, "seed_snapshot_changed_but_not_due"

    status = item.get("last_process_status") or "pending"
    if status == "expanded":
        if last_processed_at <= now - timedelta(days=settings.top_list_recheck_qualified_days):
            return True, "expanded_ttl_expired"
        return False, "expanded_ttl_not_due"
    if status == "failed":
        if last_processed_at <= now - timedelta(hours=settings.top_list_recheck_failed_hours):
            return True, "failed_retry_due"
        return False, "failed_retry_not_due"
    if status in {"rejected", "done"}:
        if last_processed_at <= now - timedelta(days=settings.top_list_recheck_rejected_days):
            return True, "rejected_ttl_expired"
        return False, "rejected_ttl_not_due"
    return True, "pending_or_unknown"


def mark_top_list_sku_selected(query_key: str, sku: str) -> None:
    db.execute(
        """
        UPDATE top_list_skus
        SET last_selected_at=CURRENT_TIMESTAMP
        WHERE query_key=%(query_key)s AND sku=%(sku)s
        """,
        {"query_key": query_key, "sku": str(sku)},
    )


def mark_seed_pool_selected(query_key: str, sku: str, source_type: str = "top_list") -> None:
    db.execute(
        """
        UPDATE seed_pool_skus
        SET last_selected_at=CURRENT_TIMESTAMP
        WHERE source_type=%(source_type)s
          AND query_key=%(query_key)s
          AND sku=%(sku)s
        """,
        {"source_type": source_type, "query_key": query_key, "sku": str(sku)},
    )


def mark_top_list_sku_processed(
    query_key: str,
    sku: str,
    *,
    qualified: bool,
    snapshot_hash: str | None = None,
    reason: str | None = None,
) -> None:
    db.execute(
        """
        UPDATE top_list_skus
        SET last_processed_at=CURRENT_TIMESTAMP,
            last_processed_snapshot_hash=COALESCE(%(snapshot_hash)s, snapshot_hash),
            last_process_status=%(status)s,
            last_process_reason=%(reason)s
        WHERE query_key=%(query_key)s AND sku=%(sku)s
        """,
        {
            "query_key": query_key,
            "sku": str(sku),
            "snapshot_hash": snapshot_hash,
            "status": "qualified" if qualified else "rejected",
            "reason": (reason or "")[:512] or None,
        },
    )


def mark_seed_pool_processed(
    query_key: str,
    sku: str,
    *,
    status: str,
    snapshot_hash: str | None = None,
    reason: str | None = None,
    seller_offer_count: int | None = None,
    source_type: str = "top_list",
) -> None:
    db.execute(
        """
        UPDATE seed_pool_skus
        SET last_processed_at=CURRENT_TIMESTAMP,
            last_processed_snapshot_hash=COALESCE(%(snapshot_hash)s, last_processed_snapshot_hash),
            last_process_status=%(status)s,
            last_process_reason=%(reason)s,
            last_offer_count=%(seller_offer_count)s
        WHERE source_type=%(source_type)s
          AND query_key=%(query_key)s
          AND sku=%(sku)s
        """,
        {
            "source_type": source_type,
            "query_key": query_key,
            "sku": str(sku),
            "snapshot_hash": snapshot_hash,
            "status": status,
            "reason": (reason or "")[:512] or None,
            "seller_offer_count": seller_offer_count,
        },
    )


def seller_recently_collected(home_url: str) -> bool:
    key = seller_key(home_url)
    row = db.fetch_one(
        "SELECT last_collected_at FROM seller_shops WHERE seller_key=%(seller_key)s",
        {"seller_key": key},
    )
    if not row or not row.get("last_collected_at"):
        return False
    return row["last_collected_at"] >= datetime.now() - timedelta(days=settings.seller_recollect_days)


def upsert_seller_shop(
    home_url: str,
    name: str | None = None,
    logo_url: str | None = None,
    raw: dict[str, Any] | None = None,
) -> str:
    key = seller_key(home_url)
    db.execute(
        """
        INSERT INTO seller_shops
          (seller_key, name, home_url, logo_url, raw_json, next_collect_after)
        VALUES
          (%(seller_key)s, %(name)s, %(home_url)s, %(logo_url)s, %(raw_json)s, %(next_collect_after)s)
        ON DUPLICATE KEY UPDATE
          name=COALESCE(VALUES(name), name),
          logo_url=COALESCE(VALUES(logo_url), logo_url),
          raw_json=COALESCE(VALUES(raw_json), raw_json),
          updated_at=CURRENT_TIMESTAMP
        """,
        {
            "seller_key": key,
            "name": name,
            "home_url": home_url,
            "logo_url": logo_url,
            "raw_json": json_dumps(raw) if raw is not None else None,
            "next_collect_after": datetime.now() + timedelta(days=settings.seller_recollect_days),
        },
    )
    return key


def get_seller_shop(seller_key_value: str) -> dict[str, Any] | None:
    return db.fetch_one(
        """
        SELECT seller_key, name, home_url, logo_url, last_collected_at, next_collect_after
        FROM seller_shops
        WHERE seller_key=%(seller_key)s
        """,
        {"seller_key": seller_key_value},
    )


def upsert_seller_offer(source_sku: str, offer: dict[str, Any]) -> str:
    home_url = offer.get("seller_home_url") or ""
    key = upsert_seller_shop(
        home_url,
        name=offer.get("name"),
        logo_url=offer.get("logo_url"),
        raw=offer.get("raw") or {},
    )
    params = {
        "source_sku": str(source_sku),
        "seller_key": key,
        "offer_sku": offer.get("offer_sku") or "",
        "seller_name": offer.get("name"),
        "seller_home_url": home_url,
        "offer_product_url": offer.get("offer_product_url"),
        "price_text": offer.get("price_text"),
        "price_amount": offer.get("price_amount"),
        "currency": offer.get("currency"),
        "main_image_url": offer.get("main_image_url"),
        "raw_json": json_dumps(offer.get("raw") or {}),
    }
    db.execute(
        """
        INSERT INTO seller_offers
          (source_sku, seller_key, offer_sku, seller_name, seller_home_url,
           offer_product_url, price_text, price_amount, currency, main_image_url, raw_json)
        VALUES
          (%(source_sku)s, %(seller_key)s, %(offer_sku)s, %(seller_name)s, %(seller_home_url)s,
           %(offer_product_url)s, %(price_text)s, %(price_amount)s, %(currency)s, %(main_image_url)s, %(raw_json)s)
        ON DUPLICATE KEY UPDATE
          seller_name=VALUES(seller_name),
          seller_home_url=VALUES(seller_home_url),
          offer_product_url=VALUES(offer_product_url),
          price_text=VALUES(price_text),
          price_amount=VALUES(price_amount),
          currency=VALUES(currency),
          main_image_url=VALUES(main_image_url),
          raw_json=VALUES(raw_json),
          collected_at=CURRENT_TIMESTAMP
        """,
        params,
    )
    if home_url and not seller_recently_collected(home_url):
        enqueue_task(
            "seller_home",
            f"seller_home:{key}",
            {"seller_key": key, "home_url": home_url, "name": offer.get("name")},
            priority=80,
        )
    return key


def upsert_seller_home_sku(seller_home_url: str, item: dict[str, Any]) -> str:
    key = seller_key(seller_home_url)
    sku = extract_sku_from_product_url(item.get("href") or item.get("product_url") or "")
    if not sku:
        return ""
    params = {
        "seller_key": key,
        "sku": sku,
        "product_url": item.get("href") or item.get("product_url"),
        "title": item.get("title"),
        "price_amount": to_decimal(item.get("price_amount")),
        "currency": item.get("currency"),
        "main_image_url": item.get("image_url") or item.get("main_image_url"),
        "raw_json": json_dumps(item),
    }
    db.execute(
        """
        INSERT INTO seller_home_skus
          (seller_key, sku, product_url, title, price_amount, currency, main_image_url, raw_json)
        VALUES
          (%(seller_key)s, %(sku)s, %(product_url)s, %(title)s, %(price_amount)s, %(currency)s, %(main_image_url)s, %(raw_json)s)
        ON DUPLICATE KEY UPDATE
          product_url=VALUES(product_url),
          title=COALESCE(VALUES(title), title),
          price_amount=COALESCE(VALUES(price_amount), price_amount),
          currency=COALESCE(VALUES(currency), currency),
          main_image_url=COALESCE(VALUES(main_image_url), main_image_url),
          raw_json=VALUES(raw_json),
          updated_at=CURRENT_TIMESTAMP
        """,
        params,
    )
    return sku


def mark_seller_collected(seller_key_value: str) -> None:
    db.execute(
        """
        UPDATE seller_shops
        SET last_collected_at=CURRENT_TIMESTAMP,
            next_collect_after=DATE_ADD(CURRENT_TIMESTAMP, INTERVAL %(days)s DAY)
        WHERE seller_key=%(seller_key)s
        """,
        {"seller_key": seller_key_value, "days": settings.seller_recollect_days},
    )


def enqueue_task(task_type: str, task_key: str, payload: dict[str, Any], priority: int = 100) -> None:
    db.execute(
        """
        INSERT INTO crawl_tasks (task_type, task_key, payload, priority)
        VALUES (%(task_type)s, %(task_key)s, %(payload)s, %(priority)s)
        ON DUPLICATE KEY UPDATE
          payload=VALUES(payload),
          priority=LEAST(priority, VALUES(priority)),
          status=IF(status IN ('success','skipped'), status, 'pending'),
          updated_at=CURRENT_TIMESTAMP
        """,
        {
            "task_type": task_type,
            "task_key": task_key,
            "payload": json_dumps(payload),
            "priority": priority,
        },
    )


def extract_sku_from_product_url(url: str) -> str | None:
    if not url:
        return None
    path = url.split("?", 1)[0].rstrip("/")
    segment = path.split("/")[-1]
    parts = segment.split("-")
    candidates = [part for part in parts if part.isdigit() and len(part) >= 6]
    if candidates:
        return candidates[-1]
    return None
