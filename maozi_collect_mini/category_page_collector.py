"""Ozon 类目页商品采集器。

功能：
- 从 ozon_category_urls 表读取叶子类目，末级优先（4级 → 3级）
- 分层过滤：价格URL筛 → 品牌应用层筛 → SKU3详情 → 跟卖判定
- 合格SKU写入 sku_products，跟卖列表写入 seller_shops
- 断点续采：支持中断后从上次位置继续
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from .browser import BrowserClient
from .repository import (
    list_leaf_categories,
    upsert_sku_product,
    upsert_seller_shop,
    parse_sku3_response,
    get_category_page_checkpoint,
    upsert_category_page_checkpoint,
    count_sku_products,
)
from .rules import (
    evaluate_selection_rule,
    ALLOWED_BRAND_NAMES,
    DEFAULT_SELECTION_RULE,
    price_to_cny,
    normalize_text,
)
from .config import settings

# 默认价格分段（RUB），从左到右依次递增
_DEFAULT_PRICE_RANGES = [
    "20.000;250.000",
    "250.000;500.000",
    "500.000;1000.000",
]

# 品牌过滤空白页阈值：连续N页全品牌商品则跳过该类目
_MAX_CONSECUTIVE_BRANDED_PAGES = 5

# 空页连续阈值：连续N页空则结束该类目采集
_MAX_CONSECUTIVE_EMPTY_PAGES = 3

# 翻页安全上限
_MAX_PAGES_PER_CATEGORY = 500


def _build_category_url(slug: str, category_id: int) -> str:
    """构建 Ozon 类目页完整 URL"""
    return f"https://www.ozon.ru/category/{slug}-{category_id}/"


def _price_range_label(pr: str) -> str:
    """价格分段标签"""
    parts = pr.replace(".000", "").split(";")
    return f"{parts[0]}-{parts[1]}₽"


def run_category_page_collection(
    config: dict[str, Any],
    stop_flag=None,
    resume: bool = False,
) -> dict[str, int]:
    """执行类目页商品采集。

    参数：
        config: GUI配置字典，包含：
            - leaf_levels: 要采集的叶子层级，如 "4" 或 "4,3"（默认 "4"）
            - price_ranges: 自定义价格分段（逗号分隔），为空则用默认三段
            - sorting: 排序方式 "score"/"new"/"price"（默认 "score"）
            - max_pages: 每个类目最大翻页数（默认 500）
            - resume_from_checkpoint: 是否启用断点续采
        stop_flag: threading.Event 对象，用于外部停止采集
        resume: 是否启用断点续采

    返回：
        {"categories_processed": N, "pages_fetched": N, "products_qualified": N,
         "sellers_recorded": N, "categories_skipped": N, "errors": N}
    """
    # 解析配置
    leaf_levels_str = config.get("leaf_levels", "4").strip()
    leaf_levels: tuple[int, ...]
    try:
        leaf_levels = tuple(int(x.strip()) for x in leaf_levels_str.split(",") if x.strip())
    except ValueError:
        leaf_levels = (4,)
    if not leaf_levels:
        leaf_levels = (4,)

    price_ranges_str = config.get("price_ranges", "").strip()
    if price_ranges_str:
        price_ranges = [x.strip() for x in price_ranges_str.split(",") if x.strip()]
    else:
        price_ranges = list(_DEFAULT_PRICE_RANGES)

    sorting = config.get("sorting", "score").strip() or "score"
    max_pages = int(config.get("max_pages", _MAX_PAGES_PER_CATEGORY))

    stats = {
        "categories_processed": 0,
        "pages_fetched": 0,
        "products_qualified": 0,
        "sellers_recorded": 0,
        "categories_skipped": 0,
        "errors": 0,
    }

    print(f"===== 开始类目页采集: leaf_levels={leaf_levels}, price_ranges={price_ranges}, sorting={sorting}, resume={resume} =====")

    # 获取叶子类目列表
    categories = list_leaf_categories(leaf_levels)
    if not categories:
        print("ERROR: 未找到任何叶子类目，请检查 ozon_category_urls 表数据")
        return stats
    print(f"共 {len(categories)} 个叶子类目待采集")

    browser = BrowserClient()
    try:
        browser.open_session()

        authenticated = browser.ensure_authenticated(max_retries=3)
        if not authenticated:
            print("ERROR: 毛子ERP登录态验证失败，无法获取毛子Token用于SKU3调用")
            return stats

        for cat_idx, cat in enumerate(categories):
            category_id = int(cat["category_id"])
            slug = cat.get("slug", "")
            name_ru = cat.get("name_ru", "")
            level = int(cat.get("level", 0))
            cat_url = _build_category_url(slug, category_id)

            if stop_flag and stop_flag.is_set():
                print(f"--- 类目 [{cat_idx+1}/{len(categories)}]: 收到停止信号，终止采集 ---")
                break

            print(f"\n--- 类目 [{cat_idx+1}/{len(categories)}] L{level}: {name_ru} (ID={category_id}, slug={slug}) ---")
            print(f"    URL: {cat_url}")

            if not slug or not category_id:
                print(f"    SKIP: slug 或 category_id 缺失")
                stats["errors"] += 1
                continue

            category_qualified = 0
            category_pages = 0

            # 对每个价格分段遍历
            for pr_idx, price_range in enumerate(price_ranges):
                if stop_flag and stop_flag.is_set():
                    print(f"  价格分段 {_price_range_label(price_range)}: 收到停止信号，跳过")
                    break

                # 断点续采：检查该类目+价格分段是否已完成
                current_page = 1
                if resume:
                    checkpoint = get_category_page_checkpoint(category_id, price_range)
                    if checkpoint and checkpoint.get("status") == "completed":
                        print(f"  价格分段 {_price_range_label(price_range)}: 已完成，跳过")
                        stats["categories_skipped"] += 1
                        continue
                    if checkpoint and checkpoint.get("status") == "in_progress":
                        last_page = int(checkpoint.get("last_page_completed", 0))
                        if last_page >= max_pages:
                            print(f"  价格分段 {_price_range_label(price_range)}: 已完成(last={last_page})，补标记")
                            upsert_category_page_checkpoint(
                                category_id, price_range, f"{name_ru} L{level}",
                                last_page, max_pages, 0, "completed"
                            )
                            stats["categories_skipped"] += 1
                            continue
                        current_page = last_page + 1
                        print(f"  价格分段 {_price_range_label(price_range)}: 从第 {current_page} 页续采")

                # 标记开始
                if resume:
                    upsert_category_page_checkpoint(
                        category_id, price_range, f"{name_ru} L{level}",
                        current_page - 1, max_pages, category_qualified, "in_progress"
                    )

                print(f"  价格分段 [{pr_idx+1}/{len(price_ranges)}] {_price_range_label(price_range)}: 开始")
                pr_qualified = 0
                pr_pages = 0
                empty_pages = 0
                branded_pages = 0

                while current_page <= max_pages:
                    if stop_flag and stop_flag.is_set():
                        print(f"  page {current_page}: 收到停止信号，保存断点")
                        if resume:
                            upsert_category_page_checkpoint(
                                category_id, price_range, f"{name_ru} L{level}",
                                current_page - 1, max_pages, category_qualified + pr_qualified, "in_progress"
                            )
                        break

                    # 认证检查：每20页检查一次
                    if current_page % 20 == 0:
                        browser.ensure_authenticated(max_retries=1)

                    try:
                        result = browser.fetch_category_page(
                            slug=slug,
                            category_id=category_id,
                            page=current_page,
                            price_range=price_range,
                            sorting=sorting,
                            page_timeout=settings.seller_page_timeout_seconds,
                        )
                    except Exception as exc:
                        print(f"  page {current_page}: 请求异常: {exc}")
                        stats["errors"] += 1
                        empty_pages += 1
                        current_page += 1
                        if empty_pages >= _MAX_CONSECUTIVE_EMPTY_PAGES * 2:
                            print(f"  page {current_page}: 连续{empty_pages}页异常，结束此价格分段")
                            break
                        continue

                    stats["pages_fetched"] += 1
                    pr_pages += 1

                    if result.get("error"):
                        err = result["error"]
                        print(f"  page {current_page}: API错误: {err[:120]}")
                        # 404/403/401 等不可恢复错误直接跳过
                        if "404" in err or "403" in err:
                            print(f"  page {current_page}: 不可恢复错误，结束此价格分段")
                            break
                        stats["errors"] += 1
                        empty_pages += 1
                        current_page += 1
                        if empty_pages >= _MAX_CONSECUTIVE_EMPTY_PAGES:
                            break
                        continue

                    items = result.get("items") or []
                    if not items:
                        empty_pages += 1
                        print(f"  page {current_page}: 无商品 (连续空页={empty_pages})")
                        current_page += 1
                        if empty_pages >= _MAX_CONSECUTIVE_EMPTY_PAGES:
                            print(f"  page {current_page}: 连续{empty_pages}页无商品，结束此价格分段")
                            break
                        # 检查 has_next 翻页标志
                        if not result.get("has_next"):
                            print(f"  page {current_page}: 无下一页，结束此价格分段")
                            break
                        continue

                    empty_pages = 0  # 有数据，重置空页计数

                    # =============================================
                    # 分层过滤1: 价格（URL参数已过滤）
                    # =============================================

                    # =============================================
                    # 分层过滤2: 品牌过滤（应用层）
                    # =============================================
                    unbranded = [it for it in items if not it.get("is_branded")]
                    branded_count = len(items) - len(unbranded)
                    if branded_count > 0 and not unbranded:
                        branded_pages += 1
                        print(f"  page {current_page}: {len(items)}条全有品牌 (连续{品牌}页={branded_pages})")
                        current_page += 1
                        if branded_pages >= _MAX_CONSECUTIVE_BRANDED_PAGES:
                            print(f"  page {current_page}: 连续{branded_pages}页全品牌，结束此价格分段")
                            break
                        # 检查翻页
                        if not result.get("has_next"):
                            break
                        continue
                    branded_pages = 0  # 有未品牌商品，重置

                    print(f"  page {current_page}: {len(items)}条, 有品牌={branded_count}, 无品牌={len(unbranded)}")

                    if not unbranded:
                        current_page += 1
                        if not result.get("has_next"):
                            break
                        continue

                    # =============================================
                    # 分层过滤3: SKU3 详情（毛子API）
                    # =============================================
                    unbranded_skus = [it["sku"] for it in unbranded if it.get("sku")]
                    if not unbranded_skus:
                        current_page += 1
                        if not result.get("has_next"):
                            break
                        continue

                    print(f"    SKU3请求: {len(unbranded_skus)}个SKU")
                    sku3_results = {}
                    try:
                        sku3_results = browser.fetch_sku3_batch(
                            unbranded_skus,
                            concurrency=settings.top_list_sku3_batch_concurrency,
                            chunk_delay_ms=settings.top_list_sku3_batch_chunk_delay_ms,
                        )
                    except Exception as exc:
                        print(f"    SKU3批量调用异常: {exc}")
                        stats["errors"] += 1
                        current_page += 1
                        if not result.get("has_next"):
                            break
                        continue

                    sku3_pass = []
                    sku3_fail = 0
                    for it in unbranded:
                        sku = it.get("sku")
                        if not sku:
                            continue
                        sku3_resp = sku3_results.get(sku)
                        if not sku3_resp or "_error" in sku3_resp:
                            sku3_fail += 1
                            err_msg = ""
                            if isinstance(sku3_resp, dict):
                                err_msg = sku3_resp.get("_error", "")[:60]
                            print(f"      SKU3失败 sku={sku} reason={err_msg}")
                            continue

                        try:
                            metric = parse_sku3_response(sku, sku3_resp)
                        except Exception:
                            sku3_fail += 1
                            print(f"      SKU3解析失败 sku={sku}")
                            continue

                        # 品牌二次确认：毛子SKU3 API的品牌字段更权威
                        brand = normalize_text(metric.get("brand"))
                        if brand and brand not in ALLOWED_BRAND_NAMES:
                            print(f"      SKU3品牌二次确认淘汰 sku={sku} brand={brand}")
                            sku3_fail += 1
                            continue

                        # SKU3先筛（跳过跟卖人数检查）
                        product_snapshot = {
                            "product_url": it.get("href"),
                            "title": it.get("title"),
                            "brand": metric.get("brand"),
                            "price": it.get("price_amount"),
                            "currency": it.get("currency") or "RUB",
                            "main_image_url": it.get("image_url"),
                            "raw": {"category_page": it},
                        }

                        result_step1 = evaluate_selection_rule(
                            metric, product_snapshot, 0, skip_offer_count=True
                        )
                        if not result_step1.matched:
                            print(f"      SKU3先筛淘汰 sku={sku} reason={result_step1.summary[:100]}")
                            sku3_fail += 1
                            continue

                        sku3_pass.append((it, metric, product_snapshot))

                    print(f"    SKU3先筛结果: 通过{len(sku3_pass)}/{len(unbranded)} (失败{sku3_fail})")

                    if not sku3_pass:
                        current_page += 1
                        if not result.get("has_next"):
                            break
                        continue

                    # =============================================
                    # 分层过滤4: 跟卖列表 + 完整判定
                    # =============================================
                    for it, metric, product_snapshot in sku3_pass:
                        sku = it["sku"]

                        # 获取跟卖列表
                        offers = browser.fetch_seller_offers(sku)
                        seller_offer_count = len(offers) if isinstance(offers, list) else None

                        # 完整判定
                        result_obj = evaluate_selection_rule(
                            metric, product_snapshot, seller_offer_count
                        )

                        if result_obj.matched:
                            # 写入 sku_products
                            upsert_sku_product(
                                metric,
                                product_snapshot,
                                seller_offer_count=seller_offer_count,
                                source_sku=sku,
                                source_table="category_page",
                            )
                            pr_qualified += 1
                            stats["products_qualified"] += 1

                            # 跟卖列表写入 seller_shops
                            if isinstance(offers, list):
                                for offer in offers:
                                    o_url = offer.get("seller_home_url")
                                    o_name = str(offer.get("seller_name") or "unknown")[:255]
                                    if o_url:
                                        if not o_url.startswith("http"):
                                            o_url = "https://www.ozon.ru" + o_url
                                        upsert_seller_shop(
                                            o_url, name=o_name,
                                            source_sku=sku, source_table="category_page"
                                        )
                                        stats["sellers_recorded"] += 1

                            print(f"      合格 sku={sku} brand={metric.get('brand','?')} sold={metric.get('sold_count','?')} price={product_snapshot.get('price','?')}")
                        else:
                            print(f"      淘汰 sku={sku} reason={result_obj.summary[:100]}")

                    # 翻页
                    current_page += 1

                    # 保存断点
                    if resume:
                        upsert_category_page_checkpoint(
                            category_id, price_range, f"{name_ru} L{level}",
                            current_page - 1, max_pages, category_qualified + pr_qualified, "in_progress"
                        )

                    if not result.get("has_next"):
                        print(f"  page {current_page}: 无下一页，结束此价格分段")
                        break

                    # 页面间延迟
                    time.sleep(0.5)

                # 价格分段结束
                print(f"  价格分段 {_price_range_label(price_range)} 完成: {pr_pages}页, 合格{pr_qualified}SKU")

                if resume:
                    upsert_category_page_checkpoint(
                        category_id, price_range, f"{name_ru} L{level}",
                        max_pages, max_pages, category_qualified + pr_qualified, "completed"
                    )
                category_qualified += pr_qualified
                category_pages += pr_pages

            # 类目结束
            stats["categories_processed"] += 1
            print(f"  类目 {name_ru} 完成: {category_pages}页, 合格{category_qualified}SKU")

            # 周期性清理页面
            browser._cleanup_excess_pages()

    except Exception as exc:
        print(f"类目页采集全局异常: {exc}")
        stats["errors"] += 1
        import traceback
        traceback.print_exc()
    finally:
        browser.close_session()

    total_sku = count_sku_products()
    print(
        f"===== 类目页采集完成: "
        f"类目={stats['categories_processed']}, "
        f"页数={stats['pages_fetched']}, "
        f"合格SKU={stats['products_qualified']}, "
        f"卖家记录={stats['sellers_recorded']}, "
        f"跳过={stats['categories_skipped']}, "
        f"错误={stats['errors']}, "
        f"SKU表总计={total_sku} ====="
    )
    return stats


if __name__ == "__main__":
    from .config import load_runtime_config
    config = load_runtime_config()
    if not config:
        print("ERROR: config.json 不存在，请先在GUI中暂存配置")
    else:
        run_category_page_collection(config)
