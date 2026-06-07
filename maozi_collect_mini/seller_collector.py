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


def run_seller_collection(limit: int = 0) -> dict[str, int]:
    """执行卖家主页采集。

    参数:
        limit: 本轮最多处理卖家数，0=无限制

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
            # 每轮查询到期卖家
            batch_size = min(limit if limit > 0 else 10, 10)
            sellers = list_due_sellers(limit=batch_size)
            if not sellers:
                print("没有到期的卖家，采集结束")
                break

            for seller in sellers:
                seller_key = seller["seller_key"]
                home_url = seller["home_url"]
                name = seller.get("name") or "unknown"
                source_sku = seller.get("source_sku")
                source_table = seller.get("source_table")

                print(f"\n--- 采集卖家: {name} ({home_url[:80]}...) ---")

                try:
                    # 打开卖家主页，预取SKU
                    result = browser.fetch_seller_home_products(
                        home_url, max_scrolls=3, page_timeout=settings.seller_page_timeout_seconds
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
                    items = [it for it in items if _item_passes_price_filter(it)]

                    # 提取SKU列表并获取SKU3详情
                    skus = []
                    for it in items:
                        sku = _extract_sku_from_url(it.get("href") or "")
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

                    # 批量获取SKU3详情
                    try:
                        sku3_results = browser.fetch_sku3_batch(
                            skus[:60],
                            concurrency=5,
                            chunk_delay_ms=500,
                        )
                    except Exception as exc:
                        print(f"  SKU3批量获取失败: {exc}")
                        stats["errors"] += 1
                        stats["sellers_processed"] += 1
                        continue

                    seller_qualified = 0
                    for it in items:
                        sku = it.get("sku")
                        if not sku:
                            continue

                        sku3_response = sku3_results.get(sku)
                        if not sku3_response or "_error" in sku3_response:
                            continue

                        try:
                            metric = parse_sku3_response(sku, sku3_response)
                        except Exception:
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

                        seller_offer_count = None
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

                    # 根据合格数决定冻结时长
                    if seller_qualified > 0:
                        freeze_seller(seller_key, 1)  # 有合格SKU，冻结1个月
                        print(f"  达标{ seller_qualified}个SKU，冻结1个月")
                    else:
                        freeze_seller(seller_key, 6)  # 无合格SKU，冻结6个月
                        print(f"  无达标SKU，冻结6个月")

                    mark_seller_collected(seller_key, seller_qualified)
                    stats["sellers_processed"] += 1

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
