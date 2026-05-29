"""
SKU3 对比测试: 预热（滚动Ozon卖家页）vs 冷请求（直接调用API）

用法:
    uv run python scripts/test_sku3_warm_vs_cold.py --sellers-file sellers.txt --cdp http://127.0.0.1:9222

sellers.txt 每行一个卖家URL
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
os.chdir(ROOT_DIR)

from ozon_pipeline.browser_ozon import BrowserOzonClient
from ozon_pipeline.config import settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sellers-file", required=True)
    parser.add_argument("--cdp", default=settings.chrome_cdp_url or "http://127.0.0.1:9222")
    parser.add_argument("--group-size", type=int, default=20, help="每组测试多少个SKU")
    parser.add_argument("--scroll-wait", type=int, default=15, help="滚动后等待秒数让插件预热")
    parser.add_argument("--scroll-count", type=int, default=3, help="滚动次数")
    parser.add_argument("--batch-concurrency", type=int, default=5, help="SKU3 batch并发数")
    args = parser.parse_args()

    sellers = []
    with open(args.sellers_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                sellers.append(line)
    
    if not sellers:
        print("ERROR: no sellers in file")
        return

    seller = random.choice(sellers)
    print(f"选中卖家: {seller}")

    browser = BrowserOzonClient(cdp_url=args.cdp)
    with browser.session():
        # Step 1: 获取卖家所有SKU（通过API page fetch，不滚动）
        print("\n[Step 1] 获取卖家SKU列表 (API模式)...")
        result = browser.seller_home_products(seller, max_scrolls=0)  # 用API，不滚动
        items = result.get("items") or []
        all_skus = [str(item.get("sku") or "") for item in items if str(item.get("sku") or "")]
        print(f"  获取到 {len(all_skus)} 个SKU")

        # 取出 2*group_size 个SKU用于两组测试
        test_skus = random.sample(all_skus, min(args.group_size * 2, len(all_skus)))
        group_a = test_skus[:args.group_size]
        group_b = test_skus[args.group_size:args.group_size * 2]
        print(f"  Group A (冷请求): {len(group_a)} SKUs")
        print(f"  Group B (预热后): {len(group_b)} SKUs")
        print(f"  A samples: {group_a[:5]}")
        print(f"  B samples: {group_b[:5]}")

        # Step 2: Group B 预热 —— 打开卖家页并滚动
        if args.scroll_count > 0:
            print(f"\n[Step 2] 打开卖家页并滚动 {args.scroll_count} 次, 等待 {args.scroll_wait}s...")
            page = browser._context.new_page()
            try:
                page.goto(seller, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
                for i in range(args.scroll_count):
                    page.mouse.wheel(0, 3000)
                    page.wait_for_timeout(1500)
                    print(f"  滚动 {i+1}/{args.scroll_count}")

                print(f"  等待 {args.scroll_wait}s 让插件预热...")
                time.sleep(args.scroll_wait)
            finally:
                try:
                    page.close()
                except Exception:
                    pass

        # Step 3: 测试 Group A (冷请求)
        print(f"\n[Step 3] 测试 Group A (冷请求) - {len(group_a)} SKUs, concurrency={args.batch_concurrency}")
        a_results = _test_sku3_batch(browser, group_a, args.batch_concurrency)

        # Step 4: 测试 Group B (预热后)
        print(f"\n[Step 4] 测试 Group B (预热后) - {len(group_b)} SKUs, concurrency={args.batch_concurrency}")
        b_results = _test_sku3_batch(browser, group_b, args.batch_concurrency)

        # Step 5: 对比分析
        _compare_results(group_a, a_results, group_b, b_results)


def _test_sku3_batch(browser, skus, concurrency):
    results = {}
    raw = browser.top_list_sku3_batch(skus, concurrency=concurrency)
    for sku in skus:
        s = str(sku)
        data = raw.get(s)
        if isinstance(data, dict):
            status = data.get("status") or {}
            results[s] = {
                "ok": True,
                "update_sales": bool(status.get("update_sales")),
                "update_variant": bool(status.get("update_variant")),
                "data_keys": list(data.keys()),
            }
        else:
            results[s] = {"ok": False, "error": repr(data)[:100] if data else "MISSING"}
    return results


def _compare_results(group_a, a_results, group_b, b_results):
    print("\n" + "=" * 60)
    print("对比结果")
    print("=" * 60)

    a_ok = sum(1 for r in a_results.values() if r["ok"])
    b_ok = sum(1 for r in b_results.values() if r["ok"])
    a_fail = len(group_a) - a_ok
    b_fail = len(group_b) - b_ok

    print(f"\n  Group A (冷请求):     成功={a_ok}/{len(group_a)}  ({a_ok/len(group_a)*100:.0f}%)  失败={a_fail}")
    print(f"  Group B (滚动预热后): 成功={b_ok}/{len(group_b)}  ({b_ok/len(group_b)*100:.0f}%)  失败={b_fail}")

    if a_ok != b_ok:
        diff = b_ok - a_ok
        direction = "更高" if diff > 0 else "更低"
        print(f"\n  *** 差异: 滚动预热后成功率 {direction} {abs(diff)} 个 ({abs(diff)/len(group_b)*100:.0f}pp) ***")

    # 详细分析成功的数据
    a_update_sales = sum(1 for r in a_results.values() if r["ok"] and r["update_sales"])
    b_update_sales = sum(1 for r in b_results.values() if r["ok"] and r["update_sales"])
    a_update_variant = sum(1 for r in a_results.values() if r["ok"] and r["update_variant"])
    b_update_variant = sum(1 for r in b_results.values() if r["ok"] and r["update_variant"])

    print(f"\n  Group A update_sales={a_update_sales}  update_variant={a_update_variant}")
    print(f"  Group B update_sales={b_update_sales}  update_variant={b_update_variant}")

    # 失败样本
    if a_fail:
        print(f"\n  Group A 失败样本:")
        for sku, r in a_results.items():
            if not r["ok"]:
                print(f"    {sku}: {r['error']}")
                if sum(1 for x in a_results.values() if not x["ok"]) > 5:
                    break
    if b_fail:
        print(f"\n  Group B 失败样本:")
        for sku, r in b_results.items():
            if not r["ok"]:
                print(f"    {sku}: {r['error']}")
                if sum(1 for x in b_results.values() if not x["ok"]) > 5:
                    break

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
