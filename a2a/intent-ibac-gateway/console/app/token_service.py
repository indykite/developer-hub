# Copyright (c) 2026 IndyKite
"""What the console reads from the IndyKite Token Service: its discovery document and its keys.

The exchanges themselves are the gateways' business (RFC 8693, client `idp.client_auth` in
token-service.yaml); the console only shows the service as it presents itself and probes it for health.
"""

from __future__ import annotations

from typing import Any

import httpx


class TokenServiceClient:
    """Read-only client for one Token Service."""

    def __init__(self, base_url: str, client_id: str, timeout: float, client: httpx.AsyncClient | None = None) -> None:
        """`client_id` is shown on the fleet page; it is the pair every gateway presents."""
        self._base = base_url.rstrip("/")
        self.client_id = client_id
        self._timeout = timeout
        self._client = client

    @property
    def exchange_url(self) -> str:
        """RFC 8693 token endpoint the gateways call."""
        return f"{self._base}/oauth2/token"

    @property
    def introspect_url(self) -> str:
        """RFC 7662 endpoint of the service's own tokens."""
        return f"{self._base}/oauth2/introspect"

    async def _get(self, url: str) -> httpx.Response:
        if self._client is not None:
            return await self._client.get(url, timeout=self._timeout)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.get(url)

    async def openid_configuration(self) -> dict[str, Any]:
        """Fetch the discovery document; doubles as the liveness check."""
        response = await self._get(f"{self._base}/.well-known/openid-configuration")
        response.raise_for_status()
        return response.json()

    async def jwks(self) -> dict[str, Any]:
        """Fetch the published signing keys (public halves only)."""
        response = await self._get(f"{self._base}/.well-known/jwks.json")
        response.raise_for_status()
        return response.json()
