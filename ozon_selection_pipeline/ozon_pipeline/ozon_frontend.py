from __future__ import annotations

from html import unescape
import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from .config import settings
from .util import OZON_BASE, normalize_url, price_text, to_decimal


class OzonFrontendClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.trust_env = settings.requests_trust_env
        if settings.http_proxy_url:
            self.session.proxies["http"] = settings.http_proxy_url
        if settings.https_proxy_url:
            self.session.proxies["https"] = settings.https_proxy_url

    def seller_offers(self, sku: str) -> list[dict[str, Any]]:
        url = f"{OZON_BASE}/api/entrypoint-api.bx/page/json/v2"
        params = {"url": f"/modal/otherOffersFromSellers?product_id={sku}"}
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
            "Referer": f"{OZON_BASE}/product/{sku}/",
        }
        response = self.session.get(
            url,
            params=params,
            headers=headers,
            timeout=settings.request_timeout_seconds,
        )
        response.raise_for_status()
        return parse_seller_offers_widget(response.json())

    def seller_home_products(self, seller_url: str, max_pages: int = 100) -> dict[str, Any]:
        seller_path = extract_relative_url(seller_url)
        all_items: list[dict[str, Any]] = []
        seen_skus: set[str] = set()
        seen_paths: set[str] = set()
        pages_fetched = 0
        next_path: str | None = seller_path

        while next_path and next_path not in seen_paths and pages_fetched < max_pages:
            data = self._entrypoint_page(next_path, referer=self.absolute_url(next_path))
            parsed = parse_seller_home_page(data)
            items = parsed.get("items") or []
            if not items and pages_fetched == 0:
                raise RuntimeError(f"seller home api returned empty items on first page: {seller_url}")

            seen_paths.add(next_path)
            for item in items:
                sku = str(item.get("sku") or "")
                key = sku or str(item.get("href") or item.get("product_url") or "")
                if not key or key in seen_skus:
                    continue
                seen_skus.add(key)
                all_items.append(item)

            next_path = parsed.get("next_page")
            pages_fetched += 1

        return {
            "page_url": seller_url,
            "page_title": None,
            "items": all_items,
            "next_page": next_path,
            "source": "entrypoint_api_http",
            "pages_fetched": pages_fetched,
        }

    def product_snapshot(self, sku: str) -> dict[str, Any]:
        product_url = f"{OZON_BASE}/product/{sku}/"
        response = self.session.get(
            product_url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": OZON_BASE,
            },
            timeout=settings.request_timeout_seconds,
        )
        response.raise_for_status()
        html = response.text
        metas = extract_html_meta_map(html)
        title = (
            metas.get("og:title")
            or metas.get("twitter:title")
            or extract_html_title(html)
        )
        price_text = metas.get("product:price:amount") or metas.get("price")
        main_image_url = metas.get("og:image") or metas.get("twitter:image")
        return {
            "sku": str(sku),
            "title": title,
            "price": to_decimal(price_text),
            "currency": metas.get("product:price:currency") or ("RUB" if price_text else None),
            "main_image_url": main_image_url,
            "product_url": str(response.url or product_url),
            "raw": {
                "metas": metas,
                "title": extract_html_title(html),
            },
        }

    @staticmethod
    def absolute_url(path: str | None) -> str:
        return urljoin(OZON_BASE, path or "")

    def _entrypoint_page(self, relative_url: str, *, referer: str) -> dict[str, Any]:
        response = self.session.get(
            f"{OZON_BASE}/api/entrypoint-api.bx/page/json/v2",
            params={"url": relative_url},
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json,text/plain,*/*",
                "Referer": referer,
            },
            timeout=settings.request_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()


def parse_seller_home_page(data: dict[str, Any]) -> dict[str, Any]:
    widget_states = data.get("widgetStates") or {}
    key = next((k for k in widget_states if k.startswith("tileGridDesktop-")), None)
    if not key:
        return {"items": [], "next_page": extract_seller_home_next_page(data)}
    raw = widget_states[key]
    if isinstance(raw, str):
        raw = json.loads(raw)
    tiles = raw.get("items") or []
    items: list[dict[str, Any]] = []
    for tile in tiles:
        href = normalize_url(((tile.get("action") or {}).get("link")))
        if not href:
            continue
        price = extract_tile_price(tile)
        items.append(
            {
                "href": href,
                "product_url": href,
                "sku": str(tile.get("sku") or tile.get("id") or ""),
                "title": extract_tile_title(tile),
                "price_text": price,
                "price_amount": to_decimal(price),
                "currency": "RUB" if price and "₽" in price else None,
                "image_url": extract_tile_image(tile),
                "raw": tile,
            }
        )
    return {"items": items, "next_page": extract_seller_home_next_page(data)}


def extract_seller_home_next_page(data: dict[str, Any]) -> str | None:
    top_level = data.get("nextPage")
    if top_level:
        return top_level
    widget_states = data.get("widgetStates") or {}
    key = next((k for k in widget_states if k.startswith("infiniteVirtualPaginator-")), None)
    if not key:
        return None
    raw = widget_states[key]
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        return None
    return raw.get("nextPage")


def extract_relative_url(value: str) -> str:
    if not value:
        return "/"
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        path = parsed.path or "/"
        if parsed.query:
            return f"{path}?{parsed.query}"
        return path
    return value if value.startswith("/") else f"/{value}"


def extract_html_meta_map(html: str) -> dict[str, str]:
    metas: dict[str, str] = {}
    for match in re.finditer(r"<meta\b[^>]*?>", html or "", flags=re.IGNORECASE):
        tag = match.group(0)
        key = (
            _extract_html_attr(tag, "property")
            or _extract_html_attr(tag, "name")
            or _extract_html_attr(tag, "itemprop")
        )
        if not key:
            continue
        metas[key] = _extract_html_attr(tag, "content") or ""
    return metas


def extract_html_title(html: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", html or "", flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    text = re.sub(r"\s+", " ", unescape(match.group(1))).strip()
    return text or None


def _extract_html_attr(tag: str, attr: str) -> str | None:
    match = re.search(
        rf"""\b{re.escape(attr)}\s*=\s*(['"])(.*?)\1""",
        tag,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    return unescape(match.group(2)).strip() or None


def parse_seller_offers_widget(data: dict[str, Any]) -> list[dict[str, Any]]:
    widget_states = data.get("widgetStates") or {}
    key = next((k for k in widget_states if k.startswith("webSellerList-")), None)
    if not key:
        return []
    raw = widget_states[key]
    if isinstance(raw, str):
        raw = json.loads(raw)
    sellers = raw.get("sellers") or []
    result: list[dict[str, Any]] = []
    for seller in sellers:
        text = price_text(seller)
        result.append(
            {
                "name": seller.get("name"),
                "seller_home_url": normalize_url(seller.get("link")),
                "offer_product_url": normalize_url(seller.get("productLink")),
                "offer_sku": str(seller.get("sku") or ""),
                "logo_url": normalize_url(seller.get("logoImageUrl")),
                "price_text": text,
                "price_amount": to_decimal(text),
                "currency": "RUB" if text and "₽" in text else None,
                "raw": seller,
            }
        )
    return result


def extract_tile_title(tile: dict[str, Any]) -> str | None:
    for block in tile.get("mainState") or []:
        if block.get("type") == "textAtom":
            return ((block.get("textAtom") or {}).get("text") or "").strip() or None
    return None


def extract_tile_price(tile: dict[str, Any]) -> str | None:
    for block in tile.get("mainState") or []:
        if block.get("type") != "priceV2":
            continue
        parts = ((block.get("priceV2") or {}).get("price") or [])
        text = "".join((part.get("text") or "") for part in parts).strip()
        return text or None
    return None


def extract_tile_image(tile: dict[str, Any]) -> str | None:
    tile_image = tile.get("tileImage") or {}
    for item in tile_image.get("items") or []:
        image = item.get("image") or {}
        link = image.get("link")
        if link:
            return normalize_url(link)
    return None


def parse_seller_home_tile(tile: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}

    for block in tile.get("mainState") or []:
        block_type = block.get("type")

        if block_type == "labelList":
            label_list = block.get("labelList") or {}
            items = label_list.get("items") or []
            texts = [i.get("title", "") for i in items if i.get("title")]
            if texts:
                result["stock_label"] = "; ".join(texts)

        elif block_type == "labelListV2":
            label_v2 = block.get("labelListV2") or {}
            test_info = label_v2.get("testInfo") or {}
            automation_id = test_info.get("automatizationId", "")
            if automation_id == "tile-list-rating":
                texts = []
                for item in label_v2.get("items") or []:
                    text_obj = item.get("text") or {}
                    t = text_obj.get("text", "")
                    if t:
                        texts.append(t)
                combined = " ".join(texts)
                rating_match = re.search(r"(\d+\.?\d*)", combined)
                if rating_match:
                    try:
                        result["seller_rating"] = float(rating_match.group(1))
                    except ValueError:
                        pass
                review_match = re.search(r"(\d[\d\s]*)\s*отзыв", combined)
                if review_match:
                    try:
                        result["seller_review_count"] = int(review_match.group(1).replace(" ", ""))
                    except ValueError:
                        pass

    brand_logo = tile.get("brandLogo")
    if isinstance(brand_logo, dict):
        logo = brand_logo.get("logo")
        if logo:
            result["brand_logo_url"] = normalize_url(logo)

    multi_button = tile.get("multiButton") or {}
    ozon_button = multi_button.get("ozonButton") or {}
    atc = ozon_button.get("addToCart") or {}
    qb = atc.get("quantityButton") or {}
    max_items = qb.get("maxItems")
    if isinstance(max_items, (int, float)):
        result["stock_max"] = int(max_items)
    action_button = atc.get("actionButton") or {}
    delivery_title = action_button.get("title")
    if delivery_title and not re.search(r"^\d+$", delivery_title):
        result["delivery_hint"] = str(delivery_title)[:64]

    tile_image = tile.get("tileImage") or {}
    badge_v2 = tile_image.get("leftBottomBadgeV2")
    if isinstance(badge_v2, dict):
        badge_text = badge_v2.get("text")
        if badge_text:
            result["badges"] = [badge_text]

    return result
