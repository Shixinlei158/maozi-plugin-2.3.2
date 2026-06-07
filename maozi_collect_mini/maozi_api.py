"""毛子ERP API客户端 —— 精简版。

保留与 ozon_selection_pipeline 完全一致的API调用方式。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

from .config import settings


class MaoziClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token if token is not None else settings.maozi_token
        self.session = requests.Session()
        self.session.trust_env = False

    def sku3(self, sku: str) -> dict[str, Any]:
        """调用毛子ERP的SKU3接口获取商品详细指标"""
        if not self.token:
            raise RuntimeError("MAOZI_TOKEN is empty. Put it in settings.json first.")
        url = f"{settings.maozi_base_url.rstrip('/')}/api.chrome/sku3"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Client": "plugin",
            "Plugin-Version": settings.maozi_plugin_version,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        }
        response = self.session.post(
            url,
            params={"sku": sku},
            json={"sku": str(sku)},
            headers=headers,
            timeout=settings.request_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def sku3_batch(
        self, skus: list[str], concurrency: int = 12
    ) -> dict[str, dict[str, Any]]:
        """批量调用SKU3接口"""
        normalized: list[str] = []
        seen: set[str] = set()
        for s in skus:
            text = str(s).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        if not normalized:
            return {}

        results: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        worker_total = max(1, min(int(concurrency or 1), len(normalized)))

        with ThreadPoolExecutor(max_workers=worker_total) as executor:
            futures = {executor.submit(self.sku3, s): s for s in normalized}
            for future in as_completed(futures):
                sku = futures[future]
                try:
                    results[sku] = future.result()
                except Exception as exc:
                    errors.append(f"{sku}:{_summarize(exc)}")

        if errors and not results:
            raise RuntimeError(
                f"maozi batch failed for all skus: {'; '.join(errors[:3])}"
            )
        return results


def _summarize(exc: Exception) -> str:
    text = str(exc).strip().replace("\r", " ").replace("\n", " ")
    if len(text) > 180:
        return text[:177] + "..."
    return text or exc.__class__.__name__
