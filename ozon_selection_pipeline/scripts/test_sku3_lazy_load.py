"""
SKU3 预热 vs 冷请求对比测试 v2 —— 整店级测试

核心假说：卖家主页上半部分 SKU（懒加载浅层）已被插件自动预热，下半部分（深层懒加载）
没有。如果不滚动就直接调 SKU3，深层 SKU 的失败率应高于浅层。

测试流程:
  1. 通过 API 获取全部 SKU 列表（基准）
  2. 打开卖家页，逐次滚动，每次记录 DOM 中新出现的 SKU（按懒加载顺序编号）
  3. 将 SKU 按出现顺序三等分: top(浅层), mid(中层), bottom(深层)
  4. 不滚动直接调 SKU3，对比三组的成功率

用法:
  uv run python scripts/test_sku3_lazy_load.py --seller-url "https://..." --cdp http://127.0.0.1:9222
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
os.chdir(ROOT_DIR)

from ozon_pipeline.browser_ozon import BrowserOzonClient
from ozon_pipeline.config import settings


def extract_sku_from_href(href: str) -> str | None:
    """从 /product/sku-name/ 中提取纯数字 SKU"""
    if not href:
        return None
    path = href.split("?", 1)[0].rstrip("/")
    segment = path.split("/")[-1]
    parts = segment.split("-")
    candidates = [p for p in parts if p.isdigit() and len(p) >= 6]
    return candidates[-1] if candidates else None


def get_all_skus_via_api(browser, seller_url: str) -> list[str]:
    """通过 entrypoint API 获取全部 SKU 列表（基准，按Ozon排序）"""
    result = browser.seller_home_products(seller_url, max_scrolls=0)
    items = result.get("items") or []
    skus = []
    for item in items:
        sku = str(item.get("sku") or "")
        if not sku:
            href = item.get("href") or item.get("product_url") or ""
            sku = extract_sku_from_href(href) or ""
        if sku:
            skus.append(sku)
    return skus


def scroll_and_track_dom(browser, seller_url: str, api_skus: list[str], max_scrolls: int = 30) -> dict[str, int]:
    """
    打开卖家页，逐次滚动，记录每个SKU首次出现在DOM时的滚动次数。
    返回 {sku: scroll_number} (0 = 第0次滚动前就出现了)
    """
    api_sku_set = set(api_skus)
    sku_scroll_order: dict[str, int] = {}  # sku -> 首次出现的滚动次数

    page = browser._context.new_page()
    try:
        print(f" 导航到卖家页...")
        page.goto(seller_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)  # 等待首屏渲染

        def _collect_visible_skus():
            """从当前DOM提取所有可见的产品SKU链接"""
            skus = page.evaluate("""
                () => {
                    const links = Array.from(document.querySelectorAll('a[href*="/product/"]'));
                    const seen = new Set();
                    const result = [];
                    for (const a of links) {
                        const href = a.href || '';
                        const match = href.match(/\\/product\\/[^/]+\\/(\\d+)\\/?/);
                        if (match) {
                            const sku = match[1];
                            if (!seen.has(sku) && sku.length >= 6) {
                                seen.add(sku);
                                result.push(sku);
                            }
                        }
                    }
                    return result;
                }
            """)
            return [str(s) for s in skus] if isinstance(skus, list) else []

        # 第0次: 记录首屏已加载的SKU
        visible = _collect_visible_skus()
        for s in visible:
            if s in api_sku_set and s not in sku_scroll_order:
                sku_scroll_order[s] = 0
        print(f"  首屏可见: {len([s for s in sku_scroll_order.values() if s == 0])} 个SKU")

        # 逐次滚动
        stable_count = 0
        for scroll_i in range(1, max_scrolls + 1):
            prev_loaded = len(sku_scroll_order)
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(800)

            visible = _collect_visible_skus()
            new_count = 0
            for s in visible:
                if s in api_sku_set and s not in sku_scroll_order:
                    sku_scroll_order[s] = scroll_i
                    new_count += 1

            total_loaded = len(sku_scroll_order)
            print(f"  滚动 {scroll_i}/{max_scrolls}: 新增 {new_count} | 累计 {total_loaded}/{len(api_skus)}")

            if new_count == 0 and total_loaded == prev_loaded:
                stable_count += 1
                if stable_count >= 3:  # 连续3次无新内容 → 认为加载完成
                    print(f"  连续{stable_count}次无新SKU，停止滚动")
                    break
            else:
                stable_count = 0

            if total_loaded >= len(api_skus) * 0.95:  # 95%以上的SKU已加载
                print(f"  已加载 {total_loaded}/{len(api_skus)} (>95%)，停止滚动")
                break

    finally:
        try:
            page.close()
        except Exception:
            pass

    return sku_scroll_order


def test_sku3_batch(browser, skus: list[str], concurrency: int):
    """调用 SKU3 批量接口，返回 {sku: ok|error}"""
    results: dict[str, dict] = {}
    raw = browser.top_list_sku3_batch(skus, concurrency=concurrency)
    for sku_str in skus:
        data = raw.get(sku_str)
        if isinstance(data, dict):
            status = data.get("status") or {}
            results[sku_str] = {
                "ok": True,
                "update_sales": bool(status.get("update_sales")),
                "update_variant": bool(status.get("update_variant")),
                "data_keys": len(data),
            }
        else:
            results[sku_str] = {"ok": False}
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seller-url", required=True)
    parser.add_argument("--cdp", default=settings.chrome_cdp_url or "http://127.0.0.1:9222")
    parser.add_argument("--max-scrolls", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--skip-scroll", action="store_true", help="跳过滚动阶段，直接对比TOP/BOTTOM(基于API顺序)")
    args = parser.parse_args()

    browser = BrowserOzonClient(cdp_url=args.cdp)
    with browser.session():
        # Step 1: 通过 API 获取全部 SKU
        print("\n" + "=" * 60)
        print("[Step 1] 通过 API 获取全部 SKU...")
        print("=" * 60)
        all_skus = get_all_skus_via_api(browser, args.seller_url)
        print(f"  API 返回: {len(all_skus)} 个SKU")
        print(f"  前5个: {all_skus[:5]}")
        print(f"  后5个: {all_skus[-5:]}")

        if not args.skip_scroll:
            # Step 2: 滚动并跟踪懒加载
            print(f"\n{'=' * 60}")
            print("[Step 2] 滚动卖家页, 跟踪懒加载顺序...")
            print("=" * 60)
            sku_order = scroll_and_track_dom(browser, args.seller_url, all_skus, args.max_scrolls)

            # 只保留出现在DOM中的SKU
            loaded_skus = sorted(sku_order.keys(), key=lambda s: sku_order[s])
            print(f"\n  最终DOM可见: {len(loaded_skus)}/{len(all_skus)} 个SKU")

            if len(loaded_skus) < 50:
                print("  DOM中SKU太少，换用API顺序分组")
                loaded_skus = all_skus[:len(all_skus)]
        else:
            sku_order = {s: i for i, s in enumerate(all_skus)}
            loaded_skus = list(all_skus)

        # Step 3: 三等分
        n = len(loaded_skus)
        third = n // 3
        top_half = loaded_skus[:third]
        mid_half = loaded_skus[third : 2 * third]
        bottom_half = loaded_skus[2 * third:]
        print(f"\n  三等分: TOP={len(top_half)} MID={len(mid_half)} BOTTOM={len(bottom_half)}")
        if sku_order:
            print(f"  TOP 范围: 滚动#{min(sku_order[s] for s in top_half)} ~ #{max(sku_order[s] for s in top_half)}")
            print(f"  BOTTOM 范围: 滚动#{min(sku_order[s] for s in bottom_half if s in sku_order)} ~ #{max(sku_order[s] for s in bottom_half if s in sku_order)}")

        # Step 4: 测试全部三组
        print(f"\n{'=' * 60}")
        print(f"[Step 3] SKU3 测试 (不额外滚动, concurrency={args.concurrency})")
        print("=" * 60)

        print(f"\n测试 TOP (浅层, {len(top_half)} SKUs)...")
        top_results = test_sku3_batch(browser, top_half, args.concurrency)

        print(f"测试 MID (中层, {len(mid_half)} SKUs)...")
        mid_results = test_sku3_batch(browser, mid_half, args.concurrency)

        print(f"测试 BOTTOM (深层, {len(bottom_half)} SKUs)...")
        bottom_results = test_sku3_batch(browser, bottom_half, args.concurrency)

        # Step 5: 对比
        print("\n" + "=" * 60)
        print("对比结果")
        print("=" * 60)

        for label, results, skus in [
            ("TOP (浅层)", top_results, top_half),
            ("MID (中层)", mid_results, mid_half),
            ("BOTTOM (深层)", bottom_results, bottom_half),
        ]:
            ok = sum(1 for r in results.values() if r["ok"])
            fail = len(skus) - ok
            update_sales = sum(1 for r in results.values() if r.get("update_sales"))
            update_var = sum(1 for r in results.values() if r.get("update_variant"))
            print(f"\n  {label}: 成功={ok}/{len(skus)} ({ok/len(skus)*100:.0f}%)  失败={fail}")
            print(f"    update_sales={update_sales}  update_variant={update_var}")

        # 差值
        top_ok = sum(1 for r in top_results.values() if r["ok"])
        bot_ok = sum(1 for r in bottom_results.values() if r["ok"])
        diff = top_ok - bot_ok
        if diff != 0:
            direction = "更高" if diff > 0 else "更低"
            pct = abs(diff) / len(bottom_half) * 100
            print(f"\n  *** TOP vs BOTTOM: 浅层成功率 {direction} {abs(diff)} 个 ({pct:.1f}pp) ***")
        else:
            print(f"\n  TOP == BOTTOM: 无差异")

        print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
