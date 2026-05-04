from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .config import ROOT_DIR, settings
from .ozon_frontend import OZON_BASE, parse_seller_home_page, parse_seller_offers_widget
from .util import grams_from_text, percent_text_to_decimal, to_decimal, to_int

MAOZI_SELECTION_URL = "https://ozon.maozierp.com/#/selection/top-list"
MAOZI_SELECTION_ORIGIN = "https://ozon.maozierp.com"


class BrowserOzonClient:
    def __init__(
        self,
        profile_dir: str | None = None,
        extension_dir: str | None = None,
        executable_path: str | None = None,
        channel: str | None = None,
        proxy_server: str | None = None,
        cdp_url: str | None = None,
        remote_debugging_port: int | None = None,
        headless: bool | None = None,
    ) -> None:
        self.profile_dir = resolve_path(profile_dir or settings.chrome_profile_dir)
        self.extension_dir = resolve_path(extension_dir or settings.chrome_extension_dir)
        self.extension_id = settings.chrome_extension_id
        self.executable_path = resolve_optional_path(executable_path or settings.chrome_executable_path)
        self.channel = settings.chrome_channel if channel is None else channel
        self.proxy_server = settings.chrome_proxy_server if proxy_server is None else proxy_server
        self.cdp_url = settings.chrome_cdp_url if cdp_url is None else cdp_url
        self.remote_debugging_port = (
            settings.chrome_remote_debugging_port if remote_debugging_port is None else remote_debugging_port
        )
        self.headless = settings.chrome_headless if headless is None else headless

    def seller_offers(self, sku: str) -> list[dict[str, Any]]:
        return self._run_page_task(self._fetch_seller_offers, sku)

    def product_snapshot(self, sku: str) -> dict[str, Any]:
        return self._run_page_task(self._fetch_product_snapshot, sku)

    def seller_home_products(self, seller_url: str, max_scrolls: int = 8) -> dict[str, Any]:
        return self._run_page_task(self._fetch_seller_home_products, seller_url, max_scrolls=max_scrolls)

    def maozi_sku3(self, sku: str) -> dict[str, Any]:
        return self._run_page_task(self._fetch_maozi_sku3, sku)

    def plugin_card_snapshot(self, sku: str) -> dict[str, Any]:
        return self._run_page_task(self._fetch_plugin_card_snapshot, sku)

    def top_list_page(self, filters: dict[str, Any], page_no: int, page_size: int = 50) -> dict[str, Any]:
        return self._run_page_task(self._fetch_top_list_page, filters, page_no, page_size)

    def warmup(self, url: str = OZON_BASE) -> None:
        sync_playwright = import_sync_playwright()

        with sync_playwright() as p:
            context = self._launch_context(p)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(url, wait_until="domcontentloaded")
                input(
                    "浏览器已打开。请在该窗口完成目标账号、Ozon 和插件登录；确认无误后按回车关闭浏览器..."
                )
            finally:
                context.close()

    def describe(self) -> dict[str, Any]:
        resolved_executable = self.executable_path or detect_chrome_executable()
        return {
            "profile_dir": str(self.profile_dir),
            "extension_dir": str(self.extension_dir),
            "extension_exists": self.extension_dir.exists(),
            "extension_id": self.extension_id,
            "executable_path": str(resolved_executable) if resolved_executable else "<not-found>",
            "executable_exists": resolved_executable.exists() if resolved_executable else False,
            "channel": self.channel or "<playwright-default>",
            "proxy_server": self.proxy_server or "<none>",
            "cdp_url": self.cdp_url or "<none>",
            "remote_debugging_port": self.remote_debugging_port,
            "headless": self.headless,
        }

    def launch_real_chrome(self, url: str = OZON_BASE) -> None:
        executable = self.executable_path or detect_chrome_executable()
        if not executable or not executable.exists():
            raise RuntimeError(
                "Chrome executable was not found. Set CHROME_EXECUTABLE_PATH or pass --chrome-exe explicitly."
            )
        args = [
            str(executable),
            f"--user-data-dir={self.profile_dir}",
            f"--remote-debugging-port={self.remote_debugging_port}",
        ]
        if self.proxy_server:
            args.append(f"--proxy-server={self.proxy_server}")
        if self.extension_dir.exists():
            args.extend(
                [
                    f"--disable-extensions-except={self.extension_dir}",
                    f"--load-extension={self.extension_dir}",
                ]
            )
        args.append(url)
        subprocess.Popen(args)

    def _launch_context(self, playwright: Any):
        args = ["--disable-dev-shm-usage"]
        if self.extension_dir.exists():
            args.extend(
                [
                    f"--disable-extensions-except={self.extension_dir}",
                    f"--load-extension={self.extension_dir}",
                ]
            )
        kwargs: dict[str, Any] = {
            "headless": self.headless,
            "args": args,
        }
        if self.executable_path:
            kwargs["executable_path"] = str(self.executable_path)
        elif self.channel:
            kwargs["channel"] = self.channel
        if self.proxy_server:
            kwargs["proxy"] = {"server": self.proxy_server}
        return playwright.chromium.launch_persistent_context(
            str(self.profile_dir),
            **kwargs,
        )

    def _run_page_task(self, handler: Any, *args: Any, **kwargs: Any) -> Any:
        sync_playwright = import_sync_playwright()

        with sync_playwright() as p:
            if self.cdp_url:
                try:
                    browser = p.chromium.connect_over_cdp(self.cdp_url)
                    context = browser.contexts[0] if browser.contexts else browser.new_context()
                    page = self._acquire_cdp_page(context, handler)
                    try:
                        return handler(page, *args, **kwargs)
                    finally:
                        if page not in context.pages:
                            page.close()
                except Exception:
                    pass
            context = self._launch_context(p)
            try:
                page = context.new_page()
                return handler(page, *args, **kwargs)
            finally:
                context.close()

    def _acquire_cdp_page(self, context: Any, handler: Any):
        handler_name = getattr(handler, "__name__", "")
        pages = list(context.pages)

        if handler_name in {"_fetch_seller_offers", "_fetch_seller_home_products", "_fetch_seller_home_products_api"}:
            for page in pages:
                if page.url.startswith(OZON_BASE):
                    return page

        if handler_name == "_fetch_top_list_page":
            for page in pages:
                if page.url.startswith(MAOZI_SELECTION_ORIGIN):
                    return page

        # Prefer reusing a disposable tab so we do not disturb the user's live Ozon pages.
        for page in pages:
            if page.url.startswith(("about:blank", "chrome-error://")):
                return page

        if handler_name == "_fetch_maozi_sku3":
            extension_prefix = f"chrome-extension://{self.extension_id}/"
            for page in pages:
                if page.url.startswith(extension_prefix):
                    return page

        for page in pages:
            if page.url.startswith(OZON_BASE):
                return page

        return context.new_page()

    def _fetch_seller_offers(self, page: Any, sku: str) -> list[dict[str, Any]]:
        if not page.url.startswith(OZON_BASE):
            page.goto(OZON_BASE, wait_until="domcontentloaded")
        data = page.evaluate(
            """
            async (sku) => {
              const target = `/modal/otherOffersFromSellers?product_id=${sku}`;
              const url = `/api/entrypoint-api.bx/page/json/v2?url=${encodeURIComponent(target)}`;
              const response = await fetch(url, { credentials: "include" });
              if (!response.ok) {
                throw new Error(`Ozon seller offers request failed: ${response.status}`);
              }
              return await response.json();
            }
            """,
            str(sku),
        )
        return parse_seller_offers_widget(data)

    def _fetch_product_snapshot(self, page: Any, sku: str) -> dict[str, Any]:
        page.goto(f"{OZON_BASE}/product/{sku}/", wait_until="domcontentloaded")
        raw = page.evaluate(
            """
            () => {
              function collectJsonLd() {
                const scripts = Array.from(document.querySelectorAll('script[type="application/ld+json"]'));
                const values = [];
                for (const script of scripts) {
                  try {
                    values.push(JSON.parse(script.textContent || 'null'));
                  } catch (error) {
                  }
                }
                return values;
              }

              function walk(node, visit) {
                if (!node) return;
                if (Array.isArray(node)) {
                  for (const item of node) walk(item, visit);
                  return;
                }
                if (typeof node !== 'object') return;
                visit(node);
                for (const value of Object.values(node)) walk(value, visit);
              }

              const metas = {};
              for (const meta of Array.from(document.querySelectorAll('meta'))) {
                const key = meta.getAttribute('property') || meta.getAttribute('name') || meta.getAttribute('itemprop');
                if (!key) continue;
                metas[key] = meta.getAttribute('content') || '';
              }

              const jsonLd = collectJsonLd();
              let ldPrice = null;
              let ldCurrency = null;
              let ldImage = null;
              let ldTitle = null;

              walk(jsonLd, (node) => {
                if (!ldTitle && typeof node.name === 'string') ldTitle = node.name;
                if (!ldImage && typeof node.image === 'string') ldImage = node.image;
                if (!ldPrice && typeof node.price !== 'undefined') ldPrice = String(node.price);
                if (!ldCurrency && typeof node.priceCurrency === 'string') ldCurrency = node.priceCurrency;
                if (!ldPrice && typeof node.lowPrice !== 'undefined') ldPrice = String(node.lowPrice);
              });

              return {
                title:
                  document.querySelector('h1')?.textContent?.trim() ||
                  metas['og:title'] ||
                  ldTitle ||
                  document.title ||
                  null,
                price_text:
                  metas['product:price:amount'] ||
                  metas['price'] ||
                  ldPrice ||
                  null,
                currency:
                  metas['product:price:currency'] ||
                  ldCurrency ||
                  null,
                main_image_url:
                  metas['og:image'] ||
                  ldImage ||
                  null,
                product_url: window.location.href,
                raw: {
                  metas,
                  jsonLd,
                },
              };
            }
            """
        )
        return {
            "sku": str(sku),
            "title": raw.get("title"),
            "price": to_decimal(raw.get("price_text")),
            "currency": raw.get("currency") or ("RUB" if raw.get("price_text") else None),
            "main_image_url": raw.get("main_image_url"),
            "product_url": raw.get("product_url"),
            "raw": raw,
        }

    def _fetch_maozi_sku3(self, page: Any, sku: str) -> dict[str, Any]:
        page.goto(self._extension_popup_url(), wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(1000)
        result = page.evaluate(
            """
            async ({ sku, pluginVersion }) => {
              const storage = await chrome.storage.local.get(["maozierp-token"]);
              const token = storage["maozierp-token"];
              if (!token) {
                throw new Error("maozierp-token is missing in chrome.storage.local");
              }
              const response = await fetch(`https://api.maozierp.com/api.chrome/sku3?sku=${sku}`, {
                method: "POST",
                headers: {
                  "Authorization": `Bearer ${token}`,
                  "Client": "plugin",
                  "Plugin-Version": pluginVersion,
                  "Content-Type": "application/json",
                  "User-Agent": "Mozilla/5.0"
                },
                body: JSON.stringify({ sku: String(sku) })
              });
              const text = await response.text();
              let data = null;
              try {
                data = JSON.parse(text);
              } catch (error) {
              }
              return {
                ok: response.ok,
                status: response.status,
                text,
                data
              };
            }
            """,
            {"sku": str(sku), "pluginVersion": settings.maozi_plugin_version},
        )
        if not result.get("ok"):
            raise RuntimeError(f"extension sku3 request failed with HTTP {result.get('status')}: {result.get('text')}")
        data = result.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("extension sku3 request returned non-JSON payload")
        return data

    def _fetch_plugin_card_snapshot(self, page: Any, sku: str) -> dict[str, Any]:
        page.goto(f"{OZON_BASE}/product/{sku}/", wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(8000)
        body_text = page.evaluate("() => document.body.innerText || ''")
        card = parse_plugin_card_text(body_text)
        card["raw_text"] = body_text
        return card

    def _fetch_seller_home_products(self, page: Any, seller_url: str, max_scrolls: int = 8) -> dict[str, Any]:
        try:
            return self._fetch_seller_home_products_api(page, seller_url)
        except Exception:
            pass
        return self._fetch_seller_home_products_dom(page, seller_url, max_scrolls=max_scrolls)

    def _fetch_seller_home_products_api(self, page: Any, seller_url: str) -> dict[str, Any]:
        if not page.url.startswith(OZON_BASE):
            page.goto(seller_url, wait_until="domcontentloaded", timeout=120000)
            page.wait_for_timeout(1500)
        seller_path = extract_relative_url(seller_url)
        raw = page.evaluate(
            """
            async ({ sellerPath }) => {
              const response = await fetch(`/api/entrypoint-api.bx/page/json/v2?url=${encodeURIComponent(sellerPath)}`, {
                credentials: "include"
              });
              return {
                ok: response.ok,
                status: response.status,
                text: await response.text(),
              };
            }
            """,
            {"sellerPath": seller_path},
        )
        if not raw.get("ok"):
            raise RuntimeError(f"seller home api request failed with HTTP {raw.get('status')}")
        import json

        data = json.loads(raw["text"])
        parsed = parse_seller_home_page(data)
        return {
            "page_url": seller_url,
            "page_title": page.title(),
            "items": parsed.get("items") or [],
            "next_page": parsed.get("next_page"),
            "source": "entrypoint_api",
        }

    def _fetch_seller_home_products_dom(self, page: Any, seller_url: str, max_scrolls: int = 8) -> dict[str, Any]:
        page.goto(seller_url, wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(3000)
        for _ in range(max_scrolls):
            page.mouse.wheel(0, 5000)
            page.wait_for_timeout(1200)
        raw = page.evaluate(
            """
            () => {
              const map = new Map();
              for (const a of Array.from(document.querySelectorAll('a[href*="/product/"]'))) {
                const href = a.href;
                if (!href) continue;
                const text = (a.textContent || '').trim();
                const card = a.closest('[class*="tile"], [class*="widget"], article, li, div');
                const img = card ? card.querySelector('img') : null;
                const priceText = card ? (card.textContent || '') : '';
                const current = map.get(href) || {
                  href,
                  title: '',
                  badge_texts: [],
                  image_url: img ? (img.currentSrc || img.src || null) : null,
                  raw_text: priceText.trim(),
                };
                if (text) {
                  if (text.length > current.title.length && !/^новинка$|^вау-цены$/i.test(text)) {
                    current.title = text;
                  } else if (/^новинка$|^вау-цены$/i.test(text)) {
                    current.badge_texts.push(text);
                  }
                }
                if (!current.image_url && img) {
                  current.image_url = img.currentSrc || img.src || null;
                }
                map.set(href, current);
              }
              return {
                page_url: location.href,
                page_title: document.title,
                items: Array.from(map.values()),
              };
            }
            """
        )
        raw["source"] = "dom_scroll"
        return raw

    def _fetch_top_list_page(self, page: Any, filters: dict[str, Any], page_no: int, page_size: int = 50) -> dict[str, Any]:
        if not page.url.startswith(MAOZI_SELECTION_ORIGIN):
            page.goto(MAOZI_SELECTION_URL, wait_until="domcontentloaded", timeout=120000)
            page.wait_for_timeout(1500)
        return page.evaluate(
            """
            async ({ filters, pageNo, pageSize }) => {
              const access = JSON.parse(localStorage.getItem('maozierp-core-access') || '{}');
              const token = access.accessToken || '';
              if (!token) {
                throw new Error('maozierp-core-access.accessToken is missing');
              }
              const params = new URLSearchParams();
              for (const [key, value] of Object.entries(filters || {})) {
                if (Array.isArray(value)) {
                  for (const item of value) {
                    params.append(`${key}[]`, item ?? '');
                  }
                  continue;
                }
                params.set(key, value ?? '');
              }
              params.set('page', String(pageNo));
              params.set('page_size', String(pageSize));
              const response = await fetch(`https://api.maozierp.com/api.selection.top/lists?${params.toString()}`, {
                method: 'GET',
                credentials: 'include',
                headers: {
                  'Accept': 'application/json, text/plain, */*',
                  'Authorization': `Bearer ${token}`,
                  'Client': 'pc',
                  'X-Client-Type': 'pc',
                  'DNT': '1'
                }
              });
              const text = await response.text();
              let data = null;
              try {
                data = JSON.parse(text);
              } catch (error) {
              }
              return {
                ok: response.ok,
                status: response.status,
                text,
                data
              };
            }
            """,
            {"filters": filters, "pageNo": int(page_no), "pageSize": int(page_size)},
        )

    def _extension_popup_url(self) -> str:
        return f"chrome-extension://{self.extension_id}/popup.html"


def resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (ROOT_DIR / path).resolve()


def resolve_optional_path(value: str | None) -> Path | None:
    if not value:
        return None
    return resolve_path(value)


def extract_relative_url(value: str) -> str:
    if not value:
        return "/"
    if value.startswith("http://") or value.startswith("https://"):
        match = re.match(r"^https?://[^/]+(?P<path>/.*)?$", value)
        if match:
            return match.group("path") or "/"
    return value if value.startswith("/") else f"/{value}"


def detect_chrome_executable() -> Path | None:
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/snap/bin/chromium"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def import_sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Playwright Python package is not installed in the current interpreter. "
            "Run `python -m pip install -r requirements.txt` in the project directory first."
        ) from exc
    return sync_playwright


def parse_plugin_card_text(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    start = next((index for index, line in enumerate(lines) if line.startswith("类目：")), -1)
    if start < 0:
        return {"metric_overrides": {}, "card_lines": []}

    stop_markers = {"一键上架", "编辑上架", "Купить сейчас"}
    card_lines: list[str] = []
    for line in lines[start:]:
        if line in stop_markers:
            break
        card_lines.append(line)

    line_map: dict[str, str] = {}
    index = 0
    while index < len(card_lines):
        line = card_lines[index]
        if line.startswith("rFBS佣金："):
            values = []
            for extra in card_lines[index + 1 : index + 4]:
                if "：" in extra:
                    break
                values.append(extra)
            line_map["rFBS佣金"] = "|".join(values)
            index += max(1, len(values) + 1)
            continue
        if "：" in line:
            label, value = line.split("：", 1)
            line_map[label.strip()] = value.strip()
        index += 1

    metric_overrides: dict[str, Any] = {}
    value = clean_card_value(line_map.get("类目"))
    if value is not None:
        metric_overrides["category"] = value

    value = clean_card_value(line_map.get("品牌"))
    if value is not None:
        metric_overrides["brand"] = value

    value = clean_card_value(line_map.get("月销量"))
    parsed = to_int(value)
    if parsed is not None:
        metric_overrides["sold_count"] = parsed

    value = clean_card_value(line_map.get("月销售额"))
    if value is not None:
        metric_overrides["sold_sum_text"] = value

    value = clean_card_value(line_map.get("月周转动态"))
    parsed = to_decimal(value)
    if parsed is not None:
        metric_overrides["sales_dynamics"] = parsed

    value = clean_card_value(line_map.get("广告费占比"))
    parsed = percent_text_to_decimal(value)
    if parsed is not None:
        metric_overrides["drr"] = parsed

    value = clean_card_value(line_map.get("参与促销天数"))
    parsed = to_int(value)
    if parsed is not None:
        metric_overrides["days_in_promo"] = parsed

    value = clean_card_value(line_map.get("参与促销的折扣"))
    parsed = percent_text_to_decimal(value)
    if parsed is not None:
        metric_overrides["discount"] = parsed

    value = clean_card_value(line_map.get("促销活动的转化率"))
    parsed = percent_text_to_decimal(value)
    if parsed is not None:
        metric_overrides["promo_revenue_share"] = parsed

    value = clean_card_value(line_map.get("付费推广天数"))
    parsed = to_int(value)
    if parsed is not None:
        metric_overrides["days_with_trafarets"] = parsed

    value = clean_card_value(line_map.get("商品卡浏览量"))
    parsed = to_int(value)
    if parsed is not None:
        metric_overrides["qty_view_pdp"] = parsed

    value = clean_card_value(line_map.get("商品卡加购率"))
    parsed = percent_text_to_decimal(value)
    if parsed is not None:
        metric_overrides["conv_to_cart_pdp"] = parsed

    value = clean_card_value(line_map.get("搜索目录浏览量"))
    parsed = to_int(value)
    if parsed is not None:
        metric_overrides["session_count_search"] = parsed

    value = clean_card_value(line_map.get("搜索目录加购率"))
    parsed = percent_text_to_decimal(value)
    if parsed is not None:
        metric_overrides["conv_to_cart_search"] = parsed

    value = clean_card_value(line_map.get("展示转化率"))
    parsed = percent_text_to_decimal(value)
    if parsed is not None:
        metric_overrides["conv_view_to_order"] = parsed

    value = clean_card_value(line_map.get("商品点击率"))
    if value is not None:
        metric_overrides["custom_click_rate_text"] = value
        parsed = percent_text_to_decimal(value)
        if parsed is not None:
            metric_overrides["custom_click_rate"] = parsed

    value = clean_card_value(line_map.get("发货模式"))
    if value is not None:
        metric_overrides["sales_schema"] = value

    value = clean_card_value(line_map.get("退货取消率"))
    parsed = percent_text_to_decimal(value)
    if parsed is not None:
        metric_overrides["nullable_redemption_rate"] = parsed

    value = clean_card_value(line_map.get("长 宽 高"))
    if value is not None:
        metric_overrides["custom_volume_text"] = value

    value = clean_card_value(line_map.get("重 量"))
    if value is not None:
        metric_overrides["custom_weight_text"] = value
        parsed = grams_from_text(value)
        if parsed is not None:
            metric_overrides["custom_weight_g"] = parsed

    value = clean_card_value(line_map.get("上架时间"))
    if value is not None:
        metric_overrides["nullable_create_date_text"] = value
        match = re.search(r"\((\d+)天\)", value)
        if match:
            metric_overrides["create_days"] = int(match.group(1))

    value = clean_card_value(line_map.get("跟卖列表"))
    seller_offer_count = None
    if value is not None:
        match = re.search(r"等(\d+)个卖家", value)
        if match:
            seller_offer_count = int(match.group(1))

    return {
        "metric_overrides": metric_overrides,
        "seller_offer_count": seller_offer_count,
        "card_lines": card_lines,
        "line_map": line_map,
    }


def clean_card_value(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text or text in {"暂无数据", "--", "-", "无"}:
        return None
    return text
