"""
回填脚本: 从 seller_home_skus.raw_json 解析 tile 高价值字段，更新 sku_products。

用法:
    uv run python scripts/backfill_sku_products_tile.py [--limit N] [--dry-run]

--dry-run: 只打印前10条，不实际写入
--limit N: 只处理 N 条
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from ozon_pipeline import db
from ozon_pipeline.ozon_frontend import parse_seller_home_tile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    count_sql = "SELECT COUNT(*) FROM seller_home_skus WHERE raw_json IS NOT NULL"
    total = db.fetch_one(count_sql)["COUNT(*)"]
    print(f"raw_json IS NOT NULL rows: {total}")

    select_sql = "SELECT sku, raw_json FROM seller_home_skus WHERE raw_json IS NOT NULL"
    params = {}
    if args.limit and args.limit > 0:
        select_sql += " LIMIT %(limit)s"
        params["limit"] = int(args.limit)

    rows = db.fetch_all(select_sql, params)
    print(f"Processing {len(rows)} rows...")

    updated = 0
    skipped_no_tile = 0
    skipped_no_sku = 0
    errors = 0

    for i, row in enumerate(rows):
        sku = row["sku"]
        raw_json_str = row["raw_json"]
        if not raw_json_str:
            skipped_no_tile += 1
            continue

        try:
            item = json.loads(raw_json_str) if isinstance(raw_json_str, str) else raw_json_str
        except (json.JSONDecodeError, TypeError):
            skipped_no_tile += 1
            continue

        # item.raw is the raw tile; but older schema stored the full item, newer stores just tile
        tile = item.get("raw") if isinstance(item, dict) else None
        if not isinstance(tile, dict):
            # Try: maybe the stored value IS the tile directly (new format)
            if isinstance(item, dict) and "mainState" in item:
                tile = item
            else:
                skipped_no_tile += 1
                continue

        parsed = parse_seller_home_tile(tile)
        if not parsed:
            skipped_no_tile += 1
            continue

        if args.dry_run:
            if i < 10:
                print(f"\n[{i+1}] sku={sku}")
                for k, v in sorted(parsed.items()):
                    print(f"  {k}: {v}")
            continue

        badges_val = parsed.get("badges")
        badges_json = json.dumps(badges_val, ensure_ascii=False) if badges_val else None
        seller_home_json = json.dumps(tile, ensure_ascii=False) if tile else None

        try:
            result = db.execute(
                """
                UPDATE sku_products
                SET
                  seller_rating = COALESCE(VALUES(seller_rating), seller_rating),
                  seller_review_count = COALESCE(VALUES(seller_review_count), seller_review_count),
                  stock_max = COALESCE(VALUES(stock_max), stock_max),
                  stock_label = COALESCE(VALUES(stock_label), stock_label),
                  brand_logo_url = COALESCE(VALUES(brand_logo_url), brand_logo_url),
                  badges = COALESCE(VALUES(badges), badges),
                  delivery_hint = COALESCE(VALUES(delivery_hint), delivery_hint),
                  raw_seller_home_json = COALESCE(VALUES(raw_seller_home_json), raw_seller_home_json),
                  updated_at = CURRENT_TIMESTAMP
                WHERE sku = %(sku)s
                """,
                {
                    "sku": sku,
                    "seller_rating": parsed.get("seller_rating"),
                    "seller_review_count": parsed.get("seller_review_count"),
                    "stock_max": parsed.get("stock_max"),
                    "stock_label": parsed.get("stock_label"),
                    "brand_logo_url": parsed.get("brand_logo_url"),
                    "badges": badges_json,
                    "delivery_hint": parsed.get("delivery_hint"),
                    "raw_seller_home_json": seller_home_json,
                },
            )
            if result > 0:
                updated += 1
            else:
                skipped_no_sku += 1
        except Exception as exc:
            errors += 1
            if errors <= 5:
                print(f"  ERR sku={sku}: {exc}")

        if (i + 1) % 5000 == 0:
            print(f"  progress: {i+1}/{len(rows)} updated={updated} no_sku={skipped_no_sku} errors={errors}")

    print(f"\nDone: updated={updated} no_tile={skipped_no_tile} no_sku={skipped_no_sku} errors={errors}")


if __name__ == "__main__":
    main()
