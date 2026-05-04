from __future__ import annotations

import argparse
import csv
from collections import deque
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import db
from .browser_ozon import BrowserOzonClient
from .config import ROOT_DIR, settings
from .maozi_api import MaoziClient
from .ozon_frontend import OzonFrontendClient
from .repository import (
    finish_top_list_run,
    get_seller_shop,
    get_recent_top_list_run,
    list_seed_pool_skus,
    list_top_list_skus,
    mark_seed_status,
    mark_seed_pool_processed,
    mark_seed_pool_selected,
    mark_seller_collected,
    mark_top_list_sku_processed,
    mark_top_list_sku_selected,
    parse_sku3_response,
    seed_pool_sku_due_state,
    seller_recently_collected,
    start_top_list_run,
    top_list_query_key,
    top_list_sku_due_state,
    upsert_seller_shop,
    upsert_seed_sku,
    upsert_seller_home_sku,
    upsert_seller_offer,
    upsert_sku3_response,
    upsert_seed_pool_item,
    upsert_top_list_item,
)
from .rules import TOP_LIST_SEED_RULE, evaluate_selection_rule, evaluate_top_list_prefilter


def cmd_migrate(_: argparse.Namespace) -> None:
    sql_dir = ROOT_DIR / "sql"
    files = sorted(sql_dir.glob("*.sql"))
    for path in files:
        db.run_sql_file(path)
    print(f"migration applied ({len(files)} files)")


def cmd_import_seeds(args: argparse.Namespace) -> None:
    path = Path(args.path)
    count = 0
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                sku = row.get("sku") or row.get("SKU")
                if sku:
                    upsert_seed_sku(str(sku).strip(), source=args.source)
                    count += 1
    else:
        for line in path.read_text(encoding="utf-8").splitlines():
            sku = line.strip()
            if sku:
                upsert_seed_sku(sku, source=args.source)
                count += 1
    print(f"imported {count} seed skus")


def build_browser_client(args: argparse.Namespace, *, headless_override: bool | None = None) -> BrowserOzonClient:
    headless = args.headless if headless_override is None else headless_override
    return BrowserOzonClient(
        profile_dir=args.profile_dir,
        extension_dir=args.extension_dir,
        executable_path=args.chrome_exe,
        channel=args.channel,
        proxy_server=args.proxy_server,
        cdp_url=args.cdp_url,
        remote_debugging_port=args.remote_debugging_port,
        headless=headless,
    )


def default_top_list_filters(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "mainType": args.main_type,
        "sku": args.sku or "",
        "category1": args.category1 or "",
        "category2": args.category2 or "",
        "category3": args.category3 or "",
        "name": args.name or "",
        "sales_min": args.sales_min or "",
        "sales_max": args.sales_max or "",
        "day_sales_min": args.day_sales_min or "",
        "day_sales_max": args.day_sales_max or "",
        "avg_price_min": args.avg_price_min or "",
        "avg_price_max": args.avg_price_max or "",
        "sales_dynamics_min": args.sales_dynamics_min or "",
        "sales_dynamics_max": args.sales_dynamics_max or "",
        "conv_to_cart_pdp_min": args.conv_to_cart_pdp_min or "",
        "conv_to_cart_pdp_max": args.conv_to_cart_pdp_max or "",
        "conv_to_cart_search_min": args.conv_to_cart_search_min or "",
        "conv_to_cart_search_max": args.conv_to_cart_search_max or "",
        "sales_schema": args.sales_schema or "",
        "sold_sum_min": args.sold_sum_min or "",
        "sold_sum_max": args.sold_sum_max or "",
        "avg_delivery_days_min": args.avg_delivery_days_min or "",
        "avg_delivery_days_max": args.avg_delivery_days_max or "",
        "create_date": [args.create_date_from, args.create_date_to],
        "sort_by": args.sort_by,
        "sort_order": args.sort_order,
    }


def cmd_fetch_sku(args: argparse.Namespace) -> None:
    client = MaoziClient()
    browser = build_browser_client(args)
    result = process_sku(args.sku, client, browser, source="manual_fetch")
    if not result["qualified"]:
        print("skipped sku:", result["sku"], result["rule_reason"])
        return
    print(
        "stored qualified sku:",
        result["sku"],
        "maozi_source=",
        result["maozi_source"],
        "seller_offers=",
        result["seller_offer_count"],
        "update_sales=",
        bool(result["status_update_sales"]),
        "update_variant=",
        bool(result["status_update_variant"]),
    )


