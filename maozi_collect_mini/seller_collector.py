"""卖家主页采集脚本。

功能：
- 按时间顺序(新入库优先)遍历seller_shops表中到期卖家
- 打开卖家主页预取SKU列表
- 品牌检测：前24条SKU全部有品牌 → 永久冻结
- 无合格SKU → 冻结6个月
- 有合格SKU → 写入sku_products，冻结1个月
- 遇到个别数据不可获取直接跳过
- 遇到整体token失效尝试通过纠错解决
"""

from __future__ import annotations

import time
from datetime import datetime
from decimal import Decimal
from typing import Any

from .browser import BrowserClient
from .config import settings
from .maozi_api import MaoziClient
from .repository import (
    list_due_sellers,
    upsert_seller_shop,
    upsert_sku_product,
    mark_seller_collected,
    freeze_seller,
    freeze_seller_permanent,
    parse_sku3_response,
)
from .rules import (
    evaluate_selection_rule,
    seller_looks_branded,
    DEFAULT_SELECTION_RULE,
    price_to_cny,
)

_PRICE_MIN_CNY = Decimal("20")
_PRICE_MAX_CNY = Decimal("1000")
_RUB_RATE = Decimal(str(settings.rub_to_cny_rate))
_BRANDED_SAMPLE_SIZE = 24


def _extract_sku_from_url(url: str) -> str:
    """从Ozon产品URL中提取SKU ID"""
    import re
    m = re.search(r"/product(?:-.*?)?/(\d+)/?", url or "")
    return m.group(1) if m else ""


def _item_passes_price_filter(item: dict[str, Any]) -> bool:
    """卖家主页商品价格预筛（20-1000 CNY）"""
    price = item.get("price_amount")
    currency = (item.get("currency") or "").strip().upper()
    if price is not None:
        try:
            price_d = Decimal(str(price))
            if currency in ("RUB", "RUR", ""):
                price_cny = price_d * _RUB_RATE
            elif currency in ("CNY", "RMB"):
                price_cny = price_d
            else:
                price_cny = None
            if price_cny is not None and (price_cny < _PRICE_MIN_CNY or price_cny > _PRICE_MAX_CNY):
                return False
        except Exception:
            pass
    return True


