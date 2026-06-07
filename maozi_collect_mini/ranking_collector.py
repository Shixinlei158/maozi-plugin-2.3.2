"""榜单采集脚本。

功能：
- 从毛子ERP榜单API拉取商品列表（每页50条定死）
- 按配置的类目层级(0/1/2/3)和页数遍历
- 对每页数据执行种子预筛选，合格商品写入seed_pool_skus
- 进一步获取SKU3详情，判定合格后记录卖家到seller_shops
- **不写入 sku_products**，合格商品入库仅由卖家主页采集负责
- 遇到个别数据不可获取直接跳过
- 遇到整体token失效尝试通过纠错解决
- 采集完成后返回结果，以便main.py自动切换到卖家模式
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from .browser import BrowserClient
from .config import settings
from .maozi_api import MaoziClient
from .repository import (
    bulk_upsert_seed_pool,
    query_key_from_filters,
    list_categories_by_level,
    upsert_seller_shop,
    parse_sku3_response,
    mark_seed_status,
)
from .rules import (
    evaluate_top_list_prefilter,
    evaluate_selection_rule,
    get_top_list_seed_rule,
    DEFAULT_SELECTION_RULE,
)


def _extract_sku(item: dict[str, Any]) -> str:
    return str(item.get("sku") or "").strip()


def _summary_page(item: dict[str, Any]) -> str:
    return (
        f"sku={_extract_sku(item)} "
        f"sold={item.get('sold_count', '?')} "
        f"price={item.get('avg_price', '?')} "
        f"schema={item.get('sales_schema', '?')}"
    )


def _build_base_filters(config: dict[str, Any]) -> dict[str, Any]:
    """从 config.json 构建榜单API基础过滤参数"""
    filters: dict[str, Any] = {
        "mainType": config.get("main_type", "hot"),
    }

    for field in [
        "sales_min", "sales_max", "day_sales_min", "day_sales_max",
        "avg_price_min", "avg_price_max", "sales_dynamics_min", "sales_dynamics_max",
        "conv_to_cart_pdp_min", "conv_to_cart_pdp_max",
        "conv_to_cart_search_min", "conv_to_cart_search_max",
        "sales_schema", "sold_sum_min", "sold_sum_max",
        "weight_min", "weight_max",
        "avg_delivery_days_min", "avg_delivery_days_max",
        "sort_by", "sort_order",
    ]:
        val = config.get(field)
        if val and str(val).strip():
            filters[field] = str(val).strip()

    # 上架日期
    create_from = config.get("create_date_from") or "2025-04-01"
    create_to = config.get("create_date_to") or datetime.now().strftime("%Y-%m-%d")
    filters["create_date"] = [create_from.strip(), create_to.strip()]

    return filters


def run_ranking_collection(config: dict[str, Any]) -> dict[str, int]:
    """执行榜单采集。

    参数:
        config: GUI配置字典，包含 category_level, page_from, page_to, 各类目/过滤参数等

    返回:
        {"seeds_collected": N, "products_qualified": N, "pages_fetched": N, "errors": N}
    """
    category_level = int(config.get("category_level", 1))
    page_from = int(config.get("page_from", 1))
    page_to = int(config.get("page_to", 100))
    main_type = config.get("main_type", "hot")

    print(f"===== 开始榜单采集: type={main_type}, level={category_level}, pages={page_from}-{page_to} =====")

    browser = BrowserClient()
    maozi = MaoziClient()
    stats = {"seeds_collected": 0, "products_qualified": 0, "pages_fetched": 0, "errors": 0}

    try:
        browser.open_session()

        authenticated = browser.ensure_authenticated(max_retries=3)
        if not authenticated:
            print("ERROR: 毛子ERP登录态验证失败，请检查浏览器登录状态")
            return stats

        base_filters = _build_base_filters(config)

        # 根据类目层级确定要采集的类目列表
        category_ids_to_collect: list[tuple[str | None, str | None, str | None, str]] = []
        if category_level == 0:
            # Level 0: 不选类目，采集整个榜单
            category_ids_to_collect.append((None, None, None, "all"))
        elif category_level == 1:
            categories = list_categories_by_level(1)
            for cat in categories:
                cat_name = cat.get("name_zh", "")
                if not cat_name:
                    continue
                category_ids_to_collect.append((cat_name, None, None, cat_name))
        elif category_level == 2:
            categories = list_categories_by_level(2)
            for cat in categories:
                cat_name = cat.get("name_zh", "")
                if not cat_name:
                    continue
                category_ids_to_collect.append((None, cat_name, None, cat_name))
        elif category_level == 3:
            categories = list_categories_by_level(3)
            for cat in categories:
                cat_name = cat.get("name_zh", "")
                if not cat_name:
                    continue
                category_ids_to_collect.append((None, None, cat_name, cat_name))
        else:
            category_ids_to_collect.append((None, None, None, "all"))

        print(f"共 {len(category_ids_to_collect)} 个类目需要采集")

        for cat1, cat2, cat3, cat_label in category_ids_to_collect:
            print(f"\n--- 采集类目: {cat_label} ---")

            filters = dict(base_filters)
            if cat1:
                filters["category1"] = cat1
            if cat2:
                filters["category2"] = cat2
            if cat3:
                filters["category3"] = cat3

            query_key = query_key_from_filters(filters)

            for page_no in range(page_from, page_to + 1):
                try:
                    # 认证检查：每10页检查一次
                    if page_no % 10 == 0:
                        browser.ensure_authenticated(max_retries=1)

                    result = browser.fetch_top_list_page(filters, page_no, page_size=50)
                    stats["pages_fetched"] += 1

                    if not result.get("ok"):
                        error_msg = result.get("error", str(result))
                        # Token失效检测
                        if "401" in error_msg or "unauthorized" in error_msg.lower():
                            print(f"page {page_no}: Token失效，尝试重新登录...")
                            browser.ensure_authenticated(max_retries=3)
                            result = browser.fetch_top_list_page(filters, page_no, page_size=50)
                            if not result.get("ok"):
                                print(f"page {page_no}: Token恢复后仍失败，跳过")
                                stats["errors"] += 1
                                continue
                        else:
                            print(f"page {page_no}: API错误 {error_msg}")
                            stats["errors"] += 1
                            continue

                    data = result.get("data", {})
                    items = data.get("list") or data.get("items") or []
                    if not items:
                        # API 可能包裹一层 {"data": {"list": [...]}}，再往里探一层
                        inner = data.get("data") or {}
                        items = inner.get("list") or inner.get("items") or inner.get("data") or []
                    if not items:
                        print(f"page {page_no}: 无数据，结束此类目")
                        break

                    # 为每条数据附加页数信息
                    for rank, item in enumerate(items):
                        item["page_no"] = page_no
                        item["page_rank"] = rank + 1

                    # 种子预筛选 → 种子池
                    seed_rule = get_top_list_seed_rule()
                    seed_items = []
                    for item in items:
                        sku = _extract_sku(item)
                        if not sku:
                            continue
                        prefilter = evaluate_top_list_prefilter(item, rule=seed_rule)
                        if prefilter.matched:
                            seed_items.append(item)

                    if seed_items:
                        saved = bulk_upsert_seed_pool(seed_items, query_key)
                        stats["seeds_collected"] += saved
                        print(
                            f"page {page_no}/{cat_label}: {len(items)}条, "
                            f"种子预筛通过{len(seed_items)}条"
                        )

                    # SKU3详情获取 → 合格商品判定
                    try:
                        skus_to_fetch = [_extract_sku(item) for item in seed_items[:60]]
                        sku3_results = browser.fetch_sku3_batch(
                            skus_to_fetch,
                            concurrency=settings.top_list_sku3_batch_concurrency,
                            chunk_delay_ms=settings.top_list_sku3_batch_chunk_delay_ms,
                        )

                        for item in seed_items:
                            sku = _extract_sku(item)
                            sku3_response = sku3_results.get(sku)
                            if not sku3_response or "_error" in sku3_response:
                                continue

                            try:
                                metric = parse_sku3_response(sku, sku3_response)
                            except Exception:
                                continue

                            product_snapshot = {
                                "product_url": item.get("link"),
                                "title": item.get("name"),
                                "brand": metric.get("brand") or item.get("brand"),
                                "price": item.get("avg_price"),
                                "currency": "RUB",
                                "main_image_url": item.get("photo"),
                                "raw": {"top_list_item": item},
                            }

                            seller_offer_count = None  # 榜单模式不获取跟卖
                            result_obj = evaluate_selection_rule(
                                metric, product_snapshot, seller_offer_count
                            )

                            if result_obj.matched:
                                # 榜单采集不写入 sku_products，仅通过卖家主页采集写入
                                # 只发现并记录卖家
                                stats["products_qualified"] += 1

                                # 将卖家信息写入seller_shops
                                seller_id = item.get("seller_id")
                                if seller_id:
                                    shop_url = f"https://www.ozon.ru/seller/{seller_id}/"
                                    name = item.get("seller_name") or item.get("name", "").split("/")[0].strip()[:255]
                                    upsert_seller_shop(
                                        shop_url,
                                        name=name,
                                        source_sku=sku,
                                        source_table="seed_pool_skus",
                                    )

                            mark_seed_status(
                                sku, query_key,
                                "qualified" if result_obj.matched else "rejected",
                                reason=result_obj.summary,
                            )

                    except Exception as exc:
                        print(f"page {page_no}: SKU3批量获取失败: {exc}")
                        stats["errors"] += 1

                    time.sleep(1)  # 页面间延迟

                except Exception as exc:
                    print(f"page {page_no}: 采集异常: {exc}")
                    stats["errors"] += 1
                    continue

    except Exception as exc:
        print(f"榜单采集全局异常: {exc}")
        stats["errors"] += 1
    finally:
        browser.close_session()

    print(
        f"===== 榜单采集完成: "
        f"种子={stats['seeds_collected']}, "
        f"合格={stats['products_qualified']}, "
        f"页数={stats['pages_fetched']}, "
        f"错误={stats['errors']} ====="
    )
    return stats


if __name__ == "__main__":
    from .config import load_runtime_config
    config = load_runtime_config()
    if not config:
        print("ERROR: config.json 不存在，请先在GUI中暂存配置")
    else:
        run_ranking_collection(config)
