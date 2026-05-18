from __future__ import annotations

import argparse
import csv
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from threading import Lock, local
from typing import Any

from . import db
from .browser_ozon import BrowserOzonClient
from .config import ROOT_DIR, settings
from .maozi_api import MaoziClient
from .ozon_frontend import OzonFrontendClient
from .repository import (
    finish_top_list_run,
    bulk_upsert_seller_home_skus,
    upsert_seller_home_sku,
    get_seller_shop,
    get_recent_top_list_run,
    list_due_seller_shops,
    list_seed_pool_skus,
    mark_seed_status,
    mark_seed_pool_processed,
    mark_seed_pool_selected,
    mark_seller_collected,
    parse_sku3_response,
    repair_seed_pool_offer_missing_rejections,
    repair_seed_pool_failed_statuses,
    seed_pool_sku_due_state,
    seller_recently_collected,
    start_top_list_run,
    top_list_query_key,
    upsert_seller_shop,
    upsert_seed_sku,
    upsert_seller_offer,
    upsert_sku3_response,
    upsert_seed_pool_item,
    upsert_top_list_item,
    upsert_sku_universe,
)
from .rules import TOP_LIST_SEED_RULE, evaluate_selection_rule, evaluate_top_list_prefilter

_VERBOSE = False


class ManualInterventionRequired(RuntimeError):
    pass


def set_verbose(enabled: bool) -> None:
    global _VERBOSE
    _VERBOSE = bool(enabled)


def is_verbose() -> bool:
    return _VERBOSE


def log_line(*parts: Any, prefix: str = "log") -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{prefix}]", *parts)


def vlog(*parts: Any, prefix: str = "verbose") -> None:
    if not _VERBOSE:
        return
    log_line(*parts, prefix=prefix)


def summarize_exception(exc: Exception | None) -> str:
    if exc is None:
        return ""
    text = str(exc).strip().replace("\r", " ").replace("\n", " ")
    if len(text) > 180:
        text = text[:177] + "..."
    return text


def build_retry_reason(stage: str, exc: Exception | None = None) -> str:
    detail = summarize_exception(exc)
    if detail:
        return f"待重试: {stage}获取失败，未完成最终判定; {detail}"
    return f"待重试: {stage}获取失败，未完成最终判定"


def should_defer_for_pending_refresh(metric: dict[str, Any], reasons: list[str]) -> bool:
    if not (metric.get("status_update_sales") or metric.get("status_update_variant")):
        return False
    if not reasons:
        return False
    refreshable_reasons = {
        "月销量缺失",
        "重量(g)缺失",
        "上架天数缺失",
        "退货取消率缺失",
        "发货模式不包含FBS",
        "跟卖人数缺失",
    }
    return all(reason in refreshable_reasons for reason in reasons)


def needs_manual_intervention(value: Any) -> bool:
    text = str(value or "").lower()
    markers = (
        "cloudflare challenge",
        "manual verification is required",
        "正在进行安全验证",
        "安全验证",
        "cloudflare",
    )
    return any(marker.lower() in text for marker in markers)


def cmd_open_gui(_: argparse.Namespace) -> None:
    from .gui import main

    main()


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
                    try:
                        upsert_seed_sku(str(sku).strip(), source=args.source)
                        count += 1
                    except Exception as exc:
                        print(f"  warn: failed to import seed sku {sku}: {exc}")
    else:
        for line in path.read_text(encoding="utf-8").splitlines():
            sku = line.strip()
            if sku:
                try:
                    upsert_seed_sku(sku, source=args.source)
                    count += 1
                except Exception as exc:
                    print(f"  warn: failed to import seed sku {sku}: {exc}")
    print(f"imported {count} seed skus")


def cmd_repair_seed_pool_failures(args: argparse.Namespace) -> None:
    if args.offer_missing_rejections:
        repaired = repair_seed_pool_offer_missing_rejections(
            source_type=args.source_type,
            query_key=args.query_key or None,
        )
        mode = "offer_missing_rejections"
    else:
        repaired = repair_seed_pool_failed_statuses(
            source_type=args.source_type,
            query_key=args.query_key or None,
            only_maozi_fetch_failures=not args.all_failed,
        )
        mode = "all_failed" if args.all_failed else "maozi_fetch_failures_only"
    scope = args.query_key or "<all>"
    print(
        "seed-pool failure repair:",
        f"source_type={args.source_type}",
        f"query_key={scope}",
        f"mode={mode}",
        f"repaired_rows={repaired}",
    )


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
    with browser.session():
        result = process_sku(args.sku, client, browser, source="manual_fetch")
    if result.get("transient_failed"):
        print("deferred sku:", result["sku"], result["rule_reason"])
        return
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
    browser = build_browser_client(args)
    with browser.session():
        offers = load_seller_offers(args.sku, browser)
    for offer in offers:
        try:
            upsert_seller_offer(args.sku, offer)
        except Exception as exc:
            print(f"  warn: failed to store offer for {args.sku}: {exc}")
    print(f"stored {len(offers)} seller offers for sku {args.sku}")


def cmd_fetch_seller_home(args: argparse.Namespace) -> None:
    client = build_browser_client(args)
    with client.session():
        result = load_seller_home_products(args.url, client, max_scrolls=args.max_scrolls)
    items = result.get("items") or []
    saved = 0
    for item in items:
        try:
            if upsert_seller_home_sku(args.url, item):
                saved += 1
        except Exception as exc:
            print(f"  warn: failed to store seller-home sku: {exc}")
    print(f"stored {saved} seller-home products from {args.url}")
    for item in items[: args.preview]:
        print(item.get("href"), "|", item.get("title") or "<no-title>")