def cmd_fetch_offers(args: argparse.Namespace) -> None:
    client = OzonFrontendClient()
    offers = client.seller_offers(args.sku)
    for offer in offers:
        upsert_seller_offer(args.sku, offer)
    print(f"stored {len(offers)} seller offers for sku {args.sku}")


def cmd_fetch_offers_browser(args: argparse.Namespace) -> None:
    client = build_browser_client(args)
    offers = client.seller_offers(args.sku)
    for offer in offers:
        upsert_seller_offer(args.sku, offer)
    print(f"stored {len(offers)} browser seller offers for sku {args.sku}")


def cmd_fetch_seller_home(args: argparse.Namespace) -> None:
    client = build_browser_client(args)
    result = client.seller_home_products(args.url, max_scrolls=args.max_scrolls)
    items = result.get("items") or []
    saved = 0
    for item in items:
        if upsert_seller_home_sku(args.url, item):
            saved += 1
    print(f"stored {saved} seller-home products from {args.url}")
    for item in items[: args.preview]:
        print(item.get("href"), "|", item.get("title") or "<no-title>")


def cmd_crawl_seller(args: argparse.Namespace) -> None:
    browser = build_browser_client(args)
    maozi = MaoziClient()
    seller_result = browser.seller_home_products(args.url, max_scrolls=args.max_scrolls)
    items = seller_result.get("items") or []
    discovered = 0
    processed = 0
    qualified = 0
    rejected = 0
    total_seller_offers = 0
    for item in items[: args.limit or None]:
        sku = upsert_seller_home_sku(args.url, item)
        if not sku:
            continue
        discovered += 1
        upsert_seed_sku(sku, source=f"seller_home:{args.url}")
        result = process_sku(sku, maozi, browser, source=f"seller_home:{args.url}")
        processed += 1
        if result["qualified"]:
            qualified += 1
            total_seller_offers += result["seller_offer_count"]
        else:
            rejected += 1
        print(
            sku,
            "qualified" if result["qualified"] else "rejected",
            "| maozi=",
            result["maozi_source"],
            "| offers=",
            result["seller_offer_count"],
            "|",
            result["rule_reason"],
        )
    print(
        "crawl summary:",
        f"discovered={discovered}",
        f"processed={processed}",
        f"qualified={qualified}",
        f"rejected={rejected}",
        f"seller_offers={total_seller_offers}",
    )


def run_seller_network(
    queue: deque[dict[str, Any]],
    *,
    browser: BrowserOzonClient,
    maozi: MaoziClient,
    max_depth: int,
    max_sellers: int,
    sku_limit: int,
    max_scrolls: int,
) -> dict[str, int]:
    max_sellers = max_sellers if max_sellers > 0 else 1000000
    max_depth = max_depth if max_depth >= 0 else 0
    sku_limit = sku_limit if sku_limit > 0 else 0
    seen_urls: set[str] = set()
    processed_sellers = 0
    skipped_recent = 0
    discovered_sellers = len(queue)
    total_skus = 0
    qualified_skus = 0
    rejected_skus = 0
    stored_offer_rows = 0

    while queue and processed_sellers < max_sellers:
        seller = queue.popleft()
        url = seller["url"]
        depth = int(seller["depth"])
        name = seller.get("name")
        normalized_url = url.rstrip("/")
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        if depth > max_depth:
            continue

        key = upsert_seller_shop(url, name=name)
        if depth > 0 and seller_recently_collected(url):
            skipped_recent += 1
            shop = get_seller_shop(key) or {}
            print(
                "skip recent seller:",
                url,
                "| name=",
                shop.get("name") or name or "<unknown>",
            )
            continue

        result = browser.seller_home_products(url, max_scrolls=max_scrolls)
        items = result.get("items") or []
        source = result.get("source") or "unknown"
        print(
            "crawl seller:",
            url,
            "| depth=",
            depth,
            "| source=",
            source,
            "| items=",
            len(items),
        )

        seller_offer_urls: dict[str, dict[str, Any]] = {}
        seller_skus = 0
        seller_qualified = 0
        seller_rejected = 0
        seller_offer_rows = 0
        for item in items[: sku_limit or None]:
            sku = upsert_seller_home_sku(url, item)
            if not sku:
                continue
            seller_skus += 1
            total_skus += 1
            upsert_seed_sku(sku, source=f"seller_home:{url}")
            sku_result = process_sku(sku, maozi, browser, source=f"seller_home:{url}")
            if sku_result["qualified"]:
                seller_qualified += 1
                qualified_skus += 1
                offers = sku_result.get("offers") or []
                seller_offer_rows += len(offers)
                stored_offer_rows += len(offers)
                for offer in offers:
                    home_url = (offer.get("seller_home_url") or "").strip()
                    if not home_url:
                        continue
                    seller_offer_urls[home_url.rstrip("/")] = {
                        "url": home_url,
                        "name": offer.get("name"),
                        "depth": depth + 1,
                    }
            else:
                seller_rejected += 1
                rejected_skus += 1
            print(
                "  sku:",
                sku,
                "|",
                "qualified" if sku_result["qualified"] else "rejected",
                "| offers=",
                sku_result["seller_offer_count"],
                "|",
                sku_result["rule_reason"],
            )

        mark_seller_collected(key)
        processed_sellers += 1
        print(
            "seller summary:",
            f"skus={seller_skus}",
            f"qualified={seller_qualified}",
            f"rejected={seller_rejected}",
            f"offers={seller_offer_rows}",
        )

        if depth < max_depth:
            for next_seller in seller_offer_urls.values():
                next_url = next_seller["url"].rstrip("/")
                if next_url in seen_urls:
                    continue
                queue.append(next_seller)
                discovered_sellers += 1

    return {
        "processed_sellers": processed_sellers,
        "skipped_recent": skipped_recent,
        "queued_sellers": discovered_sellers,
        "total_skus": total_skus,
        "qualified_skus": qualified_skus,
        "rejected_skus": rejected_skus,
        "seller_offers": stored_offer_rows,
    }


