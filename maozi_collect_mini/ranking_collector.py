"""榜单采集脚本。

功能：
- 从毛子ERP榜单API拉取商品列表（每页50条）
- 按配置的类目层级(0/1/2/3)和页数遍历
- 对每页数据执行种子预筛选（榜单种子扩展规则），通过者写入 seed_pool_skus
- 预筛通过的种子 → 将其卖家记录到 seller_shops
- **不写入 sku_products**，不入库商品由卖家主页采集负责
- 榜单阶段**不做 0325 优质品判定**，不做 SKU3 获取
- 遇到个别数据不可获取直接跳过
- 遇到整体token失效尝试通过纠错解决
- 采集完成后返回结果，以便main.py自动切换到卖家模式
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from .browser import BrowserClient
from .repository import (
    bulk_upsert_categories_from_items,
    bulk_upsert_seed_pool,
    query_key_from_filters,
    list_categories_by_level,
    upsert_seller_shop,
    mark_seed_status,
)
from .rules import (
    evaluate_top_list_prefilter,
    get_top_list_seed_rule,
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

    # 上架日期：默认截止昨天，起始为昨天往前推200天（共200天窗口）
    # GUI配置可覆盖，详见gui.py中 create_date_from / create_date_to 参数
    today = datetime.now().date()
    create_to = config.get("create_date_to") or (today - timedelta(days=1)).strftime("%Y-%m-%d")
    create_from = config.get("create_date_from") or (today - timedelta(days=201)).strftime("%Y-%m-%d")
    filters["create_date"] = [create_from.strip(), create_to.strip()]

    return filters


def run_ranking_collection(config: dict[str, Any], stop_flag=None) -> dict[str, int]:
    """执行榜单采集。

    参数:
        config: GUI配置字典，包含 category_level, page_from, page_to, 各类目/过滤参数等
        stop_flag: threading.Event 对象，用于外部停止采集

    返回:
        {"seeds_collected": N, "sellers_recorded": N, "pages_fetched": N, "errors": N}
    """
    category_level = int(config.get("category_level", 1))
    page_from = int(config.get("page_from", 1))
    page_to = int(config.get("page_to", 100))
    main_type = config.get("main_type", "hot")

    print(f"===== 开始榜单采集: type={main_type}, level={category_level}, pages={page_from}-{page_to} =====")

    browser = BrowserClient()
    stats = {"seeds_collected": 0, "sellers_recorded": 0, "pages_fetched": 0, "errors": 0}

    try:
        browser.open_session()

        authenticated = browser.ensure_authenticated(max_retries=3)
        if not authenticated:
            print("ERROR: 毛子ERP登录态验证失败，请检查浏览器登录状态")
            return stats

        base_filters = _build_base_filters(config)

        # 根据类目层级确定要采集的类目列表
        # 元组格式: (c1_id, c2_id, c3_id, label)
        # API要求使用类目ID而非类目名；二级/三级需补齐父级ID链
        category_ids_to_collect: list[tuple[str, str, str, str]] = []
        if category_level == 0:
            # Level 0: 不选类目，采集整个榜单
            category_ids_to_collect.append(("", "", "", "all"))
        elif category_level == 1:
            categories = list_categories_by_level(1)
            for cat in categories:
                cat_id = cat.get("category_id")
                cat_name = cat.get("name_zh", "")
                if not cat_id or not cat_name:
                    continue
                category_ids_to_collect.append((str(cat_id), "", "", cat_name))
        elif category_level == 2:
            categories = list_categories_by_level(2)
            for cat in categories:
                cat_id = cat.get("category_id")
                cat_parent = cat.get("parent_id")
                cat_name = cat.get("name_zh", "")
                if not cat_id or not cat_name or not cat_parent:
                    continue
                # c1=父级ID, c2=当前类目ID
                category_ids_to_collect.append((str(cat_parent), str(cat_id), "", cat_name))
        elif category_level == 3:
            # 预加载父级→祖父级映射
            parent_id_map: dict[int, int] = {}
            level2_list = list_categories_by_level(2)
            for p in level2_list:
                pid = p.get("parent_id")
                if pid is not None:
                    parent_id_map[int(p["category_id"])] = int(pid)

            categories = list_categories_by_level(3)
            for cat in categories:
                cat_id = cat.get("category_id")
                cat_parent = cat.get("parent_id")
                cat_name = cat.get("name_zh", "")
                if not cat_id or not cat_name or not cat_parent:
                    continue
                # c1=祖父级ID, c2=父级ID, c3=当前类目ID
                c1 = ""
                gp_id = parent_id_map.get(int(cat_parent))
                if gp_id:
                    c1 = str(gp_id)
                category_ids_to_collect.append((c1, str(cat_parent), str(cat_id), cat_name))
        else:
            category_ids_to_collect.append(("", "", "", "all"))

        print(f"共 {len(category_ids_to_collect)} 个类目需要采集")

        for cat1, cat2, cat3, cat_label in category_ids_to_collect:
            # 检查停止信号
            if stop_flag and stop_flag.is_set():
                print(f"--- 类目 {cat_label}: 收到停止信号，终止榜单采集 ---")
                break
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
                # 检查停止信号
                if stop_flag and stop_flag.is_set():
                    print(f"page {page_no}/{cat_label}: 收到停止信号，跳过剩余页")
                    break
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

                    # 类目自增：从榜单商品数据中提取类目ID，自动写入 ozon_categories
                    cat_upserted = bulk_upsert_categories_from_items(items)
                    if cat_upserted:
                        print(f"    ozon_categories 自增: {cat_upserted} 条新类目")

                    # 种子预筛选 → 种子池
                    seed_rule = get_top_list_seed_rule()
                    seed_items = []
                    print(f"page {page_no}/{cat_label}: {len(items)}条数据")
                    for item in items:
                        sku = _extract_sku(item)
                        if not sku:
                            continue
                        prefilter = evaluate_top_list_prefilter(item, rule=seed_rule)
                        if prefilter.matched:
                            seed_items.append(item)
                        # 逐SKU打印预筛结果
                        status = "+" if prefilter.matched else f"-{prefilter.summary[:100]}"
                        print(f"    预筛 {status} sku={sku} sold={item.get('sold_count','?')} price={item.get('avg_price','?')} schema={item.get('sales_schema','?')}")

                    if seed_items:
                        # 先获取跟卖列表，检查跟卖人数≤50，再决定是否入库
                        qualified_seeds = []
                        sellers_recorded = 0
                        offers_checked = 0
                        offers_rejected = 0
                        for item in seed_items:
                            sku = _extract_sku(item)
                            offers: list[dict[str, Any]] = []
                            try:
                                offers = browser.fetch_seller_offers(sku) or []
                            except Exception as e:
                                print(f"    跟卖获取异常 sku={sku}: {e}")
                            offers_count = len(offers) if isinstance(offers, list) else 0
                            offers_checked += 1

                            # 种子规则：跟卖人数 ≤ 50
                            if offers_count > 50:
                                print(f"    跟卖超标 sku={sku} offers={offers_count}>50")
                                offers_rejected += 1
                                mark_seed_status(sku, query_key, "rejected", reason=f"跟卖人数{offers_count}>50")
                                continue

                            qualified_seeds.append(item)

                            # 写入种子自身的卖家
                            seller_id = item.get("seller_id")
                            if seller_id:
                                shop_url = f"https://www.ozon.ru/seller/{seller_id}/"
                                name = (item.get("seller_name") or item.get("name", "").split("/")[0].strip())[:255]
                                upsert_seller_shop(shop_url, name=name, source_sku=sku, source_table="seed_pool_skus")
                                sellers_recorded += 1

                            # 写入跟卖列表中的卖家
                            for offer in offers:
                                o_url = offer.get("seller_home_url")
                                o_name = str(offer.get("seller_name") or "unknown")[:255]
                                if o_url:
                                    if not o_url.startswith("http"):
                                        o_url = "https://www.ozon.ru" + o_url
                                    upsert_seller_shop(o_url, name=o_name, source_sku=sku, source_table="seed_pool_skus")
                                    sellers_recorded += 1

                            mark_seed_status(sku, query_key, "qualified", reason="榜单种子扩展")

                        # 只有通过跟卖检查的种子才写入 seed_pool_skus
                        if qualified_seeds:
                            saved = bulk_upsert_seed_pool(qualified_seeds, query_key)
                            stats["seeds_collected"] += saved
                        else:
                            saved = 0

                        stats["sellers_recorded"] += sellers_recorded
                        print(f"    预筛总结: 通过{len(seed_items)}/{len(items)}, 跟卖合格{len(qualified_seeds)}(超标{offers_rejected}), 种子入库{saved or len(qualified_seeds)}")
                        print(f"    卖家写入: {sellers_recorded}个")
                    else:
                        print(f"    预筛总结: 通过0/{len(items)}, 本页无种子")

                    # 预筛淘汰的SKU也标记rejected
                    for item in items:
                        sku = _extract_sku(item)
                        if not sku:
                            continue
                        if sku not in {_extract_sku(s) for s in seed_items}:
                            mark_seed_status(sku, query_key, "rejected", reason="榜单种子扩展未通过")

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
        f"卖家记录={stats['sellers_recorded']}, "
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
