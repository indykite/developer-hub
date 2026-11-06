# Copyright (c) 2026 IndyKite
"""Client for the IndyKite Token Service's RFC 8693 exchange endpoint."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from app.issuer import decode_unverified

# RFC 8693 identifiers for the grant and the token type of every token in the exchange.
URN_GRANT_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
URN_TYPE_ACCESS = "urn:ietf:params:oauth:token-type:access_token"


class TokenExchangeError(Exception):
    """The Token Service refused the exchange (RFC 6749 error body) or could not be reached."""

    def __init__(self, status: int | None, body: object) -> None:
        """`status` is None when the request never got an HTTP answer."""
        self.status = status
        self.body = body
        super().__init__(f"token exchange failed: {status} {body}")


@dataclass
class Exchange:
    access_token: str
    expires_in: int | None
    issued_token_type: str | None
    claims: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0


class TokenServiceClient:
    """The console's side of the Token Service: it exchanges, the Intent Agent introspects."""

    def __init__(self, base_url: str, client_id: str, client_secret: str, timeout: float) -> None:
        """Keep the client pair equal to `idp.client_auth` in token-service.yaml."""
        self._base = base_url.rstrip("/")
        self._auth = (client_id, client_secret)
        self._timeout = timeout

    @property
    def exchange_url(self) -> str:
        """RFC 8693 token endpoint."""
        return f"{self._base}/oauth2/token"

    @property
    def introspect_url(self) -> str:
        """RFC 7662 endpoint the Intent Agent is pointed at."""
        return f"{self._base}/oauth2/introspect"

    async def exchange(self, *, subject_token: str, actor_token: str, audience: str) -> Exchange:
        """Delegate `subject_token` to the actor for `audience`; returns the signed delegation token."""
        form = {
            "grant_type": URN_GRANT_EXCHANGE,
            "subject_token": subject_token,
            "subject_token_type": URN_TYPE_ACCESS,
            "actor_token": actor_token,
            "actor_token_type": URN_TYPE_ACCESS,
            "requested_token_type": URN_TYPE_ACCESS,
            "audience": audience,
        }
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self.exchange_url, data=form, auth=self._auth)
        except httpx.HTTPError as exc:
            raise TokenExchangeError(None, str(exc)) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if response.status_code != httpx.codes.OK or not isinstance(body, dict) or "access_token" not in body:
            raise TokenExchangeError(response.status_code, body)
        token = body["access_token"]
        return Exchange(
            access_token=token,
            expires_in=body.get("expires_in"),
            issued_token_type=body.get("issued_token_type"),
            claims=decode_unverified(token),
            latency_ms=latency_ms,
        )

    async def openid_configuration(self) -> dict[str, Any]:
        """Fetch the discovery document; doubles as the liveness check."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(f"{self._base}/.well-known/openid-configuration")
        response.raise_for_status()
        return response.json()

    async def jwks(self) -> dict[str, Any]:
        """Fetch the published signing keys (public halves only)."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(f"{self._base}/.well-known/jwks.json")
        response.raise_for_status()
        return response.json()