def cmd_crawl_seller_network(args: argparse.Namespace) -> None:
    browser = build_browser_client(args)
    maozi = MaoziClient()
    stats = run_seller_network(
        deque([{"url": args.url, "depth": 0, "name": args.name or None}]),
        browser=browser,
        maozi=maozi,
        max_depth=args.max_depth,
        max_sellers=args.max_sellers,
        sku_limit=args.sku_limit,
        max_scrolls=args.max_scrolls,
    )
    print(
        "network crawl summary:",
        f"processed_sellers={stats['processed_sellers']}",
        f"skipped_recent={stats['skipped_recent']}",
        f"queued_sellers={stats['queued_sellers']}",
        f"total_skus={stats['total_skus']}",
        f"qualified_skus={stats['qualified_skus']}",
        f"rejected_skus={stats['rejected_skus']}",
        f"seller_offers={stats['seller_offers']}",
    )


def cmd_crawl_top_list_network(args: argparse.Namespace) -> None:
    browser = build_browser_client(args)
    maozi = MaoziClient()
    filters = default_top_list_filters(args)
    query_key = top_list_query_key(filters)
    page_from = max(1, int(args.page_from))
    page_to = max(page_from, int(args.page_to))
    page_size = max(1, min(int(args.page_size), 50))
    refresh_hours = max(1, int(args.refresh_hours))
    cached_run = None if args.force_refresh else get_recent_top_list_run(query_key, refresh_hours)
    run_id = start_top_list_run(
        query_key,
        args.main_type,
        filters,
        page_from=page_from,
        page_to=page_to,
        page_size=page_size,
    )
    pages_fetched = 0
    items_fetched = 0
    try:
        if cached_run:
            print(
                "reuse cached top-list snapshot:",
                f"run_id={cached_run['id']}",
                f"started_at={cached_run['started_at']}",
                f"within_hours={refresh_hours}",
            )
        else:
            for page_no in range(page_from, page_to + 1):
                raw = browser.top_list_page(filters, page_no=page_no, page_size=page_size)
                if not raw.get("ok"):
                    raise RuntimeError(
                        f"top-list request failed on page {page_no}: HTTP {raw.get('status')} {raw.get('text')}"
                    )
                body = raw.get("data") or {}
                if body.get("code") != 1:
                    raise RuntimeError(f"top-list API returned error on page {page_no}: {body}")
                payload = body.get("data") or {}
                items = payload.get("data") or []
                if not items:
                    break
                for index, item in enumerate(items, start=1):
                    upsert_top_list_item(query_key, run_id, page_no, index, item)
                    upsert_seed_pool_item(query_key, run_id, page_no, index, item)
                pages_fetched += 1
                items_fetched += len(items)
                print(
                    "top-list page:",
                    page_no,
                    f"/ {payload.get('last_page') or '?'}",
                    "| items=",
                    len(items),
                )
                last_page = int(payload.get("last_page") or page_no)
                if page_no >= last_page:
                    break

        cached_items = list_seed_pool_skus(query_key)
        due_items: list[dict[str, Any]] = []
        for item in cached_items:
            due, due_reason = seed_pool_sku_due_state(item)
            if due:
                item["_due_reason"] = due_reason
                due_items.append(item)
        if args.process_limit > 0:
            due_items = due_items[: args.process_limit]

        print(
            "top-list cache summary:",
            f"query_key={query_key}",
            f"cached_skus={len(cached_items)}",
            f"due_skus={len(due_items)}",
        )

        if args.skip_process:
            finish_top_list_run(
                run_id,
                status="success",
                pages_fetched=pages_fetched,
                items_fetched=items_fetched,
                due_skus=len(due_items),
            )
            print("skip process enabled; only refreshed or reused top-list cache")
            return

        processed_skus = 0
        qualified_skus = 0
        rejected_skus = 0
        prefiltered_skus = 0
        seller_offer_rows = 0
        next_sellers: dict[str, dict[str, Any]] = {}
        strict_seed_inserts = 0
        failed_skus = 0
        for item in due_items:
            sku = str(item["sku"])
            mark_seed_pool_selected(query_key, sku)
            prefilter = evaluate_top_list_prefilter(item, rule=TOP_LIST_SEED_RULE)
            if not prefilter.matched:
                prefiltered_skus += 1
                rejected_skus += 1
                mark_seed_pool_processed(
                    query_key,
                    sku,
                    status="rejected",
                    snapshot_hash=item.get("snapshot_hash"),
                    reason=f"种子预筛未命中: {prefilter.summary}",
                )
                print(
                    "seed sku:",
                    sku,
                    "| prefiltered-rejected |",
                    item.get("_due_reason"),
                    "|",
                    prefilter.summary,
                )
                continue
            try:
                sku_result = process_top_list_sku(sku, item, maozi, browser, source=f"top_list:{args.main_type}")
            except Exception as exc:
                failed_skus += 1
                mark_seed_pool_processed(
                    query_key,
                    sku,
                    status="failed",
                    snapshot_hash=item.get("snapshot_hash"),
                    reason=f"种子处理失败: {exc}",
                )
                print("seed sku:", sku, "| failed |", exc)
                continue
            processed_skus += 1
            if sku_result["qualified"]:
                qualified_skus += 1
                offers = sku_result.get("offers") or []
                seller_offer_rows += len(offers)
                for offer in offers:
                    home_url = (offer.get("seller_home_url") or "").strip()
                    if not home_url:
                        continue
                    next_sellers[home_url.rstrip("/")] = {
                        "url": home_url,
                        "name": offer.get("name"),
                        "depth": 1,
                    }
            else:
                rejected_skus += 1
            if sku_result.get("strict_qualified"):
                strict_seed_inserts += 1

            mark_seed_pool_processed(
                query_key,
                sku,
                status="expanded" if sku_result["qualified"] else "rejected",
                snapshot_hash=item.get("snapshot_hash"),
                reason=sku_result["rule_reason"],
                seller_offer_count=sku_result["seller_offer_count"] or None,
            )
            print(
                "seed sku:",
                sku,
                "|",
                "expanded" if sku_result["qualified"] else "rejected",
                "| due=",
                item.get("_due_reason"),
                "| offers=",
                sku_result["seller_offer_count"],
                "|",
                sku_result["rule_reason"],
            )

        seller_stats = {
            "processed_sellers": 0,
            "skipped_recent": 0,
            "queued_sellers": len(next_sellers),
            "total_skus": 0,
            "qualified_skus": 0,
            "rejected_skus": 0,
            "seller_offers": 0,
        }
        if next_sellers and args.max_depth >= 1 and args.max_sellers != 0:
            seller_stats = run_seller_network(
                deque(next_sellers.values()),
                browser=browser,
                maozi=maozi,
                max_depth=args.max_depth,
                max_sellers=args.max_sellers,
                sku_limit=args.sku_limit,
                max_scrolls=args.max_scrolls,
            )

        finish_top_list_run(
            run_id,
            status="success",
            pages_fetched=pages_fetched,
            items_fetched=items_fetched,
            due_skus=len(due_items),
            processed_skus=processed_skus + prefiltered_skus,
            qualified_skus=qualified_skus,
            rejected_skus=rejected_skus,
            seller_expansions=seller_stats["processed_sellers"],
        )
        print(
            "top-list crawl summary:",
            f"seed_pool_skus={len(cached_items)}",
            f"due_skus={len(due_items)}",
            f"prefiltered_skus={prefiltered_skus}",
            f"processed_skus={processed_skus}",
            f"expanded_seed_skus={qualified_skus}",
            f"rejected_skus={rejected_skus}",
            f"failed_skus={failed_skus}",
            f"strict_seed_inserts={strict_seed_inserts}",
            f"top_list_offer_rows={seller_offer_rows}",
            f"expanded_sellers={seller_stats['processed_sellers']}",
            f"expanded_seller_skus={seller_stats['total_skus']}",
        )
    except Exception as exc:
        finish_top_list_run(
            run_id,
            status="failed",
            pages_fetched=pages_fetched,
            items_fetched=items_fetched,
            error_message=str(exc),
        )
        raise