def run_seller_network(
    queue: deque[dict[str, Any]],
    *,
    browser: BrowserOzonClient,
    maozi: MaoziClient,
    max_depth: int,
    max_sellers: int,
    sku_limit: int,
    max_scrolls: int,
    seller_sku_workers: int,
) -> dict[str, int]:
    max_sellers = max_sellers if max_sellers > 0 else 1000000
    unlimited_depth = max_depth < 0
    max_depth = max_depth if max_depth >= 0 else 0
    sku_limit = sku_limit if sku_limit > 0 else 0
    seller_sku_workers = normalize_worker_count(seller_sku_workers, settings.seller_sku_workers)
    if not browser.cdp_url:
        seller_sku_workers = 1
    seen_urls: set[str] = set()
    processed_sellers = 0
    skipped_recent = 0
    discovered_sellers = len(queue)
    total_skus = 0
    qualified_skus = 0
    rejected_skus = 0
    deferred_skus = 0
    stored_offer_rows = 0
    vlog(
        "run_seller_network start:",
        {
            "initial_queue": len(queue),
            "max_depth": max_depth,
            "unlimited_depth": unlimited_depth,
            "max_sellers": max_sellers,
            "sku_limit": sku_limit,
            "seller_sku_workers": seller_sku_workers,
        },
        prefix="seller",
    )

    while queue and processed_sellers < max_sellers:
        seller = queue.popleft()
        url = seller["url"]
        depth = int(seller["depth"])
        name = seller.get("name")
        normalized_url = url.rstrip("/")
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        if not unlimited_depth and depth > max_depth:
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

        crawl_start = time.perf_counter()
        try:
            result = load_seller_home_products(url, browser, max_scrolls=max_scrolls)
            crawl_elapsed = time.perf_counter() - crawl_start
            items = result.get("items") or []
            source = result.get("source") or "unknown"
            vlog(
                "seller page detail:",
                {
                    "url": url,
                    "depth": depth,
                    "source": source,
                    "items": len(items),
                    "pages_fetched": result.get("pages_fetched"),
                    "next_page": result.get("next_page"),
                },
                prefix="seller",
            )
            print(
                "crawl seller:",
                url,
                "| depth=",
                depth,
                "| source=",
                source,
                "| items=",
                len(items),
                "| crawl=",
                f"{crawl_elapsed:.1f}s",
            )
        except Exception as exc:
            detail = summarize_exception(exc)
            print(
                "skip seller (load failed):",
                url,
                "| error=",
                detail or str(exc)[:120],
            )
            vlog(
                "seller page load failed, skipping:",
                {"url": url, "error": detail or str(exc)},
                prefix="seller",
            )
            mark_seller_collected(key)
            processed_sellers += 1
            continue

        seller_offer_urls: dict[str, dict[str, Any]] = {}
        seller_skus = 0
        seller_qualified = 0
        seller_rejected = 0
        seller_deferred = 0
        seller_skipped = 0
        seller_offer_rows = 0
        selected_items = items[: sku_limit or None]
        prepared_items: list[tuple[str, dict[str, Any]]] = []
        home_rows_saved = 0
        for item in selected_items:
            try:
                sku = upsert_seller_home_sku(url, item)
            except Exception as exc:
                vlog("seller-home sku upsert failed:", f"seller={url}", f"error={exc}", prefix="seller")
                print("DEBUG: upsert_seller_home_sku failed:", exc)
                continue
            if not sku:
                print("DEBUG: upsert_seller_home_sku returned empty sku for item:", item.get("title"))
                continue
            try:
                upsert_seed_sku(sku, source=f"seller_home:{url}")
            except Exception as exc:
                vlog("seed_sku upsert failed:", f"sku={sku}", f"error={exc}", prefix="seller")
                print("DEBUG: seed_sku upsert failed:", exc)
            prepared_items.append((sku, item))
            home_rows_saved += 1
        if prepared_items:
            print(
                "seller home skus prepared:",
                f"seller={url}",
                f"raw_items={len(items)}",
                f"selected={len(selected_items)}",
                f"saved={home_rows_saved}",
            )

        seller_prefetched_maozi: dict[str, tuple[dict[str, Any], str]] = {}
        seller_batch_prefetch_failed = False
        if prepared_items and browser.cdp_url:
            try:
                prefetch_start = time.perf_counter()
                seller_prefetched_maozi = prefetch_top_list_maozi_batch(
                    [sku for sku, _ in prepared_items],
                    maozi=maozi,
                    browser=browser,
                )
                prefetch_elapsed = time.perf_counter() - prefetch_start
                print(
                    "prefetch SKU3 batch:",
                    f"seller={url}",
                    f"requested={len(prepared_items)}",
                    f"fetched={len(seller_prefetched_maozi)}",
                    f"elapsed={prefetch_elapsed:.1f}s",
                )
                vlog(
                    "seller-home batch sku3 prefetched:",
                    f"seller={url}",
                    f"requested={len(prepared_items)}",
                    f"succeeded={len(seller_prefetched_maozi)}",
                    prefix="seller",
                )
            except Exception as exc:
                seller_batch_prefetch_failed = True
                vlog("seller-home batch sku3 prefetch failed:", f"seller={url}", exc, prefix="seller")

        if prepared_items and (not browser.cdp_url or seller_batch_prefetch_failed or not seller_prefetched_maozi):
            reason = "batch sku3 unavailable"
            if not browser.cdp_url:
                reason = "batch sku3 requires CDP browser"
            elif seller_batch_prefetch_failed:
                reason = "batch sku3 request failed"
            print(
                "skip seller:",
                url,
                "| reason=",
                reason,
                "| requested=",
                len(prepared_items),
            )
            vlog(
                "seller-home batch-only skip seller:",
                {
                    "seller": url,
                    "reason": reason,
                    "requested": len(prepared_items),
                },
                prefix="seller",
            )
            mark_seller_collected(key)
            processed_sellers += 1
            continue

        if not prepared_items:
            vlog("seller-home no items found/saved, skipping completion", f"seller={url}", prefix="seller")
            mark_seller_collected(key)
            processed_sellers += 1
            continue

        def process_seller_home_item(entry: tuple[str, dict[str, Any]]) -> dict[str, Any] | None:
            sku, item = entry
            prefetched = seller_prefetched_maozi.get(sku)
            product_snapshot_override = {
                "product_url": item.get("href") or item.get("product_url"),
                "title": item.get("title"),
                "price": item.get("price_amount"),
                "currency": item.get("currency"),
                "main_image_url": item.get("image_url") or item.get("main_image_url"),
                "raw": {"seller_home": item},
            }
            if prefetched is None:
                try:
                    upsert_sku_universe(
                        sku,
                        product_data=product_snapshot_override,
                    )
                except Exception as exc:
                    vlog("sku_universe upsert failed:", f"sku={sku}", f"error={exc}", prefix="seller")
                return {
                    "sku": sku,
                    "sku_result": {
                        "qualified": False,
                        "batch_skipped": True,
                        "rule_reason": "卖家页批量 sku3 未返回，已跳过单 SKU 补抓",
                        "seller_offer_count": None,
                        "offers": [],
                        "maozi_source": "seller_batch_miss",
                    },
                }
            sku_result = process_sku(
                sku,
                maozi,
                browser,
                source=f"seller_home:{url}",
                product_snapshot_override=product_snapshot_override,
                prefetched_maozi=prefetched,
                batch_only_mode=True,
            )
            return {"sku": sku, "sku_result": sku_result}

        def consume_seller_home_result(item_result: dict[str, Any] | None) -> None:
            nonlocal seller_skus, total_skus, seller_qualified, qualified_skus
            nonlocal seller_rejected, rejected_skus, seller_deferred, deferred_skus, seller_skipped, seller_offer_rows, stored_offer_rows
            if not item_result:
                return
            sku = item_result["sku"]
            sku_result = item_result["sku_result"]
            seller_skus += 1
            total_skus += 1
            if seller_skus % 50 == 0:
                print(
                    "  progress:",
                    f"sku={sku}",
                    f"done={seller_skus}/{len(prepared_items)}",
                    f"q={seller_qualified}",
                )
            if sku_result.get("batch_skipped"):
                seller_skipped += 1
                print(
                    "  sku:",
                    sku,
                    "| skipped | offers=",
                    sku_result["seller_offer_count"],
                    "|",
                    sku_result["rule_reason"],
                )
                return
            if sku_result.get("transient_failed"):
                seller_deferred += 1
                deferred_skus += 1
                if needs_manual_intervention(sku_result["rule_reason"]):
                    raise ManualInterventionRequired(sku_result["rule_reason"])
                return
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
            if sku_result["qualified"]:
                print(
                    "  qualified sku:",
                    sku,
                    "| offers=",
                    sku_result["seller_offer_count"],
                    "|",
                    sku_result["rule_reason"],
                )

        if seller_sku_workers <= 1 or len(prepared_items) <= 1:
            for entry in prepared_items:
                consume_seller_home_result(process_seller_home_item(entry))
        else:
            with ThreadPoolExecutor(max_workers=seller_sku_workers) as executor:
                futures = [executor.submit(process_seller_home_item, entry) for entry in prepared_items]
                for future in as_completed(futures):
                    consume_seller_home_result(future.result())

        mark_seller_collected(key)
        processed_sellers += 1
        total_elapsed = time.perf_counter() - crawl_start
        print(
            "seller summary:",
            f"skus={seller_skus}",
            f"qualified={seller_qualified}",
            f"rejected={seller_rejected}",
            f"deferred={seller_deferred}",
            f"skipped={seller_skipped}",
            f"offers={seller_offer_rows}",
            f"total={total_elapsed:.1f}s",
        )

        if unlimited_depth or depth < max_depth:
            for next_seller in seller_offer_urls.values():
                next_url = next_seller["url"].rstrip("/")
                if next_url in seen_urls:
                    continue
                queue.append(next_seller)
                discovered_sellers += 1
                upsert_seller_shop(next_url, name=next_seller.get("name"))

    return {
        "processed_sellers": processed_sellers,
        "skipped_recent": skipped_recent,
        "queued_sellers": discovered_sellers,
        "total_skus": total_skus,
        "qualified_skus": qualified_skus,
        "rejected_skus": rejected_skus,
        "deferred_skus": deferred_skus,
        "skipped_skus": 0,
        "seller_offers": stored_offer_rows,
    }


