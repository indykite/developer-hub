# Copyright (c) 2026 IndyKite
"""What the console still reads from the Intent Agent directly: its card, its health, its metrics.

Prompts no longer go to the agent from here; they go to a gateway, which asks the agent itself
(see `app.gateway`). The shared constants are the A2A version header and the card's path.
"""

from __future__ import annotations

from typing import Any

import httpx

A2A_VERSION = "1.0"
WELL_KNOWN_AGENT_CARD = "/.well-known/agent-card.json"


class IntentAgentClient:
    """HTTP client for one Intent Agent: the card on the JSON-RPC listener, health and metrics on the other."""

    def __init__(self, base_url: str, health_url: str, timeout: float, client: httpx.AsyncClient | None = None) -> None:
        """`base_url` serves A2A, `health_url` serves /health and /metrics; `client` is the shared pool when given."""
        self._base = base_url.rstrip("/")
        self._health = health_url.rstrip("/")
        self._timeout = timeout
        self._client = client

    @property
    def rpc_url(self) -> str:
        """The JSON-RPC endpoint: the agent serves it at `/`."""
        return f"{self._base}/"

    async def _get(self, url: str, wait_seconds: float) -> httpx.Response:
        if self._client is not None:
            return await self._client.get(url, timeout=wait_seconds)
        async with httpx.AsyncClient(timeout=wait_seconds) as client:
            return await client.get(url)

    async def agent_card(self) -> dict[str, Any]:
        """Fetch the public agent card (skills, security schemes); no token needed."""
        response = await self._get(f"{self._base}{WELL_KNOWN_AGENT_CARD}", self._timeout)
        response.raise_for_status()
        return response.json()

    async def health(self) -> bool:
        """Return True when /health answers 200; any transport failure counts as down."""
        try:
            response = await self._get(f"{self._health}/health", 3.0)
        except httpx.HTTPError:
            return False
        return response.status_code == httpx.codes.OK

    async def metrics_text(self) -> str:
        """Raw Prometheus exposition from the health listener."""
        response = await self._get(f"{self._health}/metrics", 3.0)
        response.raise_for_status()
        return response.text


def summarize_metrics(text: str) -> dict[str, Any]:
    """Reduce the Prometheus exposition to the two numbers the dashboard shows."""
    total = 0.0
    duration_sum = 0.0
    duration_count = 0.0
    by_status: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, rest = line.partition(" ")
        try:
            value = float(rest.strip())
        except ValueError:
            continue
        if name.startswith("http_requests_total"):
            total += value
            status = _label(name, "status")
            if status:
                by_status[status] = by_status.get(status, 0.0) + value
        elif name.startswith("http_request_duration_seconds_sum"):
            duration_sum += value
        elif name.startswith("http_request_duration_seconds_count"):
            duration_count += value
    avg_ms = int(duration_sum / duration_count * 1000) if duration_count else None
    return {"requests_total": int(total), "by_status": by_status, "avg_latency_ms": avg_ms}


def _label(series: str, key: str) -> str | None:
    start = series.find(f'{key}="')
    if start < 0:
        return None
    start += len(key) + 2
    end = series.find('"', start)
    return series[start:end] if end > start else None