def cmd_show_browser_config(args: argparse.Namespace) -> None:
    client = build_browser_client(args)
    info = client.describe()
    print("resolved browser configuration:")
    for key, value in info.items():
        print(f"  {key}: {value}")
    print("tip: one browser profile directory should serve exactly one target Google/Ozon/plugin account set.")


def cmd_warmup_browser(args: argparse.Namespace) -> None:
    client = build_browser_client(args, headless_override=False)
    info = client.describe()
    print("warming up browser profile:")
    for key, value in info.items():
        print(f"  {key}: {value}")
    print("open this profile and log into the exact target Google account, Ozon account, and plugin account.")
    client.warmup(args.url)


def cmd_launch_real_chrome(args: argparse.Namespace) -> None:
    client = build_browser_client(args, headless_override=False)
    info = client.describe()
    print("launching real Chrome:")
    for key, value in info.items():
        print(f"  {key}: {value}")
    print("log into the target accounts in this real Chrome window; keep it open if you plan to attach via CDP.")
    client.launch_real_chrome(args.url)


def add_browser_options(parser: argparse.ArgumentParser, include_headless: bool = False) -> None:
    parser.add_argument("--profile-dir", default=None)
    parser.add_argument("--extension-dir", default=None)
    parser.add_argument("--chrome-exe", default=None)
    parser.add_argument("--channel", default=None)
    parser.add_argument("--proxy-server", default=None)
    parser.add_argument("--cdp-url", default=None)
    parser.add_argument("--remote-debugging-port", type=int, default=None)
    if include_headless:
        parser.add_argument("--headless", action="store_true")