def due_seed_pool_items(
    *,
    query_key: str | None = None,
    source_type: str = "top_list",
    process_limit: int = 0,
    retry_failed_now: bool = False,
    retry_deferred_now: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    query_limit = max(settings.seed_pool_query_limit, process_limit if process_limit > 0 else 0)
    cached_items = list_seed_pool_skus(query_key=query_key, source_type=source_type, limit=query_limit)
    due_items: list[dict[str, Any]] = []
    for item in cached_items:
        status = item.get("last_process_status") or "pending"
        if retry_failed_now and status == "failed":
            item["_due_reason"] = "failed_retry_forced"
            due_items.append(item)
            continue
        if retry_deferred_now and status == "deferred":
            item["_due_reason"] = "deferred_retry_forced"
            due_items.append(item)
            continue
        due, due_reason = seed_pool_sku_due_state(item)
        if due:
            item["_due_reason"] = due_reason
            due_items.append(item)
    if process_limit > 0:
        due_items = due_items[:process_limit]
    return cached_items, due_items


def print_browser_runtime_notice(browser: BrowserOzonClient) -> None:
    if browser.cdp_url:
        print(
            "browser mode:",
            f"attach_existing_cdp={browser.cdp_url}",
            "| this command will reuse your manually started browser and will not open a new window",
        )
        if is_verbose():
            try:
                vlog("browser describe:", browser.describe(), prefix="runtime")
                vlog("cdp ping:", browser.ping_cdp(), prefix="runtime")
            except Exception as exc:
                vlog("cdp ping failed during runtime notice:", exc, prefix="runtime")
        return
    print("browser mode: launch_own_context | no CDP browser configured; Playwright will launch its own context")


def preflight_seed_pool_maozi_access(
    due_items: list[dict[str, Any]],
    *,
    browser: BrowserOzonClient,
    maozi: MaoziClient,
) -> dict[str, tuple[dict[str, Any], str]]:
    if not due_items:
        return {}

    probe_sku = str(due_items[0]["sku"])
    vlog("preflight start:", f"probe_sku={probe_sku}", f"due_items={len(due_items)}", prefix="preflight")
    if browser.cdp_url:
        while True:
            try:
                browser.ping_cdp()
                response, source = load_top_list_maozi_sku3(probe_sku, maozi, browser)
                print("browser preflight:", f"sku={probe_sku}", f"maozi_source={source}", "status=ok")
                if is_verbose():
                    metric = parse_sku3_response(probe_sku, response)
                    vlog(
                        "preflight metric:",
                        {
                            "sold_count": metric.get("sold_count"),
                            "sales_schema": metric.get("sales_schema"),
                            "status_update_sales": metric.get("status_update_sales"),
                            "status_update_variant": metric.get("status_update_variant"),
                            "brand": metric.get("brand"),
                        },
                        prefix="preflight",
                    )
                return {probe_sku: (response, source)}
            except Exception as exc:
                print("browser preflight failed:", exc)
                print(
                    "manual action required: keep the current browser window open, confirm Ozon and Maozi are logged in, "
                    "then press Enter to retry. Press Ctrl+C to stop this run."
                )
                input()

    try:
        response, source = load_top_list_maozi_sku3(probe_sku, maozi, browser)
    except Exception as exc:
        raise RuntimeError(
            "seed-pool preflight failed before processing the first SKU. Please confirm the browser context can reach "
            "Ozon and Maozi, then rerun."
        ) from exc
    print("browser preflight:", f"sku={probe_sku}", f"maozi_source={source}", "status=ok")
    return {probe_sku: (response, source)}


def normalize_worker_count(value: int | None, default: int = 1) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(1, parsed)


def create_worker_resource_pool(
    base_browser: BrowserOzonClient,
    base_maozi: MaoziClient,
):
    state = local()
    created_browsers: list[BrowserOzonClient] = []
    created_lock = Lock()

    def acquire() -> tuple[BrowserOzonClient, MaoziClient]:
        browser = getattr(state, "browser", None)
        if browser is None:
            browser = base_browser.clone()
            browser.open_session()
            state.browser = browser
            vlog("worker browser opened", f"worker_state={id(state)}", prefix="worker")
            with created_lock:
                created_browsers.append(browser)
        maozi = getattr(state, "maozi", None)
        if maozi is None:
            maozi = MaoziClient(token=base_maozi.token)
            state.maozi = maozi
            vlog("worker maozi client created", f"worker_state={id(state)}", prefix="worker")
        return browser, maozi

    def close_all() -> None:
        seen: set[int] = set()
        for browser in created_browsers:
            key = id(browser)
            if key in seen:
                continue
            seen.add(key)
            try:
                browser.close_session()
                vlog("worker browser closed", f"browser_id={key}", prefix="worker")
            except Exception:
                pass

    return acquire, close_all


def expand_seed_pool_items(
    due_items: list[dict[str, Any]],
    *,
    browser: BrowserOzonClient,
    maozi: MaoziClient,
    source_type: str,
    max_depth: int,
    max_sellers: int,
    sku_limit: int,
    max_scrolls: int,
    seed_sku_workers: int,
    seller_sku_workers: int,
    prefetched_maozi: dict[str, tuple[dict[str, Any], str]] | None = None,
) -> dict[str, Any]:
    processed_skus = 0
    qualified_skus = 0
    rejected_skus = 0
    prefiltered_skus = 0
    seller_offer_rows = 0
    pending_sellers: dict[str, dict[str, Any]] = {}
    strict_seed_inserts = 0
    failed_skus = 0
    deferred_seed_skus = 0
    reused_duplicate_rows = 0
    unique_skus: set[str] = set()
    cached_results: dict[str, dict[str, Any]] = {}
    seller_stats = {
        "processed_sellers": 0,
        "skipped_recent": 0,
        "queued_sellers": 0,
        "total_skus": 0,
        "qualified_skus": 0,
        "rejected_skus": 0,
        "deferred_skus": 0,
        "seller_offers": 0,
    }
    remaining_seller_budget = max_sellers if max_sellers > 0 else -1
    prefetched_maozi = dict(prefetched_maozi or {})
    seed_sku_workers = normalize_worker_count(seed_sku_workers, settings.seed_sku_workers)
    if not browser.cdp_url:
        seed_sku_workers = 1
    vlog(
        "expand_seed_pool_items config:",
        {
            "due_rows": len(due_items),
            "source_type": source_type,
            "max_depth": max_depth,
            "max_sellers": max_sellers,
            "sku_limit": sku_limit,
            "seed_sku_workers": seed_sku_workers,
            "seller_sku_workers": seller_sku_workers,
        },
        prefix="expand",
    )

    def flush_pending_sellers() -> None:
        nonlocal remaining_seller_budget
        if not pending_sellers:
            vlog("flush_pending_sellers skipped: empty queue", prefix="expand")
            return
        if remaining_seller_budget == 0:
            vlog("flush_pending_sellers skipped: seller budget exhausted", prefix="expand")
            return
        if not (max_depth < 0 or max_depth >= 1):
            pending_sellers.clear()
            vlog("flush_pending_sellers cleared: max_depth prevented expansion", prefix="expand")
            return
        batch = list(pending_sellers.values())
        pending_sellers.clear()
        seller_stats["queued_sellers"] += len(batch)
        vlog(
            "flush_pending_sellers start:",
            f"batch={len(batch)}",
            f"remaining_budget={remaining_seller_budget}",
            prefix="expand",
        )
        batch_stats = run_seller_network(
            deque(batch),
            browser=browser,
            maozi=maozi,
            max_depth=max_depth,
            max_sellers=remaining_seller_budget,
            sku_limit=sku_limit,
            max_scrolls=max_scrolls,
            seller_sku_workers=seller_sku_workers,
        )
        for key in seller_stats:
            if key == "queued_sellers":
                continue
            seller_stats[key] += batch_stats[key]
        if remaining_seller_budget > 0:
            remaining_seller_budget = max(0, remaining_seller_budget - batch_stats["processed_sellers"])
    grouped_items: dict[str, list[dict[str, Any]]] = {}
    for item in due_items:
        sku = str(item["sku"])
        unique_skus.add(sku)
        grouped_items.setdefault(sku, []).append(item)
        mark_seed_pool_selected(str(item["query_key"]), sku, source_type=source_type)
    vlog(
        "seed pool grouped:",
        f"unique_skus={len(grouped_items)}",
        f"due_rows={len(due_items)}",
        prefix="expand",
    )

    reused_duplicate_rows = sum(max(0, len(rows) - 1) for rows in grouped_items.values())
    work_items: list[tuple[str, dict[str, Any], list[dict[str, Any]], tuple[dict[str, Any], str] | None]] = []

    for sku, rows in grouped_items.items():
        item = rows[0]
        prefilter = evaluate_top_list_prefilter(item, rule=TOP_LIST_SEED_RULE)
        vlog(
            "seed prefilter result:",
            f"sku={sku}",
            f"matched={prefilter.matched}",
            f"summary={prefilter.summary}",
            prefix="seed",
        )
        if not prefilter.matched:
            prefiltered_skus += 1
            rejected_skus += 1
            reason = f"种子预筛未命中: {prefilter.summary}"
            for row in rows:
                mark_seed_pool_processed(
                    str(row["query_key"]),
                    sku,
                    status="rejected",
                    snapshot_hash=row.get("snapshot_hash"),
                    reason=reason,
                    source_type=source_type,
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
        work_items.append((sku, item, rows, prefetched_maozi.pop(sku, None)))

    if work_items and browser.cdp_url:
        batch_targets = [sku for sku, _, _, prefetch in work_items if prefetch is None]
        if batch_targets:
            try:
                batch_prefetched = prefetch_top_list_maozi_batch(
                    batch_targets,
                    maozi=maozi,
                    browser=browser,
                )
                hydrated_work_items: list[tuple[str, dict[str, Any], list[dict[str, Any]], tuple[dict[str, Any], str] | None]] = []
                for sku, item, rows, prefetch in work_items:
                    hydrated_work_items.append((sku, item, rows, prefetch or batch_prefetched.get(sku)))
                work_items = hydrated_work_items
            except Exception as exc:
                vlog("top-list batch prefetch failed:", exc, prefix="top-list")

    worker_acquire = None
    worker_close = None
    if work_items and seed_sku_workers > 1:
        worker_acquire, worker_close = create_worker_resource_pool(browser, maozi)

    def process_seed_item(
        sku: str,
        item: dict[str, Any],
        rows: list[dict[str, Any]],
        prefetch: tuple[dict[str, Any], str] | None,
    ) -> dict[str, Any]:
        worker_browser = browser
        worker_maozi = maozi
        if worker_acquire is not None:
            worker_browser, worker_maozi = worker_acquire()
        try:
            start = datetime.now()
            vlog("seed worker start:", f"sku={sku}", f"due={item.get('_due_reason')}", prefix="seed")
            sku_result = process_top_list_sku(
                sku,
                item,
                worker_maozi,
                worker_browser,
                prefetched_maozi=prefetch,
            )
            elapsed_ms = int((datetime.now() - start).total_seconds() * 1000)
            vlog(
                "seed worker finish:",
                f"sku={sku}",
                f"qualified={sku_result.get('qualified')}",
                f"strict_qualified={sku_result.get('strict_qualified')}",
                f"offers={sku_result.get('seller_offer_count')}",
                f"maozi_source={sku_result.get('maozi_source')}",
                f"elapsed_ms={elapsed_ms}",
                prefix="seed",
            )
            return {"sku": sku, "rows": rows, "item": item, "sku_result": sku_result}
        except Exception as exc:
            vlog("seed worker exception:", f"sku={sku}", exc, prefix="seed")
            return {"sku": sku, "rows": rows, "item": item, "error": exc}

    def consume_seed_item_result(result: dict[str, Any]) -> None:
        nonlocal processed_skus, qualified_skus, rejected_skus, failed_skus, deferred_seed_skus
        nonlocal strict_seed_inserts, seller_offer_rows
        sku = result["sku"]
        rows = result["rows"]
        item = result["item"]
        if result.get("error") is not None:
            failed_skus += 1
            error = result["error"]
            reason = f"种子处理失败: {error}"
            for row in rows:
                mark_seed_pool_processed(
                    str(row["query_key"]),
                    sku,
                    status="failed",
                    snapshot_hash=row.get("snapshot_hash"),
                    reason=reason,
                    source_type=source_type,
                )
            if needs_manual_intervention(reason):
                raise ManualInterventionRequired(reason)
            print("seed sku:", sku, "| failed |", error)
            return

        sku_result = result["sku_result"]
        if sku_result.get("transient_failed"):
            deferred_seed_skus += 1
            reason = sku_result["rule_reason"]
            for row in rows:
                mark_seed_pool_processed(
                    str(row["query_key"]),
                    sku,
                    status="deferred",
                    snapshot_hash=row.get("snapshot_hash"),
                    reason=reason,
                    seller_offer_count=sku_result["seller_offer_count"] or None,
                    source_type=source_type,
                )
            if needs_manual_intervention(reason):
                raise ManualInterventionRequired(reason)
            print(
                "seed sku:",
                sku,
                "| deferred | due=",
                item.get("_due_reason"),
                "| offers=",
                sku_result["seller_offer_count"],
                "|",
                reason,
            )
            return
        processed_skus += 1
        if sku_result["qualified"]:
            qualified_skus += 1
            offers = sku_result.get("offers") or []
            seller_offer_rows += len(offers)
            for offer in offers:
                home_url = (offer.get("seller_home_url") or "").strip()
                if not home_url:
                    continue
                pending_sellers[home_url.rstrip("/")] = {
                    "url": home_url,
                    "name": offer.get("name"),
                    "depth": 1,
                }
            status = "expanded"
        else:
            rejected_skus += 1
            status = "rejected"

        if sku_result.get("strict_qualified"):
            strict_seed_inserts += 1

        for row in rows:
            mark_seed_pool_processed(
                str(row["query_key"]),
                sku,
                status=status,
                snapshot_hash=row.get("snapshot_hash"),
                reason=sku_result["rule_reason"],
                seller_offer_count=sku_result["seller_offer_count"] or None,
                source_type=source_type,
            )
        print(
            "seed sku:",
            sku,
            "|",
            status,
            "| due=",
            item.get("_due_reason"),
            "| offers=",
            sku_result["seller_offer_count"],
            "|",
            sku_result["rule_reason"],
        )

    try:
        if worker_acquire is None:
            for sku, item, rows, prefetch in work_items:
                consume_seed_item_result(process_seed_item(sku, item, rows, prefetch))
        else:
            with ThreadPoolExecutor(max_workers=seed_sku_workers) as executor:
                futures = [
                    executor.submit(process_seed_item, sku, item, rows, prefetch)
                    for sku, item, rows, prefetch in work_items
                ]
                for future in as_completed(futures):
                    consume_seed_item_result(future.result())
    finally:
        if worker_close is not None:
            worker_close()

    flush_pending_sellers()

    return {
        "due_rows": len(due_items),
        "unique_due_skus": len(unique_skus),
        "processed_skus": processed_skus,
        "qualified_skus": qualified_skus,
        "rejected_skus": rejected_skus,
        "prefiltered_skus": prefiltered_skus,
        "failed_skus": failed_skus,
        "deferred_seed_skus": deferred_seed_skus,
        "strict_seed_inserts": strict_seed_inserts,
        "seller_offer_rows": seller_offer_rows,
        "reused_duplicate_rows": reused_duplicate_rows,
        "seller_stats": seller_stats,
    }


def cmd_crawl_seller_network(args: argparse.Namespace) -> None:
    browser = build_browser_client(args)
    maozi = MaoziClient()
    with browser.session():
        stats = run_seller_network(
            deque([{"url": args.url, "depth": 0, "name": args.name or None}]),
            browser=browser,
            maozi=maozi,
            max_depth=args.max_depth,
            max_sellers=args.max_sellers,
            sku_limit=args.sku_limit,
            max_scrolls=args.max_scrolls,
            seller_sku_workers=args.seller_sku_workers,
        )
    print(
        "network crawl summary:",
        f"processed_sellers={stats['processed_sellers']}",
        f"skipped_recent={stats['skipped_recent']}",
        f"queued_sellers={stats['queued_sellers']}",
        f"total_skus={stats['total_skus']}",
        f"qualified_skus={stats['qualified_skus']}",
        f"rejected_skus={stats['rejected_skus']}",
        f"deferred_skus={stats['deferred_skus']}",
        f"seller_offers={stats['seller_offers']}",
    )


def cmd_expand_seed_pool_network(args: argparse.Namespace) -> None:
    browser = build_browser_client(args)
    maozi = MaoziClient()
    print_browser_runtime_notice(browser)
    retry_failed_now = args.retry_failed_now
    retry_deferred_now = args.retry_deferred_now
    while True:
        cached_items, due_items = due_seed_pool_items(
            query_key=args.query_key or None,
            source_type=args.source_type,
            process_limit=args.process_limit,
            retry_failed_now=retry_failed_now,
            retry_deferred_now=retry_deferred_now,
        )
        vlog(
            "due items preview:",
            [f"{item['sku']}:{item.get('_due_reason')}" for item in due_items[:10]],
            prefix="expand",
        )
        print(
            "seed-pool summary:",
            f"source_type={args.source_type}",
            f"query_key={args.query_key or '<all>'}",
            f"cached_rows={len(cached_items)}",
            f"due_rows={len(due_items)}",
        )
        try:
            with browser.session():
                prefetched_maozi = preflight_seed_pool_maozi_access(due_items, browser=browser, maozi=maozi)
                stats = expand_seed_pool_items(
                    due_items,
                    browser=browser,
                    maozi=maozi,
                    source_type=args.source_type,
                    max_depth=args.max_depth,
                    max_sellers=args.max_sellers,
                    sku_limit=args.sku_limit,
                    max_scrolls=args.max_scrolls,
                    seed_sku_workers=args.seed_sku_workers,
                    seller_sku_workers=args.seller_sku_workers,
                    prefetched_maozi=prefetched_maozi,
                )
            break
        except ManualInterventionRequired as exc:
            log_line("manual action required:", exc, prefix="manual")
            log_line(
                "the browser window will stay open; complete the challenge to continue.",
                prefix="manual",
            )
            import sys
            if not sys.stdin.isatty():
                time.sleep(30)
            else:
                log_line("press Enter in console to continue...", prefix="manual")
                try:
                    input()
                except EOFError:
                    time.sleep(30)
            retry_failed_now = True
    seller_stats = stats["seller_stats"]
    print(
        "seed-pool expansion summary:",
        f"due_rows={stats['due_rows']}",
        f"unique_due_skus={stats['unique_due_skus']}",
        f"prefiltered_skus={stats['prefiltered_skus']}",
        f"processed_skus={stats['processed_skus']}",
        f"expanded_seed_skus={stats['qualified_skus']}",
        f"rejected_skus={stats['rejected_skus']}",
        f"failed_skus={stats['failed_skus']}",
        f"deferred_seed_skus={stats['deferred_seed_skus']}",
        f"strict_seed_inserts={stats['strict_seed_inserts']}",
        f"seed_offer_rows={stats['seller_offer_rows']}",
        f"reused_duplicate_rows={stats['reused_duplicate_rows']}",
        f"expanded_sellers={seller_stats['processed_sellers']}",
        f"skipped_recent_sellers={seller_stats['skipped_recent']}",
        f"expanded_seller_skus={seller_stats['total_skus']}",
        f"expanded_seller_qualified_skus={seller_stats['qualified_skus']}",
        f"expanded_seller_rejected_skus={seller_stats['rejected_skus']}",
        f"expanded_seller_offer_rows={seller_stats['seller_offers']}",
    )


def cmd_expand_seller_backlog(args: argparse.Namespace) -> None:
    browser = build_browser_client(args)
    maozi = MaoziClient()
    print_browser_runtime_notice(browser)

    total_processed = 0
    total_queued = 0
    total_skus = 0
    total_qualified = 0
    total_rejected = 0
    total_deferred = 0
    total_offers = 0
    round_no = 0

    while True:
        due_sellers = list_due_seller_shops(process_limit=args.process_limit)
        vlog(
            "seller backlog preview:",
            [f"{item.get('seller_key')}:{item.get('home_url')}" for item in due_sellers[:10]],
            prefix="seller-backlog",
        )
        print(
            f"expand-network round {round_no + 1} due:",
            f"fetched={len(due_sellers)}",
            f"process_limit={args.process_limit}",
        )
        if not due_sellers:
            print("expand-network complete: no more due seller pages to process")
            break
        queue = deque(
            {
                "url": str(item.get("home_url") or "").strip(),
                "name": item.get("name"),
                "depth": 0,
            }
            for item in due_sellers
            if str(item.get("home_url") or "").strip()
        )
        if not queue:
            print("expand-network empty after filtering invalid home_url rows")
            break
        try:
            with browser.session():
                stats = run_seller_network(
                    queue,
                    browser=browser,
                    maozi=maozi,
                    max_depth=args.max_depth,
                    max_sellers=args.max_sellers,
                    sku_limit=args.sku_limit,
                    max_scrolls=args.max_scrolls,
                    seller_sku_workers=args.seller_sku_workers,
                )
        except ManualInterventionRequired as exc:
            log_line("manual action required:", exc, prefix="manual")
            log_line(
                "the browser window will stay open; complete the challenge to continue.",
                prefix="manual",
            )
            # In GUI/non-TTY mode, input() would hang forever. 
            # We wait for a bit and then retry instead of blocking on stdin.
            import sys
            if not sys.stdin.isatty():
                time.sleep(30)
            else:
                log_line("press Enter in console to continue...", prefix="manual")
                try:
                    input()
                except EOFError:
                    time.sleep(30)
            continue

        round_no += 1
        total_processed += stats["processed_sellers"]
        total_queued += stats["queued_sellers"]
        total_skus += stats["total_skus"]
        total_qualified += stats["qualified_skus"]
        total_rejected += stats["rejected_skus"]
        total_deferred += stats["deferred_skus"]
        total_offers += stats["seller_offers"]

        print(
            f"expand-network round {round_no} done:",
            f"sellers={stats['processed_sellers']}",
            f"queued={stats['queued_sellers']}",
            f"skus={stats['total_skus']}",
            f"qualified={stats['qualified_skus']}",
            f"rejected={stats['rejected_skus']}",
            f"deferred={stats['deferred_skus']}",
            f"offers={stats['seller_offers']}",
        )

        if stats.get("queued_sellers", 0) == 0 and stats.get("processed_sellers", 0) == 0:
            print("expand-network: no new sellers discovered in this round, stopping")
            break

    print(
        "expand-network final summary:",
        f"rounds={round_no}",
        f"total_sellers_processed={total_processed}",
        f"total_sellers_queued={total_queued}",
        f"total_skus={total_skus}",
        f"total_qualified={total_qualified}",
        f"total_rejected={total_rejected}",
        f"total_deferred={total_deferred}",
        f"total_offers={total_offers}",
    )


def cmd_crawl_top_list_network(args: argparse.Namespace) -> None:
    browser = build_browser_client(args)
    maozi = MaoziClient()
    print_browser_runtime_notice(browser)
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
        with browser.session():
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

            cached_items, due_items = due_seed_pool_items(
                query_key=query_key,
                source_type="top_list",
                process_limit=args.process_limit,
                retry_failed_now=args.retry_failed_now,
                retry_deferred_now=args.retry_deferred_now,
            )

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

            expansion_stats = expand_seed_pool_items(
                due_items,
                browser=browser,
                maozi=maozi,
                source_type="top_list",
                max_depth=args.max_depth,
                max_sellers=args.max_sellers,
                sku_limit=args.sku_limit,
                max_scrolls=args.max_scrolls,
                seed_sku_workers=args.seed_sku_workers,
                seller_sku_workers=args.seller_sku_workers,
                prefetched_maozi=preflight_seed_pool_maozi_access(due_items, browser=browser, maozi=maozi),
            )
            seller_stats = expansion_stats["seller_stats"]

            finish_top_list_run(
                run_id,
                status="success",
                pages_fetched=pages_fetched,
                items_fetched=items_fetched,
                due_skus=len(due_items),
                processed_skus=expansion_stats["processed_skus"] + expansion_stats["prefiltered_skus"],
                qualified_skus=expansion_stats["qualified_skus"],
                rejected_skus=expansion_stats["rejected_skus"],
                seller_expansions=seller_stats["processed_sellers"],
            )
            print(
                "top-list crawl summary:",
                f"seed_pool_skus={len(cached_items)}",
                f"due_skus={len(due_items)}",
                f"prefiltered_skus={expansion_stats['prefiltered_skus']}",
                f"processed_skus={expansion_stats['processed_skus']}",
                f"expanded_seed_skus={expansion_stats['qualified_skus']}",
                f"rejected_skus={expansion_stats['rejected_skus']}",
                f"failed_skus={expansion_stats['failed_skus']}",
                f"deferred_seed_skus={expansion_stats['deferred_seed_skus']}",
                f"strict_seed_inserts={expansion_stats['strict_seed_inserts']}",
                f"top_list_offer_rows={expansion_stats['seller_offer_rows']}",
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
    parser.add_argument("--retry-failed-now", action="store_true")


def load_seller_offers(sku: str, browser: BrowserOzonClient) -> list[dict[str, Any]]:
    # Ozon entrypoint API requires browser cookies/fingerprint; pure requests always returns 403.
    return browser.seller_offers(sku)


def load_seller_home_products(seller_url: str, browser: BrowserOzonClient, *, max_scrolls: int = 8) -> dict[str, Any]:
    # Page-injected fetch from within CDP browser (has auth context) is the only reliable path.
    # Pure requests (OzonFrontendClient) always returns 403 without browser cookies/fingerprint.
    return browser.seller_home_products(seller_url, max_scrolls=max_scrolls)


def load_product_snapshot(sku: str, browser: BrowserOzonClient) -> dict[str, Any]:
    return browser.product_snapshot(sku)


def load_maozi_sku3(sku: str, maozi: MaoziClient, browser: BrowserOzonClient, *, prefer_direct: bool) -> tuple[dict[str, Any], str]:
    # SKU3 API requires the extension token from chrome.storage.local, only available inside the CDP browser.
    # The pure-requests MaoziClient does not carry this token; browser extension page injection is the reliable path.
    try:
        return browser.maozi_sku3(sku), "extension_page"
    except Exception as browser_exc:
        raise RuntimeError(
            f"browser extension sku3 failed: {summarize_exception(browser_exc)}"
        ) from browser_exc


def load_top_list_maozi_sku3(sku: str, maozi: MaoziClient, browser: BrowserOzonClient) -> tuple[dict[str, Any], str]:
    # SKU3 requires extension token from chrome.storage.local; only CDP browser page injection works.
    try:
        return browser.top_list_sku3(sku), "top_list_batch"
    except Exception as site_exc:
        try:
            return browser.maozi_sku3(sku), "extension_page"
        except Exception as browser_exc:
            raise RuntimeError(
                f"top-list batch failed: {summarize_exception(site_exc)}; "
                f"browser extension failed: {summarize_exception(browser_exc)}"
            ) from browser_exc


def prefetch_top_list_maozi_batch(
    skus: list[str],
    *,
    maozi: MaoziClient | None = None,
    browser: BrowserOzonClient,
    batch_size: int | None = None,
    concurrency: int | None = None,
) -> dict[str, tuple[dict[str, Any], str]]:
    if not skus:
        return {}
    resolved_batch_size = max(1, int(batch_size or settings.top_list_sku3_batch_size))
    resolved_concurrency = max(1, int(concurrency or settings.top_list_sku3_batch_concurrency))
    prefetched: dict[str, tuple[dict[str, Any], str]] = {}
    for index in range(0, len(skus), resolved_batch_size):
        chunk = skus[index : index + resolved_batch_size]
        
        max_retries = 3
        retry_delay_seconds = 0.5
        raw = {}
        for retry_idx in range(max_retries + 1):
            start = datetime.now()
            try:
                raw = browser.top_list_sku3_batch(chunk, concurrency=resolved_concurrency)
                source = "top_list_batch"
                elapsed_ms = int((datetime.now() - start).total_seconds() * 1000)
                vlog(
                    "top-list batch sku3:",
                    f"chunk_start={index}",
                    f"chunk_size={len(chunk)}",
                    f"succeeded={len(raw)}",
                    f"missing={len(chunk) - len(raw)}",
                    f"concurrency={resolved_concurrency}",
                    f"elapsed_ms={elapsed_ms}",
                    f"retry_used={retry_idx}",
                    prefix="top-list",
                )
                break
            except Exception as exc:
                elapsed_ms = int((datetime.now() - start).total_seconds() * 1000)
                if retry_idx < max_retries:
                    vlog(
                        "top-list batch sku3 retryable error:",
                        f"chunk_start={index}",
                        f"chunk_size={len(chunk)}",
                        f"retry={retry_idx + 1}/{max_retries}",
                        f"error={summarize_exception(exc)}",
                        f"elapsed_ms={elapsed_ms}",
                        prefix="top-list",
                    )
                    time.sleep(retry_delay_seconds)
                else:
                    if maozi is not None:
                        vlog(
                            "top-list direct batch failed, falling back to browser:",
                            f"chunk_start={index}",
                            f"error={exc}",
                            f"elapsed_ms={elapsed_ms}",
                            prefix="top-list",
                        )
                        raw = browser.top_list_sku3_batch(chunk, concurrency=resolved_concurrency)
                        source = "top_list_batch"
                    else:
                        vlog(
                            "top-list batch sku3 failed permanently:",
                            f"chunk_start={index}",
                            f"error={exc}",
                            f"elapsed_ms={elapsed_ms}",
                            prefix="top-list",
                        )
                        raise

        for chunk_sku, response in raw.items():
            prefetched[str(chunk_sku)] = (response, source)
        
        next_chunk_start = index + resolved_batch_size
        if next_chunk_start < len(skus) and settings.top_list_sku3_batch_chunk_delay_ms > 0:
            delay_seconds = settings.top_list_sku3_batch_chunk_delay_ms / 1000.0
            vlog(
                "top-list batch sku3 chunk delay:",
                f"delay_ms={settings.top_list_sku3_batch_chunk_delay_ms}",
                f"next_chunk_start={next_chunk_start}",
                prefix="top-list",
            )
            time.sleep(delay_seconds)
    return prefetched


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
    prefetched_maozi: tuple[dict[str, Any], str] | None = None,
) -> dict[str, Any]:
    vlog("process_top_list_sku start:", f"sku={sku}", f"prefetched={prefetched_maozi is not None}", prefix="top-list")
    if prefetched_maozi is None:
        try:
            response, maozi_source = load_top_list_maozi_sku3(sku, maozi, browser)
        except Exception as exc:
            detail = summarize_exception(exc)
            if detail:
                raise RuntimeError(f"failed to fetch maozi sku3 for top-list sku {sku}: {detail}") from exc
            raise RuntimeError(f"failed to fetch maozi sku3 for top-list sku {sku}") from exc
    else:
        response, maozi_source = prefetched_maozi

    product_snapshot = top_list_product_snapshot(top_item)
    metric_overrides = top_list_metric_overrides(top_item)
    metric_preview = parse_sku3_response(sku, response)
    for key, value in metric_overrides.items():
        if value is not None and value != "":
            metric_preview[key] = value
    vlog(
        "top-list maozi parsed:",
        {
            "sku": sku,
            "maozi_source": maozi_source,
            "sold_count": metric_preview.get("sold_count"),
            "brand": metric_preview.get("brand"),
            "sales_schema": metric_preview.get("sales_schema"),
            "status_update_sales": metric_preview.get("status_update_sales"),
            "status_update_variant": metric_preview.get("status_update_variant"),
        },
        prefix="top-list",
    )

    preview_rule = evaluate_selection_rule(metric_preview, product_snapshot, None, rule=TOP_LIST_SEED_RULE)
    non_offer_reasons = [reason for reason in preview_rule.reasons if reason != "跟卖人数缺失"]
    vlog(
        "top-list preview rule:",
        f"sku={sku}",
        f"matched={preview_rule.matched}",
        f"summary={preview_rule.summary}",
        f"non_offer_reasons={non_offer_reasons}",
        prefix="top-list",
    )
    plugin_card = None
    plugin_rescuable_reasons = {
        "月销量缺失",
        "重量(g)缺失",
        "退货取消率缺失",
        "发货模式不包含FBS",
    }
    # Seed expansion prioritizes throughput: we store the incomplete SKU in sku_universe first
    # and defer slow plugin-card rescue to later targeted backfill / strict-flow checks.
    should_try_plugin_rescue = False
    if should_try_plugin_rescue:
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
    offer_fetch_error: Exception | None = None
    if not non_offer_reasons:
        try:
            offers = load_seller_offers(sku, browser)
            seller_offer_count = len(offers)
            vlog("top-list offers fetched:", f"sku={sku}", f"offers={seller_offer_count}", prefix="top-list")
            preview_rule = evaluate_selection_rule(
                metric_preview,
                product_snapshot,
                seller_offer_count,
                rule=TOP_LIST_SEED_RULE,
            )
        except Exception as exc:
            offers = None
            offer_fetch_error = exc
            vlog("top-list offers fetch failed:", f"sku={sku}", exc, prefix="top-list")

    if seller_offer_count is None and not non_offer_reasons:
        try:
            if not plugin_card or (
                not plugin_card.get("metric_overrides")
                and plugin_card.get("seller_offer_count") is None
            ):
                plugin_card = browser.plugin_card_snapshot(sku)
            for key, value in (plugin_card.get("metric_overrides") or {}).items():
                if value is not None and value != "":
                    metric_preview[key] = value
            seller_offer_count = plugin_card.get("seller_offer_count")
            vlog("top-list plugin-card rescue:", f"sku={sku}", f"seller_offer_count={seller_offer_count}", prefix="top-list")
            preview_rule = evaluate_selection_rule(
                metric_preview,
                product_snapshot,
                seller_offer_count,
                rule=TOP_LIST_SEED_RULE,
            )
        except Exception as exc:
            if offer_fetch_error is None:
                offer_fetch_error = exc

    strict_rule = evaluate_selection_rule(metric_preview, product_snapshot, seller_offer_count)
    defer_pending_refresh = should_defer_for_pending_refresh(metric_preview, preview_rule.reasons)
    vlog(
        "top-list final preview:",
        {
            "sku": sku,
            "preview_summary": preview_rule.summary,
            "strict_summary": strict_rule.summary,
            "seller_offer_count": seller_offer_count,
            "defer_pending_refresh": defer_pending_refresh,
        },
        prefix="top-list",
    )
    universe_formal_result = strict_rule
    universe_seed_result = preview_rule
    if (seller_offer_count is None and not non_offer_reasons) or defer_pending_refresh:
        universe_formal_result = None
        universe_seed_result = None
    try:
        upsert_sku_universe(
            sku,
            product_data=product_snapshot,
            metric=metric_preview,
            plugin_card=plugin_card,
            offers=offers,
            seller_offer_count=seller_offer_count,
            formal_rule_result=universe_formal_result,
            seed_rule_result=universe_seed_result,
        )
    except Exception as exc:
        vlog("sku_universe upsert failed:", f"sku={sku}", f"error={exc}", prefix="seed")
    if seller_offer_count is None and not non_offer_reasons:
        reason = build_retry_reason("跟卖列表", offer_fetch_error)
        mark_seed_status(sku, status="failed", reason=reason)
        return {
            "sku": sku,
            "qualified": False,
            "strict_qualified": False,
            "transient_failed": True,
            "rule_reason": reason,
            "status_update_sales": metric_preview["status_update_sales"],
            "status_update_variant": metric_preview["status_update_variant"],
            "seller_offer_count": None,
            "maozi_source": maozi_source,
            "offers": [],
        }
    if defer_pending_refresh:
        reason = "待重试: 毛子 sku3 返回待刷新状态(update_sales/update_variant)，关键字段尚未补齐"
        mark_seed_status(sku, status="failed", reason=reason)
        return {
            "sku": sku,
            "qualified": False,
            "strict_qualified": False,
            "transient_failed": True,
            "rule_reason": reason,
            "status_update_sales": metric_preview["status_update_sales"],
            "status_update_variant": metric_preview["status_update_variant"],
            "seller_offer_count": seller_offer_count,
            "maozi_source": maozi_source,
            "offers": offers or [],
        }
    if preview_rule.matched and offers is not None:
        for offer in offers:
            try:
                upsert_seller_offer(sku, offer)
            except Exception as exc:
                vlog("seller_offer upsert failed:", f"sku={sku}", f"error={exc}", prefix="seed")
    strict_metric = upsert_sku3_response(
        sku,
        response,
        product_data=product_snapshot,
        seller_offer_count=seller_offer_count,
        metric_overrides=metric_overrides,
        plugin_card=plugin_card,
        apply_selection_rule=True,
    )
    return {
        "sku": sku,
        "qualified": preview_rule.matched,
        "strict_qualified": bool(strict_metric.get("qualified")),
        "rule_reason": preview_rule.summary,
        "status_update_sales": metric_preview["status_update_sales"],
        "status_update_variant": metric_preview["status_update_variant"],
        "seller_offer_count": seller_offer_count or 0,
        "maozi_source": maozi_source,
        "offers": offers or [],
    }


def process_sku(
    sku: str,
    maozi: MaoziClient,
    browser: BrowserOzonClient,
    source: str,
    product_snapshot_override: dict[str, Any] | None = None,
    prefetched_maozi: tuple[dict[str, Any], str] | None = None,
    batch_only_mode: bool = False,
) -> dict[str, Any]:
    try:
        upsert_seed_sku(sku, source=source)
    except Exception as exc:
        vlog("seed_sku upsert failed:", f"sku={sku}", f"error={exc}", prefix="sku")
    vlog("process_sku start:", f"sku={sku}", f"source={source}", f"batch_only={batch_only_mode}", prefix="sku")
    if prefetched_maozi is not None:
        response, maozi_source = prefetched_maozi
    else:
        try:
            response, maozi_source = load_maozi_sku3(sku, maozi, browser, prefer_direct=True)
        except Exception as exc:
            raise RuntimeError(
                f"failed to fetch maozi sku3 for sku {sku}; {summarize_exception(exc)}"
            ) from exc
    vlog("sku maozi source:", f"sku={sku}", f"maozi_source={maozi_source}", prefix="sku")
    product_snapshot = product_snapshot_override or load_product_snapshot(sku, browser)
    plugin_card: dict[str, Any] = {
        "metric_overrides": {},
        "seller_offer_count": None,
        "line_map": None,
        "card_lines": None,
    }
    offers: list[dict[str, Any]] | None = None
    metric_preview = parse_sku3_response(sku, response)
    seller_offer_count = None
    preview_rule = evaluate_selection_rule(metric_preview, product_snapshot, seller_offer_count)
    non_offer_reasons = [reason for reason in preview_rule.reasons if reason != "跟卖人数缺失"]
    vlog(
        "sku preview parsed:",
        {
            "sku": sku,
            "sold_count": metric_preview.get("sold_count"),
            "brand": metric_preview.get("brand"),
            "sales_schema": metric_preview.get("sales_schema"),
            "status_update_sales": metric_preview.get("status_update_sales"),
            "status_update_variant": metric_preview.get("status_update_variant"),
            "preview_summary": preview_rule.summary,
            "non_offer_reasons": non_offer_reasons,
        },
        prefix="sku",
    )
    offer_fetch_error: Exception | None = None
    plugin_rescuable_reasons = {
        "月销量缺失",
        "重量(g)缺失",
        "上架天数缺失",
        "退货取消率缺失",
        "发货模式不包含FBS",
    }
    should_try_plugin_rescue = (
        not batch_only_mode
        and
        bool(non_offer_reasons)
        and all(reason in plugin_rescuable_reasons for reason in non_offer_reasons)
    )
    if should_try_plugin_rescue:
        try:
            plugin_card = browser.plugin_card_snapshot(sku)
            for key, value in (plugin_card.get("metric_overrides") or {}).items():
                if value is not None and value != "":
                    metric_preview[key] = value
            seller_offer_count = plugin_card.get("seller_offer_count")
            preview_rule = evaluate_selection_rule(metric_preview, product_snapshot, seller_offer_count)
            non_offer_reasons = [reason for reason in preview_rule.reasons if reason != "跟卖人数缺失"]
            vlog(
                "sku plugin rescue success:",
                f"sku={sku}",
                f"seller_offer_count={seller_offer_count}",
                f"preview_summary={preview_rule.summary}",
                prefix="sku",
            )
        except Exception:
            plugin_card = {
                "metric_overrides": {},
                "seller_offer_count": None,
                "line_map": None,
                "card_lines": None,
            }
            seller_offer_count = None
            preview_rule = evaluate_selection_rule(metric_preview, product_snapshot, seller_offer_count)
            non_offer_reasons = [reason for reason in preview_rule.reasons if reason != "跟卖人数缺失"]
            vlog("sku plugin rescue failed:", f"sku={sku}", prefix="sku")

    raw_product = dict(product_snapshot.get("raw") or {})
    if plugin_card.get("line_map") or plugin_card.get("card_lines"):
        raw_product["plugin_card"] = {
            "line_map": plugin_card.get("line_map"),
            "card_lines": plugin_card.get("card_lines"),
        }
    product_snapshot["raw"] = raw_product

    if seller_offer_count is None:
        if not non_offer_reasons:
            if batch_only_mode:
                # In batch mode, don't make browser calls per SKU for offers.
                # The SKU3 data alone is sufficient for initial qualification.
                vlog("sku batch-only skipped offers:", f"sku={sku}", prefix="sku")
            else:
                try:
                    offers = load_seller_offers(sku, browser)
                    seller_offer_count = len(offers)
                    preview_rule = evaluate_selection_rule(metric_preview, product_snapshot, seller_offer_count)
                    vlog("sku offers fetched:", f"sku={sku}", f"offers={seller_offer_count}", prefix="sku")
                except Exception as exc:
                    offers = None
                    offer_fetch_error = exc
                    vlog("sku offers fetch failed:", f"sku={sku}", exc, prefix="sku")

    if seller_offer_count is None and not non_offer_reasons:
        if batch_only_mode:
            vlog("sku batch-only mode skipped plugin offers-count fallback:", f"sku={sku}", prefix="sku")
        else:
            try:
                if not plugin_card.get("line_map") and not plugin_card.get("card_lines") and not plugin_card.get("metric_overrides"):
                    plugin_card = browser.plugin_card_snapshot(sku)
                for key, value in (plugin_card.get("metric_overrides") or {}).items():
                    if value is not None and value != "":
                        metric_preview[key] = value
                seller_offer_count = plugin_card.get("seller_offer_count")
                preview_rule = evaluate_selection_rule(metric_preview, product_snapshot, seller_offer_count)
                vlog(
                    "sku plugin fallback offers count:",
                    f"sku={sku}",
                    f"seller_offer_count={seller_offer_count}",
                    f"preview_summary={preview_rule.summary}",
                    prefix="sku",
                )
            except Exception as exc:
                if offer_fetch_error is None:
                    offer_fetch_error = exc
                vlog("sku plugin fallback failed:", f"sku={sku}", exc, prefix="sku")

    defer_pending_refresh = should_defer_for_pending_refresh(metric_preview, preview_rule.reasons)
    vlog(
        "sku final preview:",
        {
            "sku": sku,
            "preview_summary": preview_rule.summary,
            "seller_offer_count": seller_offer_count,
            "defer_pending_refresh": defer_pending_refresh,
        },
        prefix="sku",
    )
    try:
        upsert_sku_universe(
            sku,
            product_data=product_snapshot,
            metric=metric_preview,
            plugin_card=plugin_card,
            offers=offers,
            seller_offer_count=seller_offer_count,
            formal_rule_result=None if ((seller_offer_count is None and not non_offer_reasons) or defer_pending_refresh) else preview_rule,
        )
    except Exception as exc:
        vlog("sku_universe upsert failed:", f"sku={sku}", f"error={exc}", prefix="sku")
    if seller_offer_count is None and not non_offer_reasons:
        if batch_only_mode:
            vlog("sku batch-only finalizing without offers:", f"sku={sku}", prefix="sku")
        else:
            reason = build_retry_reason("跟卖列表", offer_fetch_error)
            mark_seed_status(sku, status="failed", reason=reason)
            return {
                "sku": sku,
                "qualified": False,
                "strict_qualified": False,
                "transient_failed": True,
                "rule_reason": reason,
                "status_update_sales": metric_preview["status_update_sales"],
                "status_update_variant": metric_preview["status_update_variant"],
                "seller_offer_count": None,
                "maozi_source": maozi_source,
                "offers": [],
            }
    if defer_pending_refresh:
        reason = "待重试: 毛子 sku3 返回待刷新状态(update_sales/update_variant)，关键字段尚未补齐"
        mark_seed_status(sku, status="failed", reason=reason)
        return {
            "sku": sku,
            "qualified": False,
            "strict_qualified": False,
            "transient_failed": True,
            "rule_reason": reason,
            "status_update_sales": metric_preview["status_update_sales"],
            "status_update_variant": metric_preview["status_update_variant"],
            "seller_offer_count": seller_offer_count,
            "maozi_source": maozi_source,
            "offers": offers or [],
        }
    if not preview_rule.matched:
        metric = upsert_sku3_response(
            sku,
            response,
            product_data=product_snapshot,
            seller_offer_count=seller_offer_count,
            metric_overrides=plugin_card.get("metric_overrides"),
            plugin_card=plugin_card,
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

    try:
        upsert_sku_universe(
            sku,
            product_data=product_snapshot,
            metric=metric_preview,
            plugin_card=plugin_card,
            offers=offers,
            seller_offer_count=seller_offer_count,
            formal_rule_result=preview_rule,
        )
    except Exception as exc:
        vlog("sku_universe upsert failed:", f"sku={sku}", f"error={exc}", prefix="sku")

    metric = upsert_sku3_response(
        sku,
        response,
        product_data=product_snapshot,
        seller_offer_count=seller_offer_count,
        metric_overrides=plugin_card.get("metric_overrides"),
        plugin_card=plugin_card,
        apply_selection_rule=True,
    )
    if metric.get("qualified") and offers is not None:
        for offer in offers:
            try:
                upsert_seller_offer(sku, offer)
            except Exception as exc:
                vlog("seller_offer upsert failed:", f"sku={sku}", f"error={exc}", prefix="sku")
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
    parser.add_argument("--verbose", action="store_true", help="print detailed timestamped runtime logs")
    sub = parser.add_subparsers(required=True)

    migrate = sub.add_parser("migrate")
    migrate.set_defaults(func=cmd_migrate)

    import_seeds = sub.add_parser("import-seeds")
    import_seeds.add_argument("path")
    import_seeds.add_argument("--source", default="manual")
    import_seeds.set_defaults(func=cmd_import_seeds)

    repair_seed_pool_failures = sub.add_parser("repair-seed-pool-failures")
    repair_seed_pool_failures.add_argument("--query-key", default="")
    repair_seed_pool_failures.add_argument("--source-type", default="top_list")
    repair_seed_pool_failures.add_argument("--all-failed", action="store_true")
    repair_seed_pool_failures.add_argument("--offer-missing-rejections", action="store_true")
    repair_seed_pool_failures.set_defaults(func=cmd_repair_seed_pool_failures)

    fetch_sku = sub.add_parser("fetch-sku")
    fetch_sku.add_argument("sku")
    add_browser_options(fetch_sku, include_headless=True)
    fetch_sku.set_defaults(func=cmd_fetch_sku)

    fetch_offers = sub.add_parser("fetch-offers")
    fetch_offers.add_argument("sku")
    add_browser_options(fetch_offers, include_headless=True)
    fetch_offers.set_defaults(func=cmd_fetch_offers)

    fetch_seller_home = sub.add_parser("fetch-seller-home")
    fetch_seller_home.add_argument("url")
    fetch_seller_home.add_argument("--max-scrolls", type=int, default=8)
    fetch_seller_home.add_argument("--preview", type=int, default=10)
    add_browser_options(fetch_seller_home, include_headless=True)
    fetch_seller_home.set_defaults(func=cmd_fetch_seller_home)

    crawl_seller_network = sub.add_parser("crawl-seller-network")
    crawl_seller_network.add_argument("url")
    crawl_seller_network.add_argument("--name", default=None)
    crawl_seller_network.add_argument("--max-depth", type=int, default=1)
    crawl_seller_network.add_argument("--max-sellers", type=int, default=20)
    crawl_seller_network.add_argument("--sku-limit", type=int, default=0)
    crawl_seller_network.add_argument("--max-scrolls", type=int, default=8)
    crawl_seller_network.add_argument("--seller-sku-workers", type=int, default=settings.seller_sku_workers)
    add_browser_options(crawl_seller_network, include_headless=True)
    crawl_seller_network.set_defaults(func=cmd_crawl_seller_network)

    expand_seller_backlog = sub.add_parser("expand-seller-backlog")
    expand_seller_backlog.add_argument("--process-limit", type=int, default=100)
    expand_seller_backlog.add_argument("--max-depth", type=int, default=-1)
    expand_seller_backlog.add_argument("--max-sellers", type=int, default=0)
    expand_seller_backlog.add_argument("--sku-limit", type=int, default=0)
    expand_seller_backlog.add_argument("--max-scrolls", type=int, default=8)
    expand_seller_backlog.add_argument("--seller-sku-workers", type=int, default=settings.seller_sku_workers)
    add_browser_options(expand_seller_backlog, include_headless=True)
    expand_seller_backlog.set_defaults(func=cmd_expand_seller_backlog)

    crawl_top_list_network = sub.add_parser("crawl-top-list-network")
    add_top_list_options(crawl_top_list_network)
    crawl_top_list_network.add_argument("--max-depth", type=int, default=1)
    crawl_top_list_network.add_argument("--max-sellers", type=int, default=20)
    crawl_top_list_network.add_argument("--sku-limit", type=int, default=0)
    crawl_top_list_network.add_argument("--max-scrolls", type=int, default=8)
    crawl_top_list_network.add_argument("--seed-sku-workers", type=int, default=settings.seed_sku_workers)
    crawl_top_list_network.add_argument("--seller-sku-workers", type=int, default=settings.seller_sku_workers)
    crawl_top_list_network.add_argument("--retry-deferred-now", action="store_true")
    add_browser_options(crawl_top_list_network, include_headless=True)
    crawl_top_list_network.set_defaults(func=cmd_crawl_top_list_network)

    expand_seed_pool_network = sub.add_parser("expand-seed-pool-network")
    expand_seed_pool_network.add_argument("--query-key", default="")
    expand_seed_pool_network.add_argument("--source-type", default="top_list")
    expand_seed_pool_network.add_argument("--process-limit", type=int, default=0)
    expand_seed_pool_network.add_argument("--retry-failed-now", action="store_true")
    expand_seed_pool_network.add_argument("--retry-deferred-now", action="store_true")
    expand_seed_pool_network.add_argument("--max-depth", type=int, default=-1)
    expand_seed_pool_network.add_argument("--max-sellers", type=int, default=0)
    expand_seed_pool_network.add_argument("--sku-limit", type=int, default=0)
    expand_seed_pool_network.add_argument("--max-scrolls", type=int, default=8)
    expand_seed_pool_network.add_argument("--seed-sku-workers", type=int, default=settings.seed_sku_workers)
    expand_seed_pool_network.add_argument("--seller-sku-workers", type=int, default=settings.seller_sku_workers)
    add_browser_options(expand_seed_pool_network, include_headless=True)
    expand_seed_pool_network.set_defaults(func=cmd_expand_seed_pool_network)

    show_browser_config = sub.add_parser("show-browser-config")
    add_browser_options(show_browser_config, include_headless=True)
    show_browser_config.set_defaults(func=cmd_show_browser_config)

    gui = sub.add_parser("gui")
    gui.set_defaults(func=cmd_open_gui)

    launch_real_chrome = sub.add_parser("launch-real-chrome")
    launch_real_chrome.add_argument("--url", default="https://accounts.google.com/")
    add_browser_options(launch_real_chrome)
    launch_real_chrome.set_defaults(func=cmd_launch_real_chrome)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    set_verbose(getattr(args, "verbose", False))
    try:
        args.func(args)
    except RuntimeError as exc:
        print(f"error: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
