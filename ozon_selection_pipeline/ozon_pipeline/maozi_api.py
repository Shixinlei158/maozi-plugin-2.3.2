from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

from .config import settings


class MaoziClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token if token is not None else settings.maozi_token
        self.session = requests.Session()
        self.session.trust_env = settings.requests_trust_env
        if settings.http_proxy_url:
            self.session.proxies["http"] = settings.http_proxy_url
        if settings.https_proxy_url:
            self.session.proxies["https"] = settings.https_proxy_url

    def sku3(self, sku: str) -> dict[str, Any]:
        if not self.token:
            raise RuntimeError("MAOZI_TOKEN is empty. Put it in .env first.")
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

    def sku3_batch(self, skus: list[str], concurrency: int = 12) -> dict[str, dict[str, Any]]:
        normalized: list[str] = []
        seen: set[str] = set()
        for sku in skus:
            text = str(sku).strip()
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
            futures = {executor.submit(self.sku3, sku): sku for sku in normalized}
            for future in as_completed(futures):
                sku = futures[future]
                try:
                    results[sku] = future.result()
                except Exception as exc:
                    errors.append(f"{sku}:{_summarize_exception(exc)}")

        if errors and not results:
            raise RuntimeError(f"maozi direct batch failed for all skus: {'; '.join(errors[:3])}")
        return results


def _summarize_exception(exc: Exception) -> str:
    text = str(exc).strip().replace("\r", " ").replace("\n", " ")
    if len(text) > 180:
        return text[:177] + "..."
    return text or exc.__class__.__name__