def add_top_list_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--main-type", default="hot")
    parser.add_argument("--sku", default="")
    parser.add_argument("--name", default="")
    parser.add_argument("--category1", default="")
    parser.add_argument("--category2", default="")
    parser.add_argument("--category3", default="")
    parser.add_argument("--sales-min", default=settings.top_list_default_sales_min)
    parser.add_argument("--sales-max", default=settings.top_list_default_sales_max)
    parser.add_argument("--day-sales-min", default="")
    parser.add_argument("--day-sales-max", default="")
    parser.add_argument("--avg-price-min", default="200")
    parser.add_argument("--avg-price-max", default="10000")
    parser.add_argument("--sales-dynamics-min", default="")
    parser.add_argument("--sales-dynamics-max", default="")
    parser.add_argument("--conv-to-cart-pdp-min", default="")
    parser.add_argument("--conv-to-cart-pdp-max", default="")
    parser.add_argument("--conv-to-cart-search-min", default="")
    parser.add_argument("--conv-to-cart-search-max", default="")
    parser.add_argument("--sales-schema", default="FBS")
    parser.add_argument("--sold-sum-min", default="")
    parser.add_argument("--sold-sum-max", default="")
    parser.add_argument("--avg-delivery-days-min", default="")
    parser.add_argument("--avg-delivery-days-max", default="")
    parser.add_argument("--create-date-from", default=settings.top_list_default_create_date_from)
    parser.add_argument("--create-date-to", default=settings.top_list_default_create_date_to or date.today().isoformat())
    parser.add_argument("--sort-by", default="sold_sum")
    parser.add_argument("--sort-order", default="desc")
    parser.add_argument("--page-from", type=int, default=1)
    parser.add_argument("--page-to", type=int, default=settings.top_list_default_max_pages)
    parser.add_argument("--page-size", type=int, default=settings.top_list_default_page_size)
    parser.add_argument("--refresh-hours", type=int, default=settings.top_list_refresh_hours)
    parser.add_argument("--process-limit", type=int, default=0)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--skip-process", action="store_true")


