"""
产品筛选 — 批量计算临界采购价格

用法:
    python -m product-filter.main          # 计算前100条
    python -m product-filter.main --all    # 全量流式计算
    python -m product-filter.main --sku 123456789  # 计算单个产品
"""

import argparse
import sys
from decimal import Decimal

from .calculator import calc_critical_procurement_price, product_critical_price
from .db_reader import fetch_products, fetch_product_by_sku, fetch_products_stream
from .logistics import load_logistics_table


def print_result_header():
    print(
        f"{'SKU':<14} {'标题':<30} {'售价(¥)':>10} {'重量kg':>7} "
        f"{'运费(¥)':>8} {'安全运费(¥)':>10} {'临界采购价(¥)':>14} {'毛利%':>7} {'渠道':<25} {'可行':>4}"
    )
    print("-" * 150)


def print_result(r):
    ch_name = r.matched_channel.delivery_name if r.matched_channel else "N/A"
    print(
        f"{r.sku:<14} {r.title[:28]:<30} {r.price_cny or 0:>10.2f} {r.weight_kg:>7.3f} "
        f"{r.shipping_cost_cny:>8.2f} {r.shipping_safe_cny:>10.2f} "
        f"{r.critical_procurement_cny or 0:>14.2f} {r.gross_margin_pct or 0:>7.1f} "
        f"{ch_name[:23]:<25} {'Y' if r.is_viable else 'N':>4}"
    )


def main():
    parser = argparse.ArgumentParser(description="产品临界采购价格计算")
    parser.add_argument("--all", action="store_true", help="全量流式计算")
    parser.add_argument("--sku", type=str, help="计算单个产品")
    parser.add_argument("--limit", type=int, default=100, help="批量数量(默认100)")
    parser.add_argument("--offset", type=int, default=0, help="分页偏移")
    args = parser.parse_args()

    # 预加载物流费率
    print("正在加载物流费率...", file=sys.stderr)
    channels = load_logistics_table()
    print(f"已加载 {len(channels)} 个物流渠道\n", file=sys.stderr)

    # 单产品模式
    if args.sku:
        product = fetch_product_by_sku(args.sku)
        if not product:
            print(f"未找到产品: {args.sku}", file=sys.stderr)
            return
        r = product_critical_price(product, channels=channels)
        print_result_header()
        print_result(r)
        if r.error:
            print(f"\n错误: {r.error}", file=sys.stderr)
        return

    # 全量流式模式
    if args.all:
        print_result_header()
        total = 0
        viable = 0
        for batch in fetch_products_stream():
            for prod in batch:
                r = product_critical_price(prod, channels=channels)
                print_result(r)
                total += 1
                if r.is_viable:
                    viable += 1
        print(f"\n总计: {total} 条, 可行(临界价>0): {viable} 条", file=sys.stderr)
        return

    # 分页模式
    products = fetch_products(limit=args.limit, offset=args.offset)
    print_result_header()
    for prod in products:
        r = product_critical_price(prod, channels=channels)
        print_result(r)


if __name__ == "__main__":
    main()
