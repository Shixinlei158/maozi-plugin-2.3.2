from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlencode, urlparse

import pymysql
import requests
from PIL import Image
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright


ROOT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT_DIR.parent
load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    db_host: str = os.getenv("DB_HOST", "localhost")
    db_port: int = int(os.getenv("DB_PORT", "3306"))
    db_user: str = os.getenv("DB_USER", "root")
    db_password: str = os.getenv("DB_PASSWORD", "root")
    db_name: str = os.getenv("DB_NAME", "ozon_selection")
    chrome_profile_dir: str = os.getenv("CHROME_PROFILE_DIR", str(ROOT_DIR / "profiles" / "profile-001"))
    chrome_executable_path: str = os.getenv("CHROME_EXECUTABLE_PATH", "")
    chrome_channel: str = os.getenv("CHROME_CHANNEL", "chrome")
    chrome_proxy_server: str = os.getenv("CHROME_PROXY_SERVER", "")
    chrome_cdp_url: str = os.getenv("CHROME_CDP_URL", "")
    chrome_remote_debugging_port: int = int(os.getenv("CHROME_REMOTE_DEBUGGING_PORT", "9222"))
    chrome_headless: bool = os.getenv("CHROME_HEADLESS", "false").lower() in {"1", "true", "yes"}
    input_image_dir: str = os.getenv("INPUT_IMAGE_DIR", str(PROJECT_ROOT / "ozon_pic"))
    download_dir: str = os.getenv("DOWNLOAD_DIR", str(ROOT_DIR / "downloads"))
    result_dir: str = os.getenv("RESULT_DIR", str(ROOT_DIR / "results"))
    target_offer_tags: str = os.getenv("TARGET_OFFER_TAGS", "1988226")
    target_sort_type: str = os.getenv("TARGET_SORT_TYPE", "normal")
    search_page_base: str = os.getenv("SEARCH_PAGE_BASE", "https://s.1688.com/youyuan/index.htm")
    search_wait_timeout_ms: int = int(os.getenv("SEARCH_WAIT_TIMEOUT_MS", "60000"))
    max_matches_per_image: int = int(os.getenv("MAX_MATCHES_PER_IMAGE", "10"))
    source_image_table_limit: int = int(os.getenv("SOURCE_IMAGE_TABLE_LIMIT", "0"))


settings = Settings()


def connect_db(database: str | None = None):
    kwargs = dict(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )
    if database:
        kwargs["database"] = database
    else:
        kwargs["database"] = settings.db_name
    return pymysql.connect(**kwargs)


def run_sql_file(path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    statements = split_sql(sql)
    with connect_db(database=None) as conn:
        with conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)
        conn.commit()


def split_sql(sql: str) -> Iterable[str]:
    buffer: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(buffer).strip().rstrip(";")
            buffer.clear()
            if statement:
                yield statement
    tail = "\n".join(buffer).strip()
    if tail:
        yield tail


def execute(sql: str, params: dict[str, Any] | None = None) -> int:
    with connect_db() as conn:
        with conn.cursor() as cur:
            rowcount = cur.execute(sql, params or {})
        conn.commit()
        return rowcount


def fetch_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            return list(cur.fetchall())


def fetch_source_sku_images(limit: int = 0) -> list[dict[str, Any]]:
    sql = """
        SELECT sku, main_image_url
        FROM sku_products
        WHERE main_image_url IS NOT NULL AND main_image_url != ''
        ORDER BY sku
    """
    params: dict[str, Any] | None = None
    if limit and limit > 0:
        sql += " LIMIT %(limit)s"
        params = {"limit": int(limit)}
    return fetch_all(sql, params)


def fetch_table_columns(table_name: str) -> list[str]:
    rows = fetch_all(
        """
        SELECT COLUMN_NAME AS column_name
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %(table_name)s
        ORDER BY ORDINAL_POSITION
        """,
        {"table_name": table_name},
    )
    return [str(row["column_name"]) for row in rows]