def run_seller_collection(limit: int = 0, stop_flag=None) -> dict[str, int]:
    """执行卖家主页采集。

    参数:
        limit: 本轮最多处理卖家数，0=无限制
        stop_flag: threading.Event 对象，用于外部停止采集

    返回:
        {"sellers_processed": N, "products_qualified": N, "branded_skip": N, "empty_skip": N, "errors": N}
    """
    stats = {
        "sellers_processed": 0,
        "products_qualified": 0,
        "branded_skip": 0,
        "empty_skip": 0,
        "errors": 0,
    }

    browser = BrowserClient()
    maozi = MaoziClient()

    try:
        browser.open_session()

        authenticated = browser.ensure_authenticated(max_retries=3)
        if not authenticated:
            print("ERROR: 毛子ERP登录态验证失败")
            return stats

        while True:
            # 检查停止信号
            if stop_flag and stop_flag.is_set():
                print("收到停止信号，终止卖家采集")
                break
            # 每轮查询到期卖家
            batch_size = min(limit if limit > 0 else 10, 10)
            sellers = list_due_sellers(limit=batch_size)
            if not sellers:
                print("没有到期的卖家，采集结束")
                break

            for seller in sellers:
                # 检查停止信号
                if stop_flag and stop_flag.is_set():
                    print(f"卖家 {seller.get('name','?')}: 收到停止信号，终止卖家采集")
                    break
                seller_key = seller["seller_key"]
                home_url = seller["home_url"] or ""
                if not home_url.startswith("http"):
                    home_url = "https://www.ozon.ru" + home_url
                name = seller.get("name") or "unknown"
                source_sku = seller.get("source_sku")
                source_table = seller.get("source_table")

                print(f"\n--- 卖家 [{stats['sellers_processed']+1}]: {name} ({home_url[:80]}...) ---")

                try:
                    # 通过Ozon entrypoint API拉取卖家主页全部商品（翻到最后一页）
                    result = browser.fetch_seller_home_products(
                        home_url, page_timeout=settings.seller_page_timeout_seconds
                    )

                    if result.get("error"):
                        print(f"  卖家主页加载失败: {result['error']}")
                        stats["errors"] += 1
                        stats["sellers_processed"] += 1
                        continue

                    items = result.get("items") or []
                    if not items:
                        print(f"  卖家主页无商品")
                        freeze_seller(seller_key, 6)
                        mark_seller_collected(seller_key, 0)
                        stats["empty_skip"] += 1
                        stats["sellers_processed"] += 1
                        continue

                    print(f"  卖家主页获取 {len(items)} 条商品 (翻页{result.get('pages_fetched', '?')}次)")

                    # 品牌检测
                    branded_items = [{"title": it.get("title")} for it in items]
                    if seller_looks_branded(branded_items, sample_size=_BRANDED_SAMPLE_SIZE):
                        print(f"  品牌卖家，永久冻结")
                        freeze_seller_permanent(seller_key)
                        mark_seller_collected(seller_key, 0)
                        stats["branded_skip"] += 1
                        stats["sellers_processed"] += 1
                        continue

                    # 价格预筛
                    items_before_filter = len(items)
                    items = [it for it in items if _item_passes_price_filter(it)]
                    if items_before_filter > len(items):
                        print(f"  价格预筛: {len(items)}/{items_before_filter} 通过 (20-1000 CNY)")

                    # 提取SKU列表（优先使用API返回的sku字段，兜底从URL解析）
                    skus = []
                    for it in items:
                        sku = str(it.get("sku") or _extract_sku_from_url(it.get("href") or "") or "")
                        if sku:
                            skus.append(sku)
                            it["sku"] = sku

                    if not skus:
                        print(f"  无有效SKU")
                        freeze_seller(seller_key, 6)
                        mark_seller_collected(seller_key, 0)
                        stats["empty_skip"] += 1
                        stats["sellers_processed"] += 1
                        continue

                    # 打印SKU列表
                    print(f"  提取SKU列表 ({len(skus)}): {', '.join(skus[:30])}{'...' if len(skus) > 30 else ''}")

                    # 分批获取SKU3详情（全部 SKU，每批60个）
                    batch_size = settings.top_list_sku3_batch_size
                    batch_concurrency = settings.top_list_sku3_batch_concurrency
                    batch_delay = settings.top_list_sku3_batch_chunk_delay_ms
                    total_batches = (len(skus) + batch_size - 1) // batch_size
                    all_sku3_results: dict[str, Any] = {}
                    sku3_batch_errors = 0
                    try:
                        print(f"  批量获取SKU3: {len(skus)}个SKU (分{total_batches}批, 每批{batch_size}个, {batch_concurrency}并发)")
                        for batch_idx in range(0, len(skus), batch_size):
                            batch = skus[batch_idx:batch_idx + batch_size]
                            batch_no = batch_idx // batch_size + 1
                            try:
                                batch_results = browser.fetch_sku3_batch(
                                    batch,
                                    concurrency=batch_concurrency,
                                    chunk_delay_ms=batch_delay,
                                )
                                all_sku3_results.update(batch_results)
                                success_in_batch = sum(1 for v in batch_results.values() if isinstance(v, dict) and "_error" not in v)
                                fail_in_batch = len(batch) - success_in_batch
                                print(f"  SKU3批次[{batch_no}/{total_batches}]: 成功{success_in_batch} 失败{fail_in_batch}")
                            except Exception as batch_exc:
                                print(f"  SKU3批次[{batch_no}/{total_batches}]异常: {batch_exc}")
                                sku3_batch_errors += 1
                                # 单批失败不中断，继续下一批
                            # 批次间延迟（避免触发限流）
                            if batch_idx + batch_size < len(skus) and batch_delay > 0:
                                time.sleep(batch_delay / 1000.0)
                    except Exception as exc:
                        print(f"  SKU3批量获取失败: {exc}")
                        stats["errors"] += 1
                        stats["sellers_processed"] += 1
                        continue

                    if not all_sku3_results:
                        print(f"  SKU3全部失败，跳过该卖家")
                        freeze_seller(seller_key, 6)
                        mark_seller_collected(seller_key, 0)
                        stats["errors"] += 1
                        stats["sellers_processed"] += 1
                        continue

                    seller_qualified = 0
                    sku3_success = 0
                    sku3_failed = 0
                    for it in items:
                        sku = it.get("sku")
                        if not sku:
                            continue

                        sku3_response = all_sku3_results.get(sku)
                        if not sku3_response or "_error" in sku3_response:
                            sku3_failed += 1
                            err_msg = ""
                            if isinstance(sku3_response, dict):
                                err_msg = sku3_response.get("_error", "")[:60]
                            print(f"  SKU3失败 sku={sku} reason={err_msg}")
                            continue
                        sku3_success += 1

                        try:
                            metric = parse_sku3_response(sku, sku3_response)
                        except Exception:
                            sku3_failed += 1
                            print(f"  SKU3解析失败 sku={sku}")
                            continue

                        product_snapshot = {
                            "product_url": it.get("href"),
                            "title": it.get("title"),
                            "brand": metric.get("brand"),
                            "price": it.get("price_amount"),
                            "currency": it.get("currency") or "RUB",
                            "main_image_url": it.get("image_url"),
                            "raw": {"seller_home": it},
                        }

                        # 步骤1：SKU3先筛（跳过跟卖人数，避免无效API调用）
                        result_step1 = evaluate_selection_rule(
                            metric, product_snapshot, 0, skip_offer_count=True
                        )
                        if not result_step1.matched:
                            stats["sku3_filtered"] = stats.get("sku3_filtered", 0) + 1
                            print(f"  淘汰 sku={sku} reason={result_step1.summary[:80]}")
                            continue

                        # 步骤2：SKU3通过 → 获取跟卖人数 → 完整判定
                        offers = browser.fetch_seller_offers(sku)
                        seller_offer_count = len(offers) if isinstance(offers, list) else None
                        result_obj = evaluate_selection_rule(
                            metric, product_snapshot, seller_offer_count
                        )

                        if result_obj.matched:
                            upsert_sku_product(
                                metric,
                                product_snapshot,
                                seller_offer_count=seller_offer_count,
                                source_sku=source_sku,
                                source_table=source_table or "seller_shops",
                            )
                            seller_qualified += 1
                            stats["products_qualified"] += 1
                            # 合格SKU的跟卖列表写入seller_shops
                            if isinstance(offers, list) and offers:
                                for offer in offers:
                                    o_url = offer.get("seller_home_url")
                                    o_name = str(offer.get("seller_name") or "unknown")[:255]
                                    if o_url:
                                        if not o_url.startswith("http"):
                                            o_url = "https://www.ozon.ru" + o_url
                                        upsert_seller_shop(o_url, name=o_name, source_sku=sku, source_table="sku_products")
                            print(f"  合格 sku={sku} brand={metric.get('brand','?')} order30={metric.get('order_amount_30d','?')} rev30={metric.get('revenue_30d','?')}")
                        else:
                            print(f"  淘汰 sku={sku} reason={result_obj.summary[:80]}")

                    # SKU3总结
                    print(f"  SKU3总结: 成功{sku3_success} 失败{sku3_failed}")

                    # 根据合格数决定冻结时长
                    if seller_qualified > 0:
                        freeze_seller(seller_key, 1)  # 有合格SKU，冻结1个月
                        print(f"  卖家总结: 合格{ seller_qualified}/{len(skus)}SKU，冻结1个月")
                    else:
                        freeze_seller(seller_key, 6)  # 无合格SKU，冻结6个月
                        print(f"  卖家总结: 合格0/{len(skus)}SKU，冻结6个月")

                    mark_seller_collected(seller_key, seller_qualified)
                    stats["sellers_processed"] += 1

                    # 周期性清理多余页面（>10页时关闭非核心页面，防止内存溢出）
                    browser._cleanup_excess_pages()

                    # 每处理一个卖家检查认证
                    browser.ensure_authenticated(max_retries=1)

                except Exception as exc:
                    print(f"  卖家处理异常: {exc}")
                    stats["errors"] += 1
                    stats["sellers_processed"] += 1
                    continue

            # 如果有limit限制且已经够数了
            if limit > 0 and stats["sellers_processed"] >= limit:
                break

    except Exception as exc:
        print(f"卖家采集全局异常: {exc}")
        stats["errors"] += 1
    finally:
        browser.close_session()

    print(
        f"===== 卖家采集完成: "
        f"处理={stats['sellers_processed']}, "
        f"合格={stats['products_qualified']}, "
        f"品牌跳过={stats['branded_skip']}, "
        f"无货跳过={stats['empty_skip']}, "
        f"错误={stats['errors']} ====="
    )
    return stats


if __name__ == "__main__":
    run_seller_collection()
