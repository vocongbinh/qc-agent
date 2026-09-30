"""Toxiproxy HTTP API client – giả lập network fault (latency, timeout, reset).

Toxiproxy API mặc định: http://localhost:8474
Docs: https://github.com/Shopify/toxiproxy
"""

from __future__ import annotations

from typing import Any

import httpx

from config.settings import settings


class ToxiproxyClient:
    def __init__(self, api_url: str | None = None):
        self.api_url = (api_url or getattr(settings, "toxiproxy_api_url", None) or "http://localhost:8474").rstrip("/")

    def available(self) -> bool:
        try:
            r = httpx.get(f"{self.api_url}/version", timeout=3.0)
            return r.status_code == 200
        except Exception:
            return False

    def list_proxies(self) -> list[dict]:
        r = httpx.get(f"{self.api_url}/proxies", timeout=5.0)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            return list(data.values())
        return data

    def ensure_proxy(
        self,
        name: str,
        listen: str,
        upstream: str,
        enabled: bool = True,
    ) -> dict:
        """Tạo proxy nếu chưa có. listen/upstream ví dụ: 0.0.0.0:18080, host.docker.internal:8000"""
        try:
            r = httpx.get(f"{self.api_url}/proxies/{name}", timeout=5.0)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass

        payload = {
            "name": name,
            "listen": listen,
            "upstream": upstream,
            "enabled": enabled,
        }
        r = httpx.post(f"{self.api_url}/proxies", json=payload, timeout=5.0)
        r.raise_for_status()
        return r.json()

    def delete_proxy(self, name: str) -> None:
        httpx.delete(f"{self.api_url}/proxies/{name}", timeout=5.0)

    def reset_proxy(self, name: str) -> None:
        """Xóa mọi toxic trên proxy."""
        httpx.post(f"{self.api_url}/reset", timeout=5.0)

    def add_latency(
        self,
        proxy: str,
        latency_ms: int = 2000,
        jitter_ms: int = 100,
        toxic_name: str = "latency",
        stream: str = "downstream",
    ) -> dict:
        payload = {
            "name": toxic_name,
            "type": "latency",
            "stream": stream,
            "toxicity": 1.0,
            "attributes": {
                "latency": latency_ms,
                "jitter": jitter_ms,
            },
        }
        r = httpx.post(f"{self.api_url}/proxies/{proxy}/toxics", json=payload, timeout=5.0)
        r.raise_for_status()
        return r.json()

    def add_timeout(
        self,
        proxy: str,
        timeout_ms: int = 1000,
        toxic_name: str = "timeout",
        stream: str = "downstream",
    ) -> dict:
        payload = {
            "name": toxic_name,
            "type": "timeout",
            "stream": stream,
            "toxicity": 1.0,
            "attributes": {"timeout": timeout_ms},
        }
        r = httpx.post(f"{self.api_url}/proxies/{proxy}/toxics", json=payload, timeout=5.0)
        r.raise_for_status()
        return r.json()

    def add_reset_peer(
        self,
        proxy: str,
        toxic_name: str = "reset_peer",
        stream: str = "downstream",
    ) -> dict:
        payload = {
            "name": toxic_name,
            "type": "reset_peer",
            "stream": stream,
            "toxicity": 1.0,
            "attributes": {"timeout": 0},
        }
        r = httpx.post(f"{self.api_url}/proxies/{proxy}/toxics", json=payload, timeout=5.0)
        r.raise_for_status()
        return r.json()

    def remove_toxic(self, proxy: str, toxic_name: str) -> None:
        httpx.delete(f"{self.api_url}/proxies/{proxy}/toxics/{toxic_name}", timeout=5.0)

    def disable_proxy(self, name: str) -> dict:
        r = httpx.post(f"{self.api_url}/proxies/{name}", json={"enabled": False}, timeout=5.0)
        r.raise_for_status()
        return r.json()

    def enable_proxy(self, name: str) -> dict:
        r = httpx.post(f"{self.api_url}/proxies/{name}", json={"enabled": True}, timeout=5.0)
        r.raise_for_status()
        return r.json()