def load_seller_offers(sku: str, browser: BrowserOzonClient) -> list[dict[str, Any]]:
    if browser.cdp_url:
        try:
            return browser.seller_offers(sku)
        except Exception:
            pass
    try:
        return OzonFrontendClient().seller_offers(sku)
    except Exception:
        return browser.seller_offers(sku)


def load_maozi_sku3(sku: str, maozi: MaoziClient, browser: BrowserOzonClient, *, prefer_direct: bool) -> tuple[dict[str, Any], str]:
    if prefer_direct:
        try:
            return maozi.sku3(sku), "direct_api"
        except Exception:
            return browser.maozi_sku3(sku), "extension_page"
    if browser.cdp_url:
        try:
            return browser.maozi_sku3(sku), "extension_page"
        except Exception:
            return maozi.sku3(sku), "direct_api"
    try:
        return maozi.sku3(sku), "direct_api"
    except Exception:
        return browser.maozi_sku3(sku), "extension_page"


def top_list_metric_overrides(item: dict[str, Any]) -> dict[str, Any]:
    metric_overrides: dict[str, Any] = {}
    if item.get("brand"):
        metric_overrides["brand"] = item.get("brand")
    if item.get("sold_count") is not None:
        metric_overrides["sold_count"] = item.get("sold_count")
    if item.get("sales_schema"):
        metric_overrides["sales_schema"] = item.get("sales_schema")
    weight = item.get("weight")
    if weight not in (None, "", 0, "0", 0.0):
        metric_overrides["custom_weight_g"] = weight
        metric_overrides["custom_weight_text"] = f"{weight}g"
    create_value = item.get("nullable_create_date")
    if create_value:
        if isinstance(create_value, datetime):
            created = create_value.date()
        elif isinstance(create_value, date):
            created = create_value
        else:
            created = datetime.strptime(str(create_value), "%Y-%m-%d").date()
        create_days = (date.today() - created).days
        metric_overrides["create_days"] = create_days
        metric_overrides["nullable_create_date_text"] = f"{created.isoformat()}({create_days}天)"
    return metric_overrides


def top_list_product_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_url": item.get("link"),
        "title": item.get("name"),
        "brand": item.get("brand"),
        "price": item.get("avg_price"),
        "currency": "RUB",
        "main_image_url": item.get("photo"),
        "raw": {"top_list": item},
    }


def process_top_list_sku(
    sku: str,
    top_item: dict[str, Any],
    maozi: MaoziClient,
    browser: BrowserOzonClient,
    source: str,
) -> dict[str, Any]:
    try:
        response, maozi_source = load_maozi_sku3(sku, maozi, browser, prefer_direct=True)
    except Exception as exc:
        raise RuntimeError(f"failed to fetch maozi sku3 for top-list sku {sku}") from exc

    product_snapshot = top_list_product_snapshot(top_item)
    metric_overrides = top_list_metric_overrides(top_item)
    metric_preview = parse_sku3_response(sku, response)
    for key, value in metric_overrides.items():
        if value is not None and value != "":
            metric_preview[key] = value

    preview_rule = evaluate_selection_rule(metric_preview, product_snapshot, None, rule=TOP_LIST_SEED_RULE)
    non_offer_reasons = [reason for reason in preview_rule.reasons if reason != "跟卖人数缺失"]
    plugin_card = None
    plugin_rescuable_reasons = {
        "月销量缺失",
        "重量(g)缺失",
        "退货取消率缺失",
        "发货模式不包含FBS",
    }
    if non_offer_reasons and all(reason in plugin_rescuable_reasons for reason in non_offer_reasons):
        try:
            plugin_card = browser.plugin_card_snapshot(sku)
            merged_overrides = dict(metric_overrides)
            merged_overrides.update(plugin_card.get("metric_overrides") or {})
            metric_overrides = merged_overrides
            metric_preview = parse_sku3_response(sku, response)
            for key, value in metric_overrides.items():
                if value is not None and value != "":
                    metric_preview[key] = value
            preview_rule = evaluate_selection_rule(metric_preview, product_snapshot, None, rule=TOP_LIST_SEED_RULE)
            non_offer_reasons = [reason for reason in preview_rule.reasons if reason != "跟卖人数缺失"]
        except Exception:
            plugin_card = None

    offers: list[dict[str, Any]] | None = None
    seller_offer_count: int | None = None
    if not non_offer_reasons:
        try:
            offers = load_seller_offers(sku, browser)
            seller_offer_count = len(offers)
            preview_rule = evaluate_selection_rule(
                metric_preview,
                product_snapshot,
                seller_offer_count,
                rule=TOP_LIST_SEED_RULE,
            )
        except Exception:
            offers = None

    strict_rule = evaluate_selection_rule(metric_preview, product_snapshot, seller_offer_count)
    if preview_rule.matched and offers is not None:
        for offer in offers:
            upsert_seller_offer(sku, offer)
    if strict_rule.matched:
        upsert_sku3_response(
            sku,
            response,
            product_data=product_snapshot,
            seller_offer_count=seller_offer_count,
            metric_overrides=metric_overrides,
            apply_selection_rule=True,
        )
    return {
        "sku": sku,
        "qualified": preview_rule.matched,
        "strict_qualified": strict_rule.matched,
        "rule_reason": preview_rule.summary,
        "status_update_sales": metric_preview["status_update_sales"],
        "status_update_variant": metric_preview["status_update_variant"],
        "seller_offer_count": seller_offer_count or 0,
        "maozi_source": maozi_source,
        "offers": offers or [],
    }


