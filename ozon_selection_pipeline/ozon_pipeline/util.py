from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any
from urllib.parse import urljoin


OZON_BASE = "https://www.ozon.ru"


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=json_default)


def to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    text = str(value).strip().replace("%", "")
    text = re.sub(r"\s+", "", text, flags=re.UNICODE).replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def to_int(value: Any) -> int | None:
    number = to_decimal(value)
    return int(number) if number is not None else None


def percent_text_to_decimal(value: Any) -> Decimal | None:
    return to_decimal(value)


def grams_from_text(value: Any) -> Decimal | None:
    return to_decimal(value)


def seller_key(home_url: str) -> str:
    normalized = normalize_url(home_url)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def normalize_url(value: str | None) -> str:
    if not value:
        return ""
    return urljoin(OZON_BASE, value)


def price_text(value: dict[str, Any]) -> str | None:
    price = value.get("price") or {}
    card_price = price.get("cardPrice") or {}
    return card_price.get("price") or price.get("price")


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")
