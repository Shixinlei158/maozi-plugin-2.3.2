from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from . import db
from .config import settings
from .rules import TOP_LIST_SEED_RULE, ProductSelectionResult, evaluate_selection_rule, price_to_cny
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


def bulk_upsert_seed_skus(skus: list[str], source: str = "manual") -> int:
    rows = [{"sku": str(sku), "source": source} for sku in skus if str(sku).strip()]
    if not rows:
        return 0
    return db.execute_many(
        """
        INSERT INTO seed_skus (sku, source)
        VALUES (%(sku)s, %(source)s)
        ON DUPLICATE KEY UPDATE source = VALUES(source), updated_at = CURRENT_TIMESTAMP
        """,
        rows,
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


def format_decimal_text(value: Any) -> str | None:
    decimal_value = to_decimal(value)
    if decimal_value is None:
        return None
    text = format(decimal_value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def sanitize_zh_display_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text in {"暂无数据", "--", "-", "无"}:
            return None
        return text
    if isinstance(value, Decimal):
        return format_decimal_text(value)
    return value


def build_maozi_fields_zh(
    metric: dict[str, Any],
    *,
    plugin_card: dict[str, Any] | None = None,
    seller_offer_count: int | None = None,
) -> dict[str, Any]:
    plugin_card = plugin_card or {}
    line_map = plugin_card.get("line_map") or {}
    display: dict[str, Any] = {}

    for label, raw_value in line_map.items():
        cleaned = sanitize_zh_display_value(raw_value)
        if cleaned is not None:
            display[label] = cleaned

    def put(label: str, value: Any, *, percent: bool = False) -> None:
        if label in display:
            return
        cleaned = sanitize_zh_display_value(value)
        if cleaned is None:
            return
        if percent:
            if isinstance(cleaned, str) and cleaned.endswith("%"):
                display[label] = cleaned
                return
            text = format_decimal_text(cleaned)
            if text is None:
                return
            display[label] = f"{text}%"
            return
        display[label] = cleaned

    put("类目", metric.get("category"))
    put("品牌", metric.get("brand"))
    put("变体ID", metric.get("variant_id"))

    category_ids = metric.get("category_ids")
    if category_ids and "类目ID列表" not in display:
        try:
            display["类目ID列表"] = json.loads(category_ids) if isinstance(category_ids, str) else category_ids
        except (TypeError, json.JSONDecodeError):
            display["类目ID列表"] = category_ids

    put("rFBS佣金(1500以下)", metric.get("rfbs_leq_1500"))
    put("rFBS佣金(1500-5000)", metric.get("rfbs_leq_5000"))
    put("rFBS佣金(5000以上)", metric.get("rfbs_gt_5000"))
    put("FBP佣金(1500以下)", metric.get("fbp_leq_1500"))
    put("FBP佣金(1500-5000)", metric.get("fbp_leq_5000"))
    put("FBP佣金(5000以上)", metric.get("fbp_gt_5000"))
    put("月销量", metric.get("sold_count"))
    put("月销售额", metric.get("sold_sum_text"))
    put("月销售额(RUB)", metric.get("sold_sum_rub"))
    put("月销售额(CNY)", metric.get("sold_sum_cny"))
    put("平均日订单数", metric.get("avg_orders_on_acc_days"))
    put("平均日GMV(RUB)", metric.get("avg_gmv_on_acc_days"))
    put("平均日GMV(CNY)", metric.get("avg_gmv_on_acc_days_cny"))
    put("月周转动态", metric.get("sales_dynamics"))
    put("广告费占比", metric.get("drr"), percent=True)
    put("参与促销天数", metric.get("days_in_promo"))
    put("参与促销的折扣", metric.get("discount"), percent=True)
    put("促销活动的转化率", metric.get("promo_revenue_share"), percent=True)
    put("付费推广天数", metric.get("days_with_trafarets"))
    put("商品卡浏览量", metric.get("qty_view_pdp"))
    put("商品卡加购率", metric.get("conv_to_cart_pdp"), percent=True)
    put("搜索目录浏览量", metric.get("session_count_search"))
    put("搜索目录加购率", metric.get("conv_to_cart_search"), percent=True)
    put("展示转化率", metric.get("conv_view_to_order"), percent=True)
    put("商品点击率", metric.get("custom_click_rate_text") or metric.get("custom_click_rate"), percent=True)
    put("发货模式", metric.get("sales_schema"))
    put("退货取消率", metric.get("nullable_redemption_rate"), percent=True)
    put("长 宽 高", metric.get("custom_volume_text"))
    put("重 量", metric.get("custom_weight_text"))
    put("重量(g)", metric.get("custom_weight_g"))
    put("上架时间", metric.get("nullable_create_date_text"))
    put("上架天数", metric.get("create_days"))
    if "跟卖列表" not in display and seller_offer_count is not None:
        display["跟卖列表"] = f"共{seller_offer_count}个卖家"
    put("跟卖人数", seller_offer_count)
    display["状态-需更新销量"] = "是" if metric.get("status_update_sales") else "否"
    display["状态-需更新变体"] = "是" if metric.get("status_update_variant") else "否"
    put("状态版本", metric.get("status_version"))
    return display


def top_list_metric_preview(item: dict[str, Any]) -> dict[str, Any]:
    metric: dict[str, Any] = {
        "sku": str(item.get("sku") or "").strip(),
        "brand": item.get("brand"),
        "sold_count": to_int(item.get("sold_count")),
        "sales_dynamics": to_decimal(item.get("sales_dynamics")),
        "qty_view_pdp": to_int(item.get("qty_view_pdp")),
        "conv_to_cart_pdp": to_decimal(item.get("conv_to_cart_pdp")),
        "conv_to_cart_search": to_decimal(item.get("conv_to_cart_search")),
        "conv_view_to_order": to_decimal(item.get("conv_view_to_order")),
        "sales_schema": item.get("sales_schema"),
        "custom_weight_g": to_decimal(item.get("weight")),
        "custom_weight_text": f"{item.get('weight')}g" if item.get("weight") not in (None, "", 0, "0", 0.0) else None,
        "sold_sum_rub": to_decimal(item.get("sold_sum")),
    }
    create_value = item.get("nullable_create_date")
    if create_value:
        try:
            if isinstance(create_value, datetime):
                created = create_value.date()
            else:
                created = datetime.strptime(str(create_value), "%Y-%m-%d").date()
            create_days = (datetime.now().date() - created).days
            metric["create_days"] = create_days
            metric["nullable_create_date_text"] = f"{created.isoformat()}({create_days}天)"
        except ValueError:
            pass
    return metric


def record_sku_discovery(
    sku: str,
    *,
    source_type: str,
    source_key: str,
    source_url: str | None = None,
    source_name: str | None = None,
    related_sku: str | None = None,
    source_rank: int | None = None,
    raw: dict[str, Any] | list[Any] | None = None,
) -> None:
    db.execute(
        """
        INSERT INTO sku_discovery_sources
          (sku, source_type, source_key, source_url, source_name, related_sku, source_rank, raw_json, last_seen_at)
        VALUES
          (%(sku)s, %(source_type)s, %(source_key)s, %(source_url)s, %(source_name)s, %(related_sku)s, %(source_rank)s, %(raw_json)s, CURRENT_TIMESTAMP)
        ON DUPLICATE KEY UPDATE
          source_url=COALESCE(VALUES(source_url), source_url),
          source_name=COALESCE(VALUES(source_name), source_name),
          related_sku=COALESCE(VALUES(related_sku), related_sku),
          source_rank=COALESCE(VALUES(source_rank), source_rank),
          raw_json=COALESCE(VALUES(raw_json), raw_json),
          last_seen_at=CURRENT_TIMESTAMP
        """,
        {
            "sku": str(sku),
            "source_type": source_type,
            "source_key": source_key,
            "source_url": source_url,
            "source_name": source_name,
            "related_sku": str(related_sku) if related_sku else None,
            "source_rank": source_rank,
            "raw_json": json_dumps(raw) if raw is not None else None,
        },
    )


def upsert_sku_universe(
    sku: str,
    *,
    product_data: dict[str, Any] | None = None,
    metric: dict[str, Any] | None = None,
    plugin_card: dict[str, Any] | None = None,
    offers: list[dict[str, Any]] | None = None,
    seller_offer_count: int | None = None,
    formal_rule_result: ProductSelectionResult | None = None,
    seed_rule_result: ProductSelectionResult | None = None,
) -> None:
    product_data = product_data or {}
    metric = metric or {}
    plugin_card = plugin_card or {}
    price_amount = to_decimal(product_data.get("price"))
    price_currency = product_data.get("currency")
    offers_json = None if offers is None else offers
    if seller_offer_count is None and offers is not None:
        seller_offer_count = len(offers)
    maozi_fields_zh = build_maozi_fields_zh(
        metric,
        plugin_card=plugin_card,
        seller_offer_count=seller_offer_count,
    )
    params = {
        "sku": str(sku),
        "variant_id": metric.get("variant_id"),
        "title": product_data.get("title"),
        "brand": metric.get("brand") or product_data.get("brand"),
        "category": metric.get("category"),
        "category_ids": metric.get("category_ids"),
        "rfbs_leq_1500": metric.get("rfbs_leq_1500"),
        "rfbs_leq_5000": metric.get("rfbs_leq_5000"),
        "rfbs_gt_5000": metric.get("rfbs_gt_5000"),
        "fbp_leq_1500": metric.get("fbp_leq_1500"),
        "fbp_leq_5000": metric.get("fbp_leq_5000"),
        "fbp_gt_5000": metric.get("fbp_gt_5000"),
        "product_url": product_data.get("product_url"),
        "main_image_url": product_data.get("main_image_url"),
        "price_amount": price_amount,
        "price_currency": price_currency,
        "price_cny": price_to_cny(price_amount, price_currency) if price_amount is not None else None,
        "seller_offer_count": seller_offer_count,
        "sold_count": metric.get("sold_count"),
        "sold_sum_text": metric.get("sold_sum_text"),
        "sold_sum_rub": metric.get("sold_sum_rub"),
        "sold_sum_cny": metric.get("sold_sum_cny"),
        "avg_orders_on_acc_days": metric.get("avg_orders_on_acc_days"),
        "avg_gmv_on_acc_days": metric.get("avg_gmv_on_acc_days"),
        "avg_gmv_on_acc_days_cny": metric.get("avg_gmv_on_acc_days_cny"),
        "sales_dynamics": metric.get("sales_dynamics"),
        "drr": metric.get("drr"),
        "days_in_promo": metric.get("days_in_promo"),
        "discount": metric.get("discount"),
        "promo_revenue_share": metric.get("promo_revenue_share"),
        "days_with_trafarets": metric.get("days_with_trafarets"),
        "qty_view_pdp": metric.get("qty_view_pdp"),
        "conv_to_cart_pdp": metric.get("conv_to_cart_pdp"),
        "session_count_search": metric.get("session_count_search"),
        "conv_to_cart_search": metric.get("conv_to_cart_search"),
        "conv_view_to_order": metric.get("conv_view_to_order"),
        "sales_schema": metric.get("sales_schema"),
        "nullable_redemption_rate": metric.get("nullable_redemption_rate"),
        "custom_click_rate_text": metric.get("custom_click_rate_text"),
        "custom_click_rate": metric.get("custom_click_rate"),
        "custom_volume_text": metric.get("custom_volume_text"),
        "custom_weight_text": metric.get("custom_weight_text"),
        "custom_weight_g": metric.get("custom_weight_g"),
        "nullable_create_date_text": metric.get("nullable_create_date_text"),
        "create_days": metric.get("create_days"),
        "status_update_sales": metric.get("status_update_sales"),
        "status_update_variant": metric.get("status_update_variant"),
        "status_version": metric.get("status_version"),
        "is_formal_qualified": None if formal_rule_result is None else (1 if formal_rule_result.matched else 0),
        "formal_rule_name": formal_rule_result.rule_name if formal_rule_result else None,
        "formal_rule_reason": formal_rule_result.summary if formal_rule_result else None,
        "is_seed_qualified": None if seed_rule_result is None else (1 if seed_rule_result.matched else 0),
        "seed_rule_name": seed_rule_result.rule_name if seed_rule_result else None,
        "seed_rule_reason": seed_rule_result.summary if seed_rule_result else None,
        "maozi_fields_zh_json": json_dumps(maozi_fields_zh) if maozi_fields_zh else None,
        "maozi_raw_json": metric.get("raw_json"),
        "product_raw_json": json_dumps(product_data.get("raw")) if product_data.get("raw") is not None else None,
        "plugin_card_raw_json": json_dumps(
            {
                "seller_offer_count": plugin_card.get("seller_offer_count"),
                "line_map": plugin_card.get("line_map"),
                "card_lines": plugin_card.get("card_lines"),
                "metric_overrides": plugin_card.get("metric_overrides"),
            }
        )
        if plugin_card
        else None,
        "seller_offers_json": json_dumps(offers_json) if offers_json is not None else None,
        "last_maozi_collected_at": datetime.now() if metric else None,
        "last_product_collected_at": datetime.now() if product_data else None,
        "last_plugin_collected_at": datetime.now() if plugin_card else None,
        "last_offer_collected_at": datetime.now() if offers is not None else None,
    }
    db.execute(
        """
        INSERT INTO sku_universe
          (sku, variant_id, title, brand, category, category_ids,
           rfbs_leq_1500, rfbs_leq_5000, rfbs_gt_5000, fbp_leq_1500, fbp_leq_5000, fbp_gt_5000,
           product_url, main_image_url,
           price_amount, price_currency, price_cny, seller_offer_count, sold_count, sold_sum_text,
           sold_sum_rub, sold_sum_cny, avg_orders_on_acc_days, avg_gmv_on_acc_days, avg_gmv_on_acc_days_cny,
           sales_dynamics, drr, days_in_promo, discount, promo_revenue_share, days_with_trafarets, qty_view_pdp,
           conv_to_cart_pdp, session_count_search, conv_to_cart_search, conv_view_to_order, sales_schema,
           nullable_redemption_rate, custom_click_rate_text, custom_click_rate, custom_volume_text,
           custom_weight_text, custom_weight_g, nullable_create_date_text, create_days,
           status_update_sales, status_update_variant, status_version, is_formal_qualified,
           formal_rule_name, formal_rule_reason, is_seed_qualified, seed_rule_name, seed_rule_reason,
           maozi_fields_zh_json, maozi_raw_json, product_raw_json, plugin_card_raw_json, seller_offers_json,
           last_seen_at, last_maozi_collected_at, last_product_collected_at, last_plugin_collected_at, last_offer_collected_at)
        VALUES
          (%(sku)s, %(variant_id)s, %(title)s, %(brand)s, %(category)s, %(category_ids)s,
           %(rfbs_leq_1500)s, %(rfbs_leq_5000)s, %(rfbs_gt_5000)s, %(fbp_leq_1500)s, %(fbp_leq_5000)s, %(fbp_gt_5000)s,
           %(product_url)s, %(main_image_url)s,
           %(price_amount)s, %(price_currency)s, %(price_cny)s, %(seller_offer_count)s, %(sold_count)s, %(sold_sum_text)s,
           %(sold_sum_rub)s, %(sold_sum_cny)s, %(avg_orders_on_acc_days)s, %(avg_gmv_on_acc_days)s, %(avg_gmv_on_acc_days_cny)s,
           %(sales_dynamics)s, %(drr)s, %(days_in_promo)s, %(discount)s, %(promo_revenue_share)s, %(days_with_trafarets)s, %(qty_view_pdp)s,
           %(conv_to_cart_pdp)s, %(session_count_search)s, %(conv_to_cart_search)s, %(conv_view_to_order)s, %(sales_schema)s,
           %(nullable_redemption_rate)s, %(custom_click_rate_text)s, %(custom_click_rate)s, %(custom_volume_text)s,
           %(custom_weight_text)s, %(custom_weight_g)s, %(nullable_create_date_text)s, %(create_days)s,
           COALESCE(%(status_update_sales)s, 0), COALESCE(%(status_update_variant)s, 0), %(status_version)s, COALESCE(%(is_formal_qualified)s, 0),
           %(formal_rule_name)s, %(formal_rule_reason)s, %(is_seed_qualified)s, %(seed_rule_name)s, %(seed_rule_reason)s,
           %(maozi_fields_zh_json)s, %(maozi_raw_json)s, %(product_raw_json)s, %(plugin_card_raw_json)s, %(seller_offers_json)s,
           CURRENT_TIMESTAMP, %(last_maozi_collected_at)s, %(last_product_collected_at)s, %(last_plugin_collected_at)s, %(last_offer_collected_at)s)
        ON DUPLICATE KEY UPDATE
          variant_id=COALESCE(VALUES(variant_id), variant_id),
          title=COALESCE(VALUES(title), title),
          brand=COALESCE(VALUES(brand), brand),
          category=COALESCE(VALUES(category), category),
          category_ids=COALESCE(VALUES(category_ids), category_ids),
          rfbs_leq_1500=COALESCE(VALUES(rfbs_leq_1500), rfbs_leq_1500),
          rfbs_leq_5000=COALESCE(VALUES(rfbs_leq_5000), rfbs_leq_5000),
          rfbs_gt_5000=COALESCE(VALUES(rfbs_gt_5000), rfbs_gt_5000),
          fbp_leq_1500=COALESCE(VALUES(fbp_leq_1500), fbp_leq_1500),
          fbp_leq_5000=COALESCE(VALUES(fbp_leq_5000), fbp_leq_5000),
          fbp_gt_5000=COALESCE(VALUES(fbp_gt_5000), fbp_gt_5000),
          product_url=COALESCE(VALUES(product_url), product_url),
          main_image_url=COALESCE(VALUES(main_image_url), main_image_url),
          price_amount=COALESCE(VALUES(price_amount), price_amount),
          price_currency=COALESCE(VALUES(price_currency), price_currency),
          price_cny=COALESCE(VALUES(price_cny), price_cny),
          seller_offer_count=COALESCE(VALUES(seller_offer_count), seller_offer_count),
          sold_count=COALESCE(VALUES(sold_count), sold_count),
          sold_sum_text=COALESCE(VALUES(sold_sum_text), sold_sum_text),
          sold_sum_rub=COALESCE(VALUES(sold_sum_rub), sold_sum_rub),
          sold_sum_cny=COALESCE(VALUES(sold_sum_cny), sold_sum_cny),
          avg_orders_on_acc_days=COALESCE(VALUES(avg_orders_on_acc_days), avg_orders_on_acc_days),
          avg_gmv_on_acc_days=COALESCE(VALUES(avg_gmv_on_acc_days), avg_gmv_on_acc_days),
          avg_gmv_on_acc_days_cny=COALESCE(VALUES(avg_gmv_on_acc_days_cny), avg_gmv_on_acc_days_cny),
          sales_dynamics=COALESCE(VALUES(sales_dynamics), sales_dynamics),
          drr=COALESCE(VALUES(drr), drr),
          days_in_promo=COALESCE(VALUES(days_in_promo), days_in_promo),
          discount=COALESCE(VALUES(discount), discount),
          promo_revenue_share=COALESCE(VALUES(promo_revenue_share), promo_revenue_share),
          days_with_trafarets=COALESCE(VALUES(days_with_trafarets), days_with_trafarets),
          qty_view_pdp=COALESCE(VALUES(qty_view_pdp), qty_view_pdp),
          conv_to_cart_pdp=COALESCE(VALUES(conv_to_cart_pdp), conv_to_cart_pdp),
          session_count_search=COALESCE(VALUES(session_count_search), session_count_search),
          conv_to_cart_search=COALESCE(VALUES(conv_to_cart_search), conv_to_cart_search),
          conv_view_to_order=COALESCE(VALUES(conv_view_to_order), conv_view_to_order),
          sales_schema=COALESCE(VALUES(sales_schema), sales_schema),
          nullable_redemption_rate=COALESCE(VALUES(nullable_redemption_rate), nullable_redemption_rate),
          custom_click_rate_text=COALESCE(VALUES(custom_click_rate_text), custom_click_rate_text),
          custom_click_rate=COALESCE(VALUES(custom_click_rate), custom_click_rate),
          custom_volume_text=COALESCE(VALUES(custom_volume_text), custom_volume_text),
          custom_weight_text=COALESCE(VALUES(custom_weight_text), custom_weight_text),
          custom_weight_g=COALESCE(VALUES(custom_weight_g), custom_weight_g),
          nullable_create_date_text=COALESCE(VALUES(nullable_create_date_text), nullable_create_date_text),
          create_days=COALESCE(VALUES(create_days), create_days),
          status_update_sales=COALESCE(VALUES(status_update_sales), status_update_sales),
          status_update_variant=COALESCE(VALUES(status_update_variant), status_update_variant),
          status_version=COALESCE(VALUES(status_version), status_version),
          is_formal_qualified=COALESCE(VALUES(is_formal_qualified), is_formal_qualified),
          formal_rule_name=COALESCE(VALUES(formal_rule_name), formal_rule_name),
          formal_rule_reason=COALESCE(VALUES(formal_rule_reason), formal_rule_reason),
          is_seed_qualified=COALESCE(VALUES(is_seed_qualified), is_seed_qualified),
          seed_rule_name=COALESCE(VALUES(seed_rule_name), seed_rule_name),
          seed_rule_reason=COALESCE(VALUES(seed_rule_reason), seed_rule_reason),
          maozi_fields_zh_json=COALESCE(VALUES(maozi_fields_zh_json), maozi_fields_zh_json),
          maozi_raw_json=COALESCE(VALUES(maozi_raw_json), maozi_raw_json),
          product_raw_json=COALESCE(VALUES(product_raw_json), product_raw_json),
          plugin_card_raw_json=COALESCE(VALUES(plugin_card_raw_json), plugin_card_raw_json),
          seller_offers_json=COALESCE(VALUES(seller_offers_json), seller_offers_json),
          last_seen_at=CURRENT_TIMESTAMP,
          last_maozi_collected_at=COALESCE(VALUES(last_maozi_collected_at), last_maozi_collected_at),
          last_product_collected_at=COALESCE(VALUES(last_product_collected_at), last_product_collected_at),
          last_plugin_collected_at=COALESCE(VALUES(last_plugin_collected_at), last_plugin_collected_at),
          last_offer_collected_at=COALESCE(VALUES(last_offer_collected_at), last_offer_collected_at)
        """,
        params,
    )


def bulk_upsert_sku_universe_product_snapshots(entries: list[dict[str, Any]]) -> int:
    rows: list[dict[str, Any]] = []
    now = datetime.now()
    for entry in entries:
        product_data = entry.get("product_data") or {}
        price_amount = to_decimal(product_data.get("price"))
        price_currency = product_data.get("currency")
        rows.append(
            {
                "sku": str(entry.get("sku") or ""),
                "title": product_data.get("title"),
                "brand": product_data.get("brand"),
                "product_url": product_data.get("product_url"),
                "main_image_url": product_data.get("main_image_url"),
                "price_amount": price_amount,
                "price_currency": price_currency,
                "price_cny": price_to_cny(price_amount, price_currency) if price_amount is not None else None,
                "product_raw_json": json_dumps(product_data.get("raw")) if product_data.get("raw") is not None else None,
                "last_product_collected_at": now if product_data else None,
            }
        )
    rows = [row for row in rows if row["sku"]]
    if not rows:
        return 0
    return db.execute_insert_many(
        """
        INSERT INTO sku_universe
          (sku, title, brand, product_url, main_image_url, price_amount, price_currency, price_cny,
           product_raw_json, last_seen_at, last_product_collected_at)
        VALUES
          (%(sku)s, %(title)s, %(brand)s, %(product_url)s, %(main_image_url)s, %(price_amount)s, %(price_currency)s, %(price_cny)s,
           %(product_raw_json)s, CURRENT_TIMESTAMP, %(last_product_collected_at)s)
        ON DUPLICATE KEY UPDATE
          title=COALESCE(VALUES(title), title),
          brand=COALESCE(VALUES(brand), brand),
          product_url=COALESCE(VALUES(product_url), product_url),
          main_image_url=COALESCE(VALUES(main_image_url), main_image_url),
          price_amount=COALESCE(VALUES(price_amount), price_amount),
          price_currency=COALESCE(VALUES(price_currency), price_currency),
          price_cny=COALESCE(VALUES(price_cny), price_cny),
          product_raw_json=COALESCE(VALUES(product_raw_json), product_raw_json),
          last_seen_at=CURRENT_TIMESTAMP,
          last_product_collected_at=COALESCE(VALUES(last_product_collected_at), last_product_collected_at)
        """,
        rows,
        batch_size=200,
    )


def bulk_upsert_sku_results(
    results: list[dict[str, Any]],
    *,
    source: str,
) -> None:
    """批量更新 SKU 处理结果，包括指标、产品快照和种子表状态。"""
    if not results:
        return

    metric_rows = []
    product_rows = []
    seed_status_rows = []
    universe_entries = []

    now = datetime.now()

    for item in results:
        sku = item["sku"]
        sku_result = item["sku_result"]
        metric = sku_result.get("metric")
        product_snapshot = sku_result.get("product_snapshot")
        selection_result = sku_result.get("selection_result")
        plugin_card = sku_result.get("plugin_card") or {}
        seller_offer_count = sku_result.get("seller_offer_count")
        
        # 1. 准备指标行
        if metric:
            m_row = dict(metric)
            m_row["collected_at"] = now
            metric_rows.append(m_row)
        
        # 2. 准备产品快照行 (仅达标 SKU)
        if selection_result and selection_result.matched and metric:
            p_row = {
                "sku": sku,
                "variant_id": metric.get("variant_id"),
                "product_url": product_snapshot.get("product_url") if product_snapshot else None,
                "title": product_snapshot.get("title") if product_snapshot else None,
                "brand": metric.get("brand") or (product_snapshot.get("brand") if product_snapshot else None),
                "category": metric.get("category"),
                "category_ids": metric.get("category_ids"),
                "price": product_snapshot.get("price") if product_snapshot else None,
                "currency": product_snapshot.get("currency") if product_snapshot else None,
                "main_image_url": product_snapshot.get("main_image_url") if product_snapshot else None,
                "sold_count": metric.get("sold_count"),
                "sold_sum_rub": metric.get("sold_sum_rub"),
                "sold_sum_cny": metric.get("sold_sum_cny"),
                "sales_dynamics": metric.get("sales_dynamics"),
                "maozi_fields_zh_json": build_maozi_fields_zh(metric, plugin_card=plugin_card, seller_offer_count=seller_offer_count),
                "maozi_collected_at": now,
            }
            # 补齐其它 rfbs/fbp 字段... 这里简化一下，实际按需补全
            for f in ["rfbs_leq_1500", "rfbs_leq_5000", "rfbs_gt_5000", "fbp_leq_1500", "fbp_leq_5000", "fbp_gt_5000", 
                      "avg_orders_on_acc_days", "avg_gmv_on_acc_days", "avg_gmv_on_acc_days_cny", "drr", "days_in_promo", "discount"]:
                p_row[f] = metric.get(f)
            product_rows.append(p_row)

        # 3. 准备种子表状态
        status = "qualified" if (selection_result and selection_result.matched) else "rejected"
        if sku_result.get("transient_failed"):
            status = "failed"
        
        seed_status_rows.append({
            "sku": sku,
            "status": status,
            "reason": sku_result.get("rule_reason") or "批量处理",
            "now": now,
        })

        # 4. 准备 universe 更新
        store_universe_entry = (
            bool(metric)
            or not source.startswith("seller_home:")
            or settings.seller_store_deferred_universe
        )
        if store_universe_entry and (product_snapshot or metric):
            universe_entries.append({
                "sku": sku,
                "product_data": product_snapshot,
                "metric": metric,
                "plugin_card": plugin_card,
                "seller_offer_count": seller_offer_count,
                "selection_result": selection_result,
            })

    # 执行批量写入
    if metric_rows:
        columns = [c for c in metric_rows[0].keys()]
        updates = [f"{c}=VALUES({c})" for c in columns if c not in ("sku", "collected_at")]
        sql = f"""
            INSERT INTO sku_plugin_metrics ({",".join(columns)})
            VALUES ({",".join("%(" + c + ")s" for c in columns)})
            ON DUPLICATE KEY UPDATE {",".join(updates)}, collected_at=VALUES(collected_at)
        """
        db.execute_insert_many(sql, metric_rows, batch_size=200)

    if product_rows:
        columns = [c for c in product_rows[0].keys()]
        updates = [f"{c}=VALUES({c})" for c in columns if c not in ("sku", "maozi_collected_at")]
        sql = f"""
            INSERT INTO sku_products ({",".join(columns)})
            VALUES ({",".join("%(" + c + ")s" for c in columns)})
            ON DUPLICATE KEY UPDATE {",".join(updates)}, maozi_collected_at=VALUES(maozi_collected_at)
        """
        db.execute_insert_many(sql, product_rows, batch_size=200)

    if seed_status_rows and not source.startswith("seller_home:"):
        db.execute_many(
            """
            UPDATE seed_pool_skus 
            SET last_process_status=%(status)s, last_process_reason=%(reason)s, updated_at=%(now)s 
            WHERE sku=%(sku)s
            """,
            seed_status_rows
        )
    
    if universe_entries:
        # 这里需要一个新的 bulk 函数来处理完整的 universe 更新
        bulk_upsert_sku_universe_full(universe_entries)


def bulk_upsert_sku_universe_full(entries: list[dict[str, Any]]) -> None:
    rows = []
    now = datetime.now()
    for entry in entries:
        sku = entry["sku"]
        product_data = entry.get("product_data") or {}
        metric = entry.get("metric") or {}
        plugin_card = entry.get("plugin_card") or {}
        seller_offer_count = entry.get("seller_offer_count")
        selection_result = entry.get("selection_result")
        
        price_amount = to_decimal(product_data.get("price"))
        price_currency = product_data.get("currency")
        
        rows.append({
            "sku": sku,
            "title": product_data.get("title"),
            "brand": metric.get("brand") or product_data.get("brand"),
            "product_url": product_data.get("product_url"),
            "main_image_url": product_snapshot_main_image(product_data, plugin_card),
            "price_amount": price_amount,
            "price_currency": price_currency,
            "price_cny": price_to_cny(price_amount, price_currency) if price_amount else None,
            "sold_count": metric.get("sold_count"),
            "sold_sum_rub": metric.get("sold_sum_rub"),
            "sold_sum_cny": metric.get("sold_sum_cny"),
            "seller_offer_count": seller_offer_count,
            "formal_rule_name": selection_result.rule_name if selection_result else None,
            "is_formal_qualified": 1 if (selection_result and selection_result.matched) else 0,
            "formal_rule_reason": selection_result.summary if selection_result else None,
            "product_raw_json": json_dumps(product_data.get("raw")) if product_data.get("raw") else None,
            "maozi_raw_json": json_dumps(entry.get("metric_raw")) if entry.get("metric_raw") else None,
            "last_seen_at": now,
            "last_product_collected_at": now if product_data else None,
            "last_maozi_collected_at": now if metric else None,
        })

    if not rows:
        return

    columns = [c for c in rows[0].keys()]
    updates = [f"{c}=VALUES({c})" for c in columns if c not in ("sku", "last_seen_at")]
    sql = f"""
        INSERT INTO sku_universe ({",".join(columns)})
        VALUES ({",".join("%(" + c + ")s" for c in columns)})
        ON DUPLICATE KEY UPDATE {",".join(updates)}, last_seen_at=VALUES(last_seen_at)
    """
    db.execute_insert_many(sql, rows, batch_size=200)


def product_snapshot_main_image(product_data: dict[str, Any], plugin_card: dict[str, Any]) -> str | None:
    return plugin_card.get("main_image_url") or product_data.get("main_image_url")


def upsert_sku3_response(
    sku: str,
    response: dict[str, Any],
    product_data: dict[str, Any] | None = None,
    seller_offer_count: int | None = None,
    metric_overrides: dict[str, Any] | None = None,
    plugin_card: dict[str, Any] | None = None,
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
    if metric["status_update_sales"] or metric["status_update_variant"]:
        enqueue_task(
            "playwright_refresh",
            f"playwright_refresh:{sku}",
            {"sku": str(sku), "reason": "sku3 requested fresh seller data"},
            priority=50,
        )
    if selection_result is not None and not selection_result.matched:
        return {
            **metric,
            "qualified": False,
            "rule_name": selection_result.rule_name,
            "rule_reason": selection_result.summary,
        }

    if selection_result is not None and selection_result.matched:
        upsert_sku_product(
            metric,
            product_data=product_data,
            plugin_card=plugin_card,
            seller_offer_count=seller_offer_count,
        )

    columns = list(metric.keys())
    updates = [f"{col}=VALUES({col})" for col in columns if col != "sku"]
    sql = f"""
        INSERT INTO sku_plugin_metrics ({",".join(columns)}, collected_at)
        VALUES ({",".join("%(" + col + ")s" for col in columns)}, CURRENT_TIMESTAMP)
        ON DUPLICATE KEY UPDATE {",".join(updates)}, collected_at=CURRENT_TIMESTAMP
    """
    db.execute(sql, metric)
    if selection_result is not None:
        return {
            **metric,
            "qualified": True,
            "rule_name": selection_result.rule_name,
            "rule_reason": selection_result.summary,
        }
    return metric


def upsert_sku_product(
    metric: dict[str, Any],
    product_data: dict[str, Any] | None = None,
    plugin_card: dict[str, Any] | None = None,
    seller_offer_count: int | None = None,
) -> None:
    product_data = product_data or {}
    maozi_fields_zh = build_maozi_fields_zh(
        metric,
        plugin_card=plugin_card,
        seller_offer_count=seller_offer_count,
    )
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
        "avg_orders_on_acc_days": metric.get("avg_orders_on_acc_days"),
        "avg_gmv_on_acc_days": metric.get("avg_gmv_on_acc_days"),
        "avg_gmv_on_acc_days_cny": metric.get("avg_gmv_on_acc_days_cny"),
        "sales_dynamics": metric.get("sales_dynamics"),
        "drr": metric.get("drr"),
        "days_in_promo": metric.get("days_in_promo"),
        "discount": metric.get("discount"),
        "promo_revenue_share": metric.get("promo_revenue_share"),
        "days_with_trafarets": metric.get("days_with_trafarets"),
        "qty_view_pdp": metric.get("qty_view_pdp"),
        "session_count_search": metric.get("session_count_search"),
        "conv_to_cart_pdp": metric.get("conv_to_cart_pdp"),
        "conv_to_cart_search": metric.get("conv_to_cart_search"),
        "conv_view_to_order": metric.get("conv_view_to_order"),
        "sales_schema": metric.get("sales_schema"),
        "nullable_redemption_rate": metric.get("nullable_redemption_rate"),
        "custom_click_rate_text": metric.get("custom_click_rate_text"),
        "custom_click_rate": metric.get("custom_click_rate"),
        "custom_volume_text": metric.get("custom_volume_text"),
        "custom_weight_text": metric.get("custom_weight_text"),
        "custom_weight_g": metric.get("custom_weight_g"),
        "nullable_create_date_text": metric.get("nullable_create_date_text"),
        "create_days": metric.get("create_days"),
        "status_update_sales": metric.get("status_update_sales"),
        "status_update_variant": metric.get("status_update_variant"),
        "status_version": metric.get("status_version"),
        "maozi_fields_zh_json": json_dumps(maozi_fields_zh) if maozi_fields_zh else None,
        "raw_maozi_json": metric.get("raw_json"),
        "raw_frontend_json": json_dumps(product_data.get("raw")) if product_data.get("raw") is not None else None,
        "maozi_collected_at": datetime.now() if metric else None,
    }
    db.execute(
        """
        INSERT INTO sku_products
          (sku, variant_id, product_url, title, brand, category, category_ids,
           price, currency, main_image_url,
           rfbs_leq_1500, rfbs_leq_5000, rfbs_gt_5000, fbp_leq_1500, fbp_leq_5000, fbp_gt_5000,
           sold_count, sold_sum_text, sold_sum_rub, sold_sum_cny, avg_orders_on_acc_days,
           avg_gmv_on_acc_days, avg_gmv_on_acc_days_cny, sales_dynamics, drr, days_in_promo,
           discount, promo_revenue_share, days_with_trafarets, qty_view_pdp, session_count_search,
           conv_to_cart_pdp, conv_to_cart_search, conv_view_to_order, sales_schema,
           nullable_redemption_rate, custom_click_rate_text, custom_click_rate, custom_volume_text,
           custom_weight_text, custom_weight_g, nullable_create_date_text, create_days,
           status_update_sales, status_update_variant, status_version,
           maozi_fields_zh_json, raw_maozi_json, raw_frontend_json, maozi_collected_at, last_seen_at)
        VALUES
          (%(sku)s, %(variant_id)s, %(product_url)s, %(title)s, %(brand)s, %(category)s, %(category_ids)s,
           %(price)s, %(currency)s, %(main_image_url)s,
           %(rfbs_leq_1500)s, %(rfbs_leq_5000)s, %(rfbs_gt_5000)s, %(fbp_leq_1500)s, %(fbp_leq_5000)s, %(fbp_gt_5000)s,
           %(sold_count)s, %(sold_sum_text)s, %(sold_sum_rub)s, %(sold_sum_cny)s, %(avg_orders_on_acc_days)s,
           %(avg_gmv_on_acc_days)s, %(avg_gmv_on_acc_days_cny)s, %(sales_dynamics)s, %(drr)s, %(days_in_promo)s,
           %(discount)s, %(promo_revenue_share)s, %(days_with_trafarets)s, %(qty_view_pdp)s, %(session_count_search)s,
           %(conv_to_cart_pdp)s, %(conv_to_cart_search)s, %(conv_view_to_order)s, %(sales_schema)s,
           %(nullable_redemption_rate)s, %(custom_click_rate_text)s, %(custom_click_rate)s, %(custom_volume_text)s,
           %(custom_weight_text)s, %(custom_weight_g)s, %(nullable_create_date_text)s, %(create_days)s,
           COALESCE(%(status_update_sales)s, 0), COALESCE(%(status_update_variant)s, 0), %(status_version)s,
           %(maozi_fields_zh_json)s, %(raw_maozi_json)s, %(raw_frontend_json)s, %(maozi_collected_at)s, CURRENT_TIMESTAMP)
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
          rfbs_leq_1500=COALESCE(VALUES(rfbs_leq_1500), rfbs_leq_1500),
          rfbs_leq_5000=COALESCE(VALUES(rfbs_leq_5000), rfbs_leq_5000),
          rfbs_gt_5000=COALESCE(VALUES(rfbs_gt_5000), rfbs_gt_5000),
          fbp_leq_1500=COALESCE(VALUES(fbp_leq_1500), fbp_leq_1500),
          fbp_leq_5000=COALESCE(VALUES(fbp_leq_5000), fbp_leq_5000),
          fbp_gt_5000=COALESCE(VALUES(fbp_gt_5000), fbp_gt_5000),
          sold_count=COALESCE(VALUES(sold_count), sold_count),
          sold_sum_text=COALESCE(VALUES(sold_sum_text), sold_sum_text),
          sold_sum_rub=COALESCE(VALUES(sold_sum_rub), sold_sum_rub),
          sold_sum_cny=COALESCE(VALUES(sold_sum_cny), sold_sum_cny),
          avg_orders_on_acc_days=COALESCE(VALUES(avg_orders_on_acc_days), avg_orders_on_acc_days),
          avg_gmv_on_acc_days=COALESCE(VALUES(avg_gmv_on_acc_days), avg_gmv_on_acc_days),
          avg_gmv_on_acc_days_cny=COALESCE(VALUES(avg_gmv_on_acc_days_cny), avg_gmv_on_acc_days_cny),
          sales_dynamics=COALESCE(VALUES(sales_dynamics), sales_dynamics),
          drr=COALESCE(VALUES(drr), drr),
          days_in_promo=COALESCE(VALUES(days_in_promo), days_in_promo),
          discount=COALESCE(VALUES(discount), discount),
          promo_revenue_share=COALESCE(VALUES(promo_revenue_share), promo_revenue_share),
          days_with_trafarets=COALESCE(VALUES(days_with_trafarets), days_with_trafarets),
          qty_view_pdp=COALESCE(VALUES(qty_view_pdp), qty_view_pdp),
          session_count_search=COALESCE(VALUES(session_count_search), session_count_search),
          conv_to_cart_pdp=COALESCE(VALUES(conv_to_cart_pdp), conv_to_cart_pdp),
          conv_to_cart_search=COALESCE(VALUES(conv_to_cart_search), conv_to_cart_search),
          conv_view_to_order=COALESCE(VALUES(conv_view_to_order), conv_view_to_order),
          sales_schema=COALESCE(VALUES(sales_schema), sales_schema),
          nullable_redemption_rate=COALESCE(VALUES(nullable_redemption_rate), nullable_redemption_rate),
          custom_click_rate_text=COALESCE(VALUES(custom_click_rate_text), custom_click_rate_text),
          custom_click_rate=COALESCE(VALUES(custom_click_rate), custom_click_rate),
          custom_volume_text=COALESCE(VALUES(custom_volume_text), custom_volume_text),
          custom_weight_text=COALESCE(VALUES(custom_weight_text), custom_weight_text),
          custom_weight_g=COALESCE(VALUES(custom_weight_g), custom_weight_g),
          nullable_create_date_text=COALESCE(VALUES(nullable_create_date_text), nullable_create_date_text),
          create_days=COALESCE(VALUES(create_days), create_days),
          status_update_sales=VALUES(status_update_sales),
          status_update_variant=VALUES(status_update_variant),
          status_version=COALESCE(VALUES(status_version), status_version),
          maozi_fields_zh_json=COALESCE(VALUES(maozi_fields_zh_json), maozi_fields_zh_json),
          raw_maozi_json=COALESCE(VALUES(raw_maozi_json), raw_maozi_json),
          raw_frontend_json=COALESCE(VALUES(raw_frontend_json), raw_frontend_json),
          maozi_collected_at=COALESCE(VALUES(maozi_collected_at), maozi_collected_at),
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
    record_sku_discovery(
        sku,
        source_type="top_list",
        source_key=f"{query_key}:{page_no}:{page_rank}",
        source_url=params.get("link"),
        source_name="top_list",
        source_rank=page_rank,
        raw=item,
    )
    upsert_sku_universe(
        sku,
        product_data={
            "product_url": params.get("link"),
            "title": params.get("name"),
            "brand": params.get("brand"),
            "price": params.get("avg_price"),
            "currency": "RUB",
            "main_image_url": params.get("photo"),
            "raw": {"top_list": item},
        },
        metric=top_list_metric_preview(item),
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


def list_seed_pool_skus(
    query_key: str | None = None,
    source_type: str = "top_list",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    resolved_limit = max(1, int(limit or settings.seed_pool_query_limit))
    if query_key:
        return db.fetch_all(
            """
            SELECT source_type, query_key, sku, page_no, page_rank, snapshot_hash, last_processed_snapshot_hash,
                   last_process_status, last_process_reason, last_offer_count, last_selected_at, last_processed_at,
                   name, brand, link, photo, sold_count, sold_sum, avg_price, sales_dynamics,
                   sales_schema, weight, nullable_create_date, upstream_update_time, last_seen_at
            FROM seed_pool_skus
            WHERE source_type=%(source_type)s
              AND query_key=%(query_key)s
            ORDER BY last_seen_at DESC, page_no ASC, page_rank ASC, sku ASC
            LIMIT %(limit)s
            """,
            {"source_type": source_type, "query_key": query_key, "limit": resolved_limit},
        )
    return db.fetch_all(
        """
        SELECT source_type, query_key, sku, page_no, page_rank, snapshot_hash, last_processed_snapshot_hash,
               last_process_status, last_process_reason, last_offer_count, last_selected_at, last_processed_at,
               name, brand, link, photo, sold_count, sold_sum, avg_price, sales_dynamics,
               sales_schema, weight, nullable_create_date, upstream_update_time, last_seen_at
        FROM seed_pool_skus
        WHERE source_type=%(source_type)s
          AND COALESCE(last_process_status, '') <> 'deferred'
        ORDER BY last_seen_at DESC, query_key ASC, page_no ASC, page_rank ASC, sku ASC
        LIMIT %(limit)s
        """,
        {"source_type": source_type, "limit": resolved_limit},
    )


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
        reason = str(item.get("last_process_reason") or "")
        if "failed to fetch maozi sku3 for top-list sku" in reason:
            return True, "failed_retry_infra_immediate"
        if last_processed_at <= now - timedelta(hours=settings.top_list_recheck_failed_hours):
            return True, "failed_retry_due"
        return False, "failed_retry_not_due"
    if status == "deferred":
        return False, "deferred_retry_paused"
    if status in {"rejected", "done"}:
        if last_processed_at <= now - timedelta(days=settings.top_list_recheck_rejected_days):
            return True, "rejected_ttl_expired"
        return False, "rejected_ttl_not_due"
    return True, "pending_or_unknown"


def repair_seed_pool_failed_statuses(
    *,
    source_type: str = "top_list",
    query_key: str | None = None,
    only_maozi_fetch_failures: bool = True,
) -> int:
    where = [
        "source_type=%(source_type)s",
        "last_process_status='failed'",
    ]
    params: dict[str, Any] = {"source_type": source_type}
    if query_key:
        where.append("query_key=%(query_key)s")
        params["query_key"] = query_key
    if only_maozi_fetch_failures:
        where.append("last_process_reason LIKE %(reason_like)s")
        params["reason_like"] = "%failed to fetch maozi sku3 for top-list sku%"
    return db.execute(
        f"""
        UPDATE seed_pool_skus
        SET last_processed_snapshot_hash=NULL,
            last_process_status=NULL,
            last_process_reason=NULL,
            last_offer_count=NULL,
            last_selected_at=NULL,
            last_processed_at=NULL
        WHERE {' AND '.join(where)}
        """,
        params,
    )


def repair_seed_pool_offer_missing_rejections(
    *,
    source_type: str = "top_list",
    query_key: str | None = None,
) -> int:
    where = [
        "source_type=%(source_type)s",
        "last_process_status='rejected'",
        "last_process_reason LIKE %(reason_like)s",
    ]
    params: dict[str, Any] = {
        "source_type": source_type,
        "reason_like": "%跟卖人数缺失%",
    }
    if query_key:
        where.append("query_key=%(query_key)s")
        params["query_key"] = query_key
    return db.execute(
        f"""
        UPDATE seed_pool_skus
        SET last_processed_snapshot_hash=NULL,
            last_process_status=NULL,
            last_process_reason=NULL,
            last_offer_count=NULL,
            last_selected_at=NULL,
            last_processed_at=NULL
        WHERE {' AND '.join(where)}
        """,
        params,
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


def list_due_seller_shops(process_limit: int = 0) -> list[dict[str, Any]]:
    sql = """
        SELECT seller_key, name, home_url, logo_url, last_collected_at, next_collect_after
        FROM seller_shops
        WHERE home_url IS NOT NULL
          AND home_url <> ''
          AND (last_collected_at IS NULL OR next_collect_after IS NULL OR next_collect_after <= CURRENT_TIMESTAMP)
        ORDER BY
          CASE WHEN last_collected_at IS NULL THEN 0 ELSE 1 END ASC,
          COALESCE(next_collect_after, TIMESTAMP('1970-01-01 00:00:00')) ASC,
          seller_key ASC
    """
    params: dict[str, Any] = {}
    if process_limit and int(process_limit) > 0:
        sql += "\nLIMIT %(limit)s"
        params["limit"] = int(process_limit)
    return db.fetch_all(sql, params or None)


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
    offer_sku = str(offer.get("offer_sku") or extract_sku_from_product_url(offer.get("offer_product_url") or "") or "").strip()
    if offer_sku:
        record_sku_discovery(
            offer_sku,
            source_type="seller_offer",
            source_key=f"{source_sku}:{key}",
            source_url=home_url,
            source_name=offer.get("name"),
            related_sku=str(source_sku),
            raw=offer,
        )
        upsert_sku_universe(
            offer_sku,
            product_data={
                "product_url": offer.get("offer_product_url"),
                "title": None,
                "brand": None,
                "price": offer.get("price_amount"),
                "currency": offer.get("currency"),
                "main_image_url": offer.get("main_image_url"),
                "raw": {"seller_offer": offer},
            },
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
    record_sku_discovery(
        sku,
        source_type="seller_home",
        source_key=key,
        source_url=seller_home_url,
        raw=item,
    )
    upsert_sku_universe(
        sku,
        product_data={
            "product_url": params.get("product_url"),
            "title": params.get("title"),
            "price": params.get("price_amount"),
            "currency": params.get("currency"),
            "main_image_url": params.get("main_image_url"),
            "raw": {"seller_home": item},
        },
    )
    return sku


def prepare_seller_home_sku_rows(seller_home_url: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    key = seller_key(seller_home_url)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        sku = extract_sku_from_product_url(item.get("href") or item.get("product_url") or "")
        if not sku or sku in seen:
            continue
        seen.add(sku)
        product_url = item.get("href") or item.get("product_url")
        title = item.get("title")
        price_amount = to_decimal(item.get("price_amount"))
        currency = item.get("currency")
        main_image_url = item.get("image_url") or item.get("main_image_url")
        raw_json = None if settings.seller_fast_mode else json_dumps(item)
        rows.append(
            {
                "seller_key": key,
                "sku": sku,
                "product_url": product_url,
                "title": title,
                "price_amount": price_amount,
                "currency": currency,
                "main_image_url": main_image_url,
                "raw_json": raw_json,
                "source_type": "seller_home",
                "source_key": key,
                "source_url": seller_home_url,
                "source_name": None,
                "related_sku": None,
                "source_rank": None,
                "product_data": {
                    "product_url": product_url,
                    "title": title,
                    "price": price_amount,
                    "currency": currency,
                    "main_image_url": main_image_url,
                    "raw": {"seller_home": item},
                },
            }
        )
    return rows


def bulk_upsert_seller_home_skus(seller_home_url: str, items: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    rows = prepare_seller_home_sku_rows(seller_home_url, items)
    if not rows:
        return []
    db.execute_insert_many(
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
          raw_json=COALESCE(VALUES(raw_json), raw_json),
          updated_at=CURRENT_TIMESTAMP
        """,
        rows,
        batch_size=200,
    )
    if settings.seller_fast_mode:
        return [(row["sku"], row["product_data"]["raw"]["seller_home"]) for row in rows]
    db.execute_insert_many(
        """
        INSERT INTO sku_discovery_sources
          (sku, source_type, source_key, source_url, source_name, related_sku, source_rank, raw_json, last_seen_at)
        VALUES
          (%(sku)s, %(source_type)s, %(source_key)s, %(source_url)s, %(source_name)s, %(related_sku)s, %(source_rank)s, %(raw_json)s, CURRENT_TIMESTAMP)
        ON DUPLICATE KEY UPDATE
          source_url=COALESCE(VALUES(source_url), source_url),
          source_name=COALESCE(VALUES(source_name), source_name),
          related_sku=COALESCE(VALUES(related_sku), related_sku),
          source_rank=COALESCE(VALUES(source_rank), source_rank),
          raw_json=COALESCE(VALUES(raw_json), raw_json),
          last_seen_at=CURRENT_TIMESTAMP
        """,
        rows,
        batch_size=200,
    )
    bulk_upsert_sku_universe_product_snapshots(rows)
    return [(row["sku"], row["product_data"]["raw"]["seller_home"]) for row in rows]


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