def upsert_run(run_key: str, image_dir: str, download_dir: str) -> int:
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO matched_source_runs
                  (run_key, chrome_profile_dir, chrome_executable_path, chrome_channel, chrome_remote_debugging_port,
                   image_input_dir, download_dir, status, started_at)
                VALUES
                  (%(run_key)s, %(chrome_profile_dir)s, %(chrome_executable_path)s, %(chrome_channel)s,
                   %(chrome_remote_debugging_port)s, %(image_input_dir)s, %(download_dir)s, 'running', CURRENT_TIMESTAMP)
                ON DUPLICATE KEY UPDATE
                  chrome_profile_dir=VALUES(chrome_profile_dir),
                  chrome_executable_path=VALUES(chrome_executable_path),
                  chrome_channel=VALUES(chrome_channel),
                  chrome_remote_debugging_port=VALUES(chrome_remote_debugging_port),
                  image_input_dir=VALUES(image_input_dir),
                  download_dir=VALUES(download_dir),
                  status='running',
                  error_message=NULL,
                  started_at=CURRENT_TIMESTAMP,
                  finished_at=NULL
                """,
                {
                    "run_key": run_key,
                    "chrome_profile_dir": settings.chrome_profile_dir,
                    "chrome_executable_path": settings.chrome_executable_path or None,
                    "chrome_channel": settings.chrome_channel,
                    "chrome_remote_debugging_port": settings.chrome_remote_debugging_port,
                    "image_input_dir": image_dir,
                    "download_dir": download_dir,
                },
            )
            conn.commit()
        return get_run_id(run_key)


def get_run_id(run_key: str) -> int:
    row = fetch_one("SELECT id FROM matched_source_runs WHERE run_key=%(run_key)s", {"run_key": run_key})
    if not row:
        raise RuntimeError(f"run not found: {run_key}")
    return int(row["id"])


def fetch_one(sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.fetchone()


def finish_run(run_key: str, *, status: str, source_count: int, matched_count: int, failed_count: int, error_message: str | None = None) -> None:
    execute(
        """
        UPDATE matched_source_runs
        SET status=%(status)s,
            source_count=%(source_count)s,
            matched_count=%(matched_count)s,
            failed_count=%(failed_count)s,
            error_message=%(error_message)s,
            finished_at=CASE WHEN %(status)s IN ('success','failed') THEN CURRENT_TIMESTAMP ELSE finished_at END
        WHERE run_key=%(run_key)s
        """,
        {
            "run_key": run_key,
            "status": status,
            "source_count": source_count,
            "matched_count": matched_count,
            "failed_count": failed_count,
            "error_message": error_message,
        },
    )


def sanitize_filename(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", name).strip("._") or "sku"


def image_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_image_downloads(input_dir: Path, download_dir: Path) -> list[Path]:
    download_dir.mkdir(parents=True, exist_ok=True)
    candidates = sorted([p for p in input_dir.iterdir() if p.is_file() and p.stem.lower().startswith("sku")])
    if not candidates:
        candidates = sorted([p for p in input_dir.iterdir() if p.is_file()])

    copied: list[Path] = []
    for src in candidates:
        ext = src.suffix.lower() if src.suffix else ".jpg"
        dst = download_dir / f"{sanitize_filename(src.stem)}{ext}"
        if dst.resolve() != src.resolve():
            shutil.copy2(src, dst)
        copied.append(dst)
    return copied


def download_remote_source_images(download_dir: Path, limit: int = 0) -> list[Path]:
    download_dir.mkdir(parents=True, exist_ok=True)
    rows = fetch_source_sku_images(limit=limit)
    downloaded: list[Path] = []
    for row in rows:
        sku = str(row.get("sku") or "").strip()
        url = str(row.get("main_image_url") or "").strip()
        if not sku or not url:
            continue
        ext = guess_extension(url)
        target = download_dir / f"{sanitize_filename(sku)}{ext}"
        if target.exists() and target.stat().st_size > 0:
            downloaded.append(target)
            continue
        try:
            resp = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            target.write_bytes(resp.content)
            downloaded.append(target)
            print(f"downloaded {sku}")
        except Exception as exc:
            print(f"warn: failed to download {sku}: {exc}")
    return downloaded


def guess_extension(url: str) -> str:
    ext = Path(urlparse(url).path).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ext
    return ".jpg"


def image_meta(path: Path) -> dict[str, Any]:
    with Image.open(path) as img:
        width, height = img.size
        mime = Image.MIME.get(img.format, "application/octet-stream")
    stat = path.stat()
    return {
        "path": str(path),
        "file_name": path.name,
        "hash": image_hash(path),
        "size_bytes": stat.st_size,
        "width": width,
        "height": height,
        "mime": mime,
    }


def parse_jsonish(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("{") or stripped.startswith("["):
        return json.loads(stripped)
    m = re.search(r"\((\{.*\}|\[.*\])\)\s*$", stripped, flags=re.S)
    if m:
        return json.loads(m.group(1))
    return stripped


def safe_json(value: Any, default: Any) -> str:
    if value is None:
        value = default
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except Exception:
            return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def build_search_url(image_id: str, image_id_list: str, offer_tags: str, sort_type: str) -> str:
    query = {
        "tab": "imageSearch",
        "imageId": image_id,
        "imageIdList": image_id_list,
        "spm": "a260k.home2025.imagesearch.upload",
        "sortType": sort_type,
        "offerTags": offer_tags,
    }
    return f"{settings.search_page_base}?{urlencode(query)}"


def upload_image_and_open_search(page, image_path: Path) -> dict[str, Any]:
    upload = page.locator('input[type="file"]').first
    if upload.count() == 0:
        raise RuntimeError("no file input found on image search page")

    def is_upload_response(resp) -> bool:
        return "mtop.cbu.pc.tiansuo.portal.tools.media.image.upload" in resp.url

    image_id = None
    image_id_list = None
    raw_upload: Any = None
    try:
        with page.expect_response(is_upload_response, timeout=settings.search_wait_timeout_ms) as resp_info:
            upload.set_input_files(str(image_path))
        raw_upload = parse_jsonish(resp_info.value.text())
    except Exception:
        # Some builds auto-navigate first and expose the imageId in the URL.
        upload.set_input_files(str(image_path))
        page.wait_for_timeout(2500)
        raw_upload = None

    if isinstance(raw_upload, dict):
        data = raw_upload.get("data") or {}
        image_id = data.get("imageId") or data.get("image_id") or image_id
        image_id_list = data.get("imageIdList") or data.get("image_id_list") or image_id_list

    if not image_id:
        parsed = urlparse(page.url)
        params = parse_qs(parsed.query)
        image_id = (params.get("imageId") or [None])[0]
        image_id_list = (params.get("imageIdList") or [None])[0]

    if not image_id:
        raise RuntimeError(f"failed to obtain imageId for {image_path.name}")

    search_url = build_search_url(image_id, image_id_list or image_id, settings.target_offer_tags, settings.target_sort_type)
    page.goto(search_url, wait_until="domcontentloaded", timeout=settings.search_wait_timeout_ms)
    page.wait_for_timeout(4000)
    return {
        "search_url": search_url,
        "image_id": image_id,
        "image_id_list": image_id_list or image_id,
        "raw_upload": raw_upload,
    }


def extract_offer_cards(page) -> list[dict[str, Any]]:
    cards = page.locator('[data-splus-logkey*="offerlist.offer"], .major-offer')
    count = min(cards.count(), settings.max_matches_per_image)
    results: list[dict[str, Any]] = []
    for idx in range(count):
        card = cards.nth(idx)
        text = card.inner_text(timeout=5000)
        html = card.evaluate("(el) => el.outerHTML")
        img_src = card.locator("img.mainImg--GT1EYFGa").first.get_attribute("src") if card.locator("img.mainImg--GT1EYFGa").count() else None
        shop_name = card.locator('.shopName--vsCP_gNh').first.text_content() if card.locator('.shopName--vsCP_gNh').count() else None
        shop_uid = None
        shop_years = card.locator('.tpYear--BPYmBM3z').first.text_content() if card.locator('.tpYear--BPYmBM3z').count() else None
        title = None
        price_text = None
        price_amount = None
        sales_text = None
        min_order_text = None
        services: list[str] = []
        tags: list[str] = []
        attributes: list[str] = []
        detail_url = None
        anchors = card.locator('a[href]').evaluate_all("(els) => els.map(a => ({href: a.href, text: a.textContent || '', title: a.title || '', attrs: Array.from(a.attributes).map(x => [x.name, x.value])}))")
        if anchors:
            for a in anchors:
                href = a.get("href")
                text_value = (a.get("text") or "").strip()
                if href and not detail_url:
                    detail_url = href
                if text_value and text_value != "旺旺在线":
                    if not title:
                        title = text_value
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            title = title or lines[0]
        for line in lines:
            if line.startswith("¥") and price_text is None:
                price_text = line.replace("¥", "", 1).strip()
                price_amount = to_number(price_text)
            elif re.fullmatch(r"\d+(?:\.\d+)?万\+件|\d+\+件|\d+件|\d+\.?\d*万?件|\d+件起批", line):
                if sales_text is None and "起批" not in line:
                    sales_text = line
                elif min_order_text is None and "起批" in line:
                    min_order_text = line
            elif line in {"先采后付", "7天无理由", "包邮", "代发包邮", "官方物流", "真实工厂认证", "实力认证", "退货包运费"}:
                if line not in services:
                    services.append(line)
            elif "入驻" in line and shop_years is None:
                shop_years = line
            elif line and line not in services and line not in tags and line not in {title, sales_text, min_order_text}:
                if any(k in line for k in ["面料", "工艺", "厚薄", "款式", "适合", "主面料", "腰带扣", "材质", "裤长", "功能", "种类", "处理", "价格低", "同款低", "全网低价", "首单减", "回头率", "元宝可抵"]):
                    attributes.append(line)

        if detail_url:
            parsed = urlparse(detail_url)
            q = parse_qs(parsed.query)
            shop_uid = (q.get("uid") or [None])[0]

        results.append(
            {
                "matched_rank": idx + 1,
                "matched_score": parse_score_from_card(html),
                "matched_title": title,
                "matched_offer_id": parse_offer_id(detail_url),
                "matched_product_url": detail_url,
                "matched_main_image_url": img_src,
                "matched_shop_name": shop_name,
                "matched_shop_uid": shop_uid,
                "matched_shop_years_text": shop_years,
                "matched_price_text": price_text,
                "matched_price_amount": price_amount,
                "matched_currency": "CNY",
                "matched_sales_text": sales_text,
                "matched_min_order_text": min_order_text,
                "matched_services_json": services,
                "matched_tags_json": tags,
                "matched_attributes_json": attributes,
                "matched_card_raw_json": {
                    "text": text,
                    "html": html,
                },
                "matched_detail_url": detail_url,
            }
        )
    return results


def parse_offer_id(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"offerId=(\d+)", url)
    return m.group(1) if m else None


def parse_score_from_card(html: str) -> float | None:
    m = re.search(r"normalizationScore:([\d.]+)", html)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    m = re.search(r"finalScore:([-\d.]+)", html)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def to_number(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.replace(",", "").strip()
    try:
        if text.endswith("万"):
            return float(text[:-1]) * 10000.0
        return float(re.sub(r"[^0-9.\-]", "", text)) if re.search(r"\d", text) else None
    except ValueError:
        return None


def save_match_row(sku: str, image_path: Path, meta: dict[str, Any], rank: int, item: dict[str, Any], search_info: dict[str, Any]) -> None:
    execute(
        """
        INSERT INTO matched_source_products
          (sku, variant_id, source_image_path, source_image_file, source_image_url, source_image_hash, source_image_size_bytes,
           source_image_width, source_image_height, source_image_mime, search_url, search_image_id, search_image_id_list,
           search_offer_tags, search_sort_type, matched_rank, matched_score, matched_title, matched_offer_id,
           matched_product_url, matched_main_image_url, matched_shop_name, matched_shop_uid, matched_shop_years_text,
           matched_price_text, matched_price_amount, matched_currency, matched_sales_text, matched_min_order_text,
           matched_services_json, matched_tags_json, matched_attributes_json, matched_card_raw_json, matched_detail_raw_json,
           matched_detail_url, match_status, match_reason)
        VALUES
          (%(sku)s, %(variant_id)s, %(source_image_path)s, %(source_image_file)s, %(source_image_url)s, %(source_image_hash)s, %(source_image_size_bytes)s,
           %(source_image_width)s, %(source_image_height)s, %(source_image_mime)s, %(search_url)s, %(search_image_id)s, %(search_image_id_list)s,
           %(search_offer_tags)s, %(search_sort_type)s, %(matched_rank)s, %(matched_score)s, %(matched_title)s, %(matched_offer_id)s,
           %(matched_product_url)s, %(matched_main_image_url)s, %(matched_shop_name)s, %(matched_shop_uid)s, %(matched_shop_years_text)s,
           %(matched_price_text)s, %(matched_price_amount)s, %(matched_currency)s, %(matched_sales_text)s, %(matched_min_order_text)s,
           %(matched_services_json)s, %(matched_tags_json)s, %(matched_attributes_json)s, %(matched_card_raw_json)s, %(matched_detail_raw_json)s,
           %(matched_detail_url)s, %(match_status)s, %(match_reason)s)
        ON DUPLICATE KEY UPDATE
          source_image_path=VALUES(source_image_path),
          source_image_file=VALUES(source_image_file),
          source_image_url=VALUES(source_image_url),
          source_image_hash=VALUES(source_image_hash),
          source_image_size_bytes=VALUES(source_image_size_bytes),
          source_image_width=VALUES(source_image_width),
          source_image_height=VALUES(source_image_height),
          source_image_mime=VALUES(source_image_mime),
          search_url=VALUES(search_url),
          search_image_id=VALUES(search_image_id),
          search_image_id_list=VALUES(search_image_id_list),
          search_offer_tags=VALUES(search_offer_tags),
          search_sort_type=VALUES(search_sort_type),
          matched_score=VALUES(matched_score),
          matched_title=VALUES(matched_title),
          matched_offer_id=VALUES(matched_offer_id),
          matched_product_url=VALUES(matched_product_url),
          matched_main_image_url=VALUES(matched_main_image_url),
          matched_shop_name=VALUES(matched_shop_name),
          matched_shop_uid=VALUES(matched_shop_uid),
          matched_shop_years_text=VALUES(matched_shop_years_text),
          matched_price_text=VALUES(matched_price_text),
          matched_price_amount=VALUES(matched_price_amount),
          matched_currency=VALUES(matched_currency),
          matched_sales_text=VALUES(matched_sales_text),
          matched_min_order_text=VALUES(matched_min_order_text),
          matched_services_json=VALUES(matched_services_json),
          matched_tags_json=VALUES(matched_tags_json),
          matched_attributes_json=VALUES(matched_attributes_json),
          matched_card_raw_json=VALUES(matched_card_raw_json),
          matched_detail_raw_json=VALUES(matched_detail_raw_json),
          matched_detail_url=VALUES(matched_detail_url),
          match_status=VALUES(match_status),
          match_reason=VALUES(match_reason),
          updated_at=CURRENT_TIMESTAMP
        """,
        {
            "sku": sku,
            "variant_id": None,
            "source_image_path": str(image_path),
            "source_image_file": image_path.name,
            "source_image_url": meta.get("source_image_url"),
            "source_image_hash": meta["hash"],
            "source_image_size_bytes": meta["size_bytes"],
            "source_image_width": meta["width"],
            "source_image_height": meta["height"],
            "source_image_mime": meta["mime"],
            "search_url": search_info.get("search_url"),
            "search_image_id": search_info.get("image_id"),
            "search_image_id_list": search_info.get("image_id_list"),
            "search_offer_tags": settings.target_offer_tags,
            "search_sort_type": settings.target_sort_type,
            "matched_rank": rank,
            "matched_score": item.get("matched_score"),
            "matched_title": item.get("matched_title"),
            "matched_offer_id": item.get("matched_offer_id"),
            "matched_product_url": item.get("matched_product_url"),
            "matched_main_image_url": item.get("matched_main_image_url"),
            "matched_shop_name": item.get("matched_shop_name"),
            "matched_shop_uid": item.get("matched_shop_uid"),
            "matched_shop_years_text": item.get("matched_shop_years_text"),
            "matched_price_text": item.get("matched_price_text"),
            "matched_price_amount": item.get("matched_price_amount"),
            "matched_currency": item.get("matched_currency"),
            "matched_sales_text": item.get("matched_sales_text"),
            "matched_min_order_text": item.get("matched_min_order_text"),
            "matched_services_json": safe_json(item.get("matched_services_json"), []),
            "matched_tags_json": safe_json(item.get("matched_tags_json"), []),
            "matched_attributes_json": safe_json(item.get("matched_attributes_json"), []),
            "matched_card_raw_json": safe_json(item.get("matched_card_raw_json"), {}),
            "matched_detail_raw_json": safe_json(item.get("matched_detail_raw_json"), {}),
            "matched_detail_url": item.get("matched_detail_url"),
            "match_status": "matched" if item.get("matched_offer_id") else "no_result",
            "match_reason": None if item.get("matched_offer_id") else "no offerId from result card",
        },
    )


def find_images(input_dir: Path) -> list[Path]:
    files = [p for p in input_dir.iterdir() if p.is_file() and not p.name.startswith(".")]
    sku_files = [p for p in files if p.stem.lower().startswith("sku")]
    if sku_files:
        return sorted(sku_files)
    return sorted(files)


def match_one(page, sku: str, image_path: Path) -> list[dict[str, Any]]:
    meta = image_meta(image_path)
    upload_info = upload_image_and_open_search(page, image_path)
    cards = extract_offer_cards(page)
    for idx, item in enumerate(cards, 1):
        save_match_row(sku, image_path, meta | {"source_image_url": None}, idx, item, upload_info)
    return cards


def ensure_sql() -> None:
    sql_path = ROOT_DIR / "sql" / "001_init.sql"
    if sql_path.exists():
        run_sql_file(sql_path)


def sync_images() -> list[Path]:
    download_dir = Path(settings.download_dir)
    local_input_dir = Path(settings.input_image_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    remote_downloads = download_remote_source_images(download_dir, limit=settings.source_image_table_limit)
    local_copies: list[Path] = []
    if local_input_dir.exists():
        local_copies = ensure_image_downloads(local_input_dir, download_dir)
    merged: dict[str, Path] = {}
    for path in remote_downloads + local_copies:
        merged[path.name] = path
    if merged:
        return [merged[name] for name in sorted(merged)]
    return find_images(download_dir)


def main() -> int:
    ensure_sql()
    download_dir = Path(settings.download_dir)
    result_dir = Path(settings.result_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)

    run_key = hashlib.sha1(
        f"{settings.input_image_dir}|{settings.download_dir}|{settings.target_offer_tags}|{settings.target_sort_type}|{datetime.now().isoformat()}".encode(
            "utf-8"
        )
    ).hexdigest()
    upsert_run(run_key, str(Path(settings.input_image_dir)), str(download_dir))

    images = sync_images()
    if not images:
        finish_run(run_key, status="failed", source_count=0, matched_count=0, failed_count=0, error_message="no source images found")
        print("no source images found")
        return 1

    matched_count = 0
    failed_count = 0

    with sync_playwright() as p:
        browser_args = ["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"]
        browser_kwargs: dict[str, Any] = {
            "headless": settings.chrome_headless,
            "args": browser_args,
        }
        if settings.chrome_executable_path:
            browser_kwargs["executable_path"] = settings.chrome_executable_path
        else:
            browser_kwargs["channel"] = settings.chrome_channel
        if settings.chrome_proxy_server:
            browser_kwargs["proxy"] = {"server": settings.chrome_proxy_server}

        browser = p.chromium.launch_persistent_context(str(Path(settings.chrome_profile_dir)), **browser_kwargs)
        try:
            page = browser.pages[0] if browser.pages else browser.new_page()
            for src in images:
                sku = src.stem
                try:
                    page.goto(settings.search_page_base, wait_until="domcontentloaded", timeout=settings.search_wait_timeout_ms)
                    page.wait_for_timeout(1500)
                    cards = match_one(page, sku, src)
                    matched_count += 1 if cards else 0
                    print(f"OK {sku}: {len(cards)} cards")
                except Exception as exc:
                    failed_count += 1
                    execute(
                        """
                        INSERT INTO matched_source_products
                          (sku, source_image_path, source_image_file, source_image_hash, match_status, match_reason)
                        VALUES
                          (%(sku)s, %(source_image_path)s, %(source_image_file)s, %(source_image_hash)s, 'error', %(match_reason)s)
                        ON DUPLICATE KEY UPDATE
                          match_status='error',
                          match_reason=VALUES(match_reason),
                          updated_at=CURRENT_TIMESTAMP
                        """,
                        {
                            "sku": sku,
                            "source_image_path": str(src),
                            "source_image_file": src.name,
                            "source_image_hash": image_hash(src),
                            "match_reason": str(exc),
                        },
                    )
                    print(f"FAIL {sku}: {exc}")
            finish_run(run_key, status="success", source_count=len(images), matched_count=matched_count, failed_count=failed_count)
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
