import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ozon_pipeline.db import fetch_all

OUTPUT_DIR = Path(r"C:\project\maozi-plugin-2.3.2\ozon_pic")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def get_image_extension(url: str) -> str:
    ext = Path(url).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ext
    return ".jpg"


def download_images():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = fetch_all("SELECT sku, main_image_url FROM sku_products WHERE main_image_url IS NOT NULL AND main_image_url != ''")
    print(f"共 {len(rows)} 条记录")

    success = 0
    failed = 0
    skipped = 0

    for i, row in enumerate(rows, 1):
        sku = row["sku"]
        url = row["main_image_url"]

        ext = get_image_extension(url)
        file_path = OUTPUT_DIR / f"{sku}{ext}"

        if file_path.exists():
            skipped += 1
            continue

        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            file_path.write_bytes(resp.content)
            success += 1
            print(f"[{i}/{len(rows)}] OK: {sku}")
        except Exception as e:
            failed += 1
            print(f"[{i}/{len(rows)}] FAIL: {sku} - {e}")

    print(f"\n完成: 成功 {success}, 失败 {failed}, 跳过 {skipped}")


if __name__ == "__main__":
    download_images()