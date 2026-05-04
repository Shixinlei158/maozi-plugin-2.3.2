from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

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

    @staticmethod
    def absolute_url(path: str | None) -> str:
        return urljoin(OZON_BASE, path or "")


def parse_seller_home_page(data: dict[str, Any]) -> dict[str, Any]:
    widget_states = data.get("widgetStates") or {}
    key = next((k for k in widget_states if k.startswith("tileGridDesktop-")), None)
    if not key:
        return {"items": [], "next_page": data.get("nextPage")}
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
    return {"items": items, "next_page": data.get("nextPage")}


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
