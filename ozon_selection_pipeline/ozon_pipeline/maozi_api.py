from __future__ import annotations

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