def process_sku(sku: str, maozi: MaoziClient, browser: BrowserOzonClient, source: str) -> dict[str, Any]:
    upsert_seed_sku(sku, source=source)
    if browser.cdp_url:
        try:
            response = browser.maozi_sku3(sku)
            maozi_source = "extension_page"
        except Exception as browser_exc:
            try:
                response = maozi.sku3(sku)
                maozi_source = "direct_api"
            except Exception as api_exc:
                raise RuntimeError(
                    f"failed to fetch maozi sku3 for sku {sku}; "
                    "the extension popup is likely not logged in and direct API fallback was also rejected"
                ) from api_exc
    else:
        maozi_source = "direct_api"
        try:
            response = maozi.sku3(sku)
        except Exception as api_exc:
            try:
                response = browser.maozi_sku3(sku)
                maozi_source = "extension_page"
            except Exception as browser_exc:
                raise RuntimeError(
                    f"failed to fetch maozi sku3 for sku {sku}; "
                    "direct API and browser-extension fallback both failed"
                ) from browser_exc
    product_snapshot = browser.product_snapshot(sku)
    plugin_card = browser.plugin_card_snapshot(sku)
    offers: list[dict[str, Any]] | None = None
    seller_offer_count = plugin_card.get("seller_offer_count")

    raw_product = dict(product_snapshot.get("raw") or {})
    raw_product["plugin_card"] = {
        "line_map": plugin_card.get("line_map"),
        "card_lines": plugin_card.get("card_lines"),
    }
    product_snapshot["raw"] = raw_product
    metric_preview = parse_sku3_response(sku, response)
    for key, value in (plugin_card.get("metric_overrides") or {}).items():
        if value is not None and value != "":
            metric_preview[key] = value
    preview_rule = evaluate_selection_rule(metric_preview, product_snapshot, seller_offer_count)
    if not preview_rule.matched and seller_offer_count is None:
        non_offer_reasons = [reason for reason in preview_rule.reasons if reason != "跟卖人数缺失"]
        if not non_offer_reasons:
            try:
                offers = load_seller_offers(sku, browser)
                seller_offer_count = len(offers)
                preview_rule = evaluate_selection_rule(metric_preview, product_snapshot, seller_offer_count)
            except Exception:
                offers = None

    if not preview_rule.matched:
        metric = upsert_sku3_response(
            sku,
            response,
            product_data=product_snapshot,
            seller_offer_count=seller_offer_count,
            metric_overrides=plugin_card.get("metric_overrides"),
            apply_selection_rule=True,
        )
        return {
            "sku": metric["sku"],
            "qualified": False,
            "rule_reason": metric.get("rule_reason") or "未命中规则",
            "status_update_sales": metric["status_update_sales"],
            "status_update_variant": metric["status_update_variant"],
            "seller_offer_count": seller_offer_count or 0,
            "maozi_source": maozi_source,
        }

    if offers is None:
        try:
            offers = load_seller_offers(sku, browser)
            seller_offer_count = len(offers)
        except Exception:
            offers = None

    metric = upsert_sku3_response(
        sku,
        response,
        product_data=product_snapshot,
        seller_offer_count=seller_offer_count,
        metric_overrides=plugin_card.get("metric_overrides"),
        apply_selection_rule=True,
    )
    if metric.get("qualified") and offers is not None:
        for offer in offers:
            upsert_seller_offer(sku, offer)
    return {
        "sku": metric["sku"],
        "qualified": bool(metric.get("qualified")),
        "rule_reason": metric.get("rule_reason") or "未命中规则",
        "status_update_sales": metric["status_update_sales"],
        "status_update_variant": metric["status_update_variant"],
        "seller_offer_count": seller_offer_count or 0,
        "maozi_source": maozi_source,
        "offers": offers or [],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ozon-pipeline")
    sub = parser.add_subparsers(required=True)

    migrate = sub.add_parser("migrate")
    migrate.set_defaults(func=cmd_migrate)

    import_seeds = sub.add_parser("import-seeds")
    import_seeds.add_argument("path")
    import_seeds.add_argument("--source", default="manual")
    import_seeds.set_defaults(func=cmd_import_seeds)

    fetch_sku = sub.add_parser("fetch-sku")
    fetch_sku.add_argument("sku")
    add_browser_options(fetch_sku, include_headless=True)
    fetch_sku.set_defaults(func=cmd_fetch_sku)

    fetch_offers = sub.add_parser("fetch-offers")
    fetch_offers.add_argument("sku")
    fetch_offers.set_defaults(func=cmd_fetch_offers)

    fetch_offers_browser = sub.add_parser("fetch-offers-browser")
    fetch_offers_browser.add_argument("sku")
    add_browser_options(fetch_offers_browser, include_headless=True)
    fetch_offers_browser.set_defaults(func=cmd_fetch_offers_browser)

    fetch_seller_home = sub.add_parser("fetch-seller-home")
    fetch_seller_home.add_argument("url")
    fetch_seller_home.add_argument("--max-scrolls", type=int, default=8)
    fetch_seller_home.add_argument("--preview", type=int, default=10)
    add_browser_options(fetch_seller_home, include_headless=True)
    fetch_seller_home.set_defaults(func=cmd_fetch_seller_home)

    crawl_seller = sub.add_parser("crawl-seller")
    crawl_seller.add_argument("url")
    crawl_seller.add_argument("--max-scrolls", type=int, default=8)
    crawl_seller.add_argument("--limit", type=int, default=0)
    add_browser_options(crawl_seller, include_headless=True)
    crawl_seller.set_defaults(func=cmd_crawl_seller)

    crawl_seller_network = sub.add_parser("crawl-seller-network")
    crawl_seller_network.add_argument("url")
    crawl_seller_network.add_argument("--name", default=None)
    crawl_seller_network.add_argument("--max-depth", type=int, default=1)
    crawl_seller_network.add_argument("--max-sellers", type=int, default=20)
    crawl_seller_network.add_argument("--sku-limit", type=int, default=0)
    crawl_seller_network.add_argument("--max-scrolls", type=int, default=8)
    add_browser_options(crawl_seller_network, include_headless=True)
    crawl_seller_network.set_defaults(func=cmd_crawl_seller_network)

    crawl_top_list_network = sub.add_parser("crawl-top-list-network")
    add_top_list_options(crawl_top_list_network)
    crawl_top_list_network.add_argument("--max-depth", type=int, default=1)
    crawl_top_list_network.add_argument("--max-sellers", type=int, default=20)
    crawl_top_list_network.add_argument("--sku-limit", type=int, default=0)
    crawl_top_list_network.add_argument("--max-scrolls", type=int, default=8)
    add_browser_options(crawl_top_list_network, include_headless=True)
    crawl_top_list_network.set_defaults(func=cmd_crawl_top_list_network)

    show_browser_config = sub.add_parser("show-browser-config")
    add_browser_options(show_browser_config, include_headless=True)
    show_browser_config.set_defaults(func=cmd_show_browser_config)

    warmup_browser = sub.add_parser("warmup-browser")
    warmup_browser.add_argument("--url", default="https://accounts.google.com/")
    add_browser_options(warmup_browser)
    warmup_browser.set_defaults(func=cmd_warmup_browser)

    launch_real_chrome = sub.add_parser("launch-real-chrome")
    launch_real_chrome.add_argument("--url", default="https://accounts.google.com/")
    add_browser_options(launch_real_chrome)
    launch_real_chrome.set_defaults(func=cmd_launch_real_chrome)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except RuntimeError as exc:
        print(f"error: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
