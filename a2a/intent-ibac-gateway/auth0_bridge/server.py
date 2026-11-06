# Copyright (c) 2026 IndyKite
"""auth0-bridge: RFC 7662 introspection and token-endpoint passthrough for Auth0.

Auth0 publishes no introspection endpoint. The Agent Gateway validates the
user's access token only through one, under the same base URL as the
client-credentials endpoint it gets its actor token from, and the Intent Agent
introspects its bearer the same way. This service is that base URL:

- ``POST /oauth/introspect`` verifies an Auth0 JWT against the tenant JWKS
  (signature, issuer, expiry) and answers it as RFC 7662 describes; a token it
  cannot vouch for answers ``{"active": false}``. The caller authenticates
  with HTTP Basic (or ``client_id``/``client_secret`` in the form): the
  credentials are checked by a client-credentials grant at Auth0 and the
  answer is cached, so every machine-to-machine application of the tenant may
  introspect and nothing is listed here.
- ``POST /oauth/token`` forwards the request to Auth0 unchanged, adding
  ``audience=AUTH0_DEFAULT_AUDIENCE`` when the caller sent none: Auth0 refuses
  a client-credentials grant without an audience and the gateway sends none
  for its actor token.

The audience of an introspected token is not enforced here: the gateway and
the Intent Agent check it against their own configuration.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import time
from http import HTTPStatus

import httpx
import jwt
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from jwt import PyJWKClient

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("auth0-bridge")

# Bare domain or URL in .env: only the host is kept.
AUTH0_DOMAIN = (os.getenv("AUTH0_DOMAIN") or "").strip().removeprefix("https://").removeprefix("http://").strip("/")
ISSUER = f"https://{AUTH0_DOMAIN}/"
TOKEN_ENDPOINT = f"https://{AUTH0_DOMAIN}/oauth/token"
JWKS_URI = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
# Audience added to a client-credentials grant that names none (the gateways'
# actor tokens); the Token Service matches actor tokens on it.
DEFAULT_AUDIENCE = (os.getenv("AUTH0_DEFAULT_AUDIENCE") or "").strip()
# How long a caller's verified client credentials stay accepted without
# asking Auth0 again (seconds).
CLIENT_AUTH_CACHE_TTL = int(os.getenv("CLIENT_AUTH_CACHE_TTL", "600"))
BRIDGE_PORT = int(os.getenv("BRIDGE_PORT", "8090"))
UPSTREAM_TIMEOUT = float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "10"))
ALGORITHMS = ["RS256"]

app = FastAPI(title="auth0-bridge", docs_url=None, redoc_url=None)

_jwks: PyJWKClient | None = None
# client_id -> (sha256 of the secret, verified-until epoch seconds)
_verified_clients: dict[str, tuple[str, float]] = {}


def _jwks_client() -> PyJWKClient:
    global _jwks  # noqa: PLW0603 - one cached JWKS client per process
    if _jwks is None:
        _jwks = PyJWKClient(JWKS_URI, cache_keys=True, lifespan=3600)
    return _jwks


async def _post_auth0(form: dict[str, str], headers: dict[str, str]) -> tuple[int, object]:
    """POST a form to the Auth0 token endpoint; returns (status, parsed JSON or text)."""
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
        resp = await client.post(TOKEN_ENDPOINT, data=form, headers=headers)
    try:
        body: object = resp.json()
    except ValueError:
        body = resp.text
    return resp.status_code, body


def _signing_key_for(token: str):
    return _jwks_client().get_signing_key_from_jwt(token).key


def _basic_credentials(request: Request) -> tuple[str, str] | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:].strip()).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    client_id, sep, secret = decoded.partition(":")
    return (client_id, secret) if sep else None


def _caller_credentials(request: Request, form: dict[str, str]) -> tuple[str, str] | None:
    creds = _basic_credentials(request)
    if creds is None and form.get("client_id") and form.get("client_secret"):
        creds = (form["client_id"], form["client_secret"])
    return creds


async def _caller_is_tenant_client(client_id: str, secret: str) -> bool:
    """Return True when Auth0 accepts these credentials for a client-credentials grant (cached)."""
    digest = hashlib.sha256(secret.encode()).hexdigest()
    cached = _verified_clients.get(client_id)
    now = time.monotonic()
    if cached and cached[0] == digest and cached[1] > now:
        return True
    form = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret}
    if DEFAULT_AUDIENCE:
        form["audience"] = DEFAULT_AUDIENCE
    status, body = await _post_auth0(form, {})
    ok = status == HTTPStatus.OK and isinstance(body, dict) and bool(body.get("access_token"))
    if ok:
        _verified_clients[client_id] = (digest, now + CLIENT_AUTH_CACHE_TTL)
    else:
        _verified_clients.pop(client_id, None)
        log.warning("caller %s rejected by Auth0 (%s)", client_id, status)
    return ok


def _unauthorized(description: str) -> Response:
    return JSONResponse(
        {"error": "invalid_client", "error_description": description},
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="auth0-bridge"'},
    )


def _inactive(reason: str, caller: str) -> JSONResponse:
    log.info("introspect caller=%s active=false reason=%s", caller, reason)
    return JSONResponse({"active": False}, headers={"Cache-Control": "no-store"})


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "issuer": ISSUER, "default_audience": DEFAULT_AUDIENCE}


@app.post("/oauth/introspect")
async def introspect(request: Request) -> Response:
    """RFC 7662 introspection of an Auth0-issued JWT."""
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    creds = _caller_credentials(request, form)
    if creds is None:
        return _unauthorized("client authentication required")
    client_id, secret = creds
    if not await _caller_is_tenant_client(client_id, secret):
        return _unauthorized("client authentication failed")

    token = form.get("token", "")
    if not token:
        return JSONResponse({"error": "invalid_request", "error_description": "token is required"}, status_code=400)
    # token_type_hint is deliberately ignored: Auth0 issues one kind of JWT.
    try:
        # PyJWKClient fetches the tenant's keys with blocking I/O: off the event loop.
        key = await asyncio.to_thread(_signing_key_for, token)
        claims = jwt.decode(
            token,
            key,
            algorithms=ALGORITHMS,
            issuer=ISSUER,
            options={"verify_aud": False, "require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        return _inactive(type(exc).__name__, client_id)
    except Exception:
        log.exception("introspection failed before the token could be judged")
        return _inactive("jwks_unavailable", client_id)

    response = {"active": True, "token_type": "Bearer", **claims}  # nosec B105 - OAuth token type, not a secret
    if "azp" in claims and "client_id" not in claims:
        response["client_id"] = claims["azp"]
    log.info(
        "introspect caller=%s active=true sub=%s aud=%s client_id=%s",
        client_id,
        claims.get("sub"),
        claims.get("aud"),
        response.get("client_id"),
    )
    return JSONResponse(response, headers={"Cache-Control": "no-store"})


@app.post("/oauth/token")
async def token(request: Request) -> Response:
    """Pass a token request through to Auth0, adding the default audience when absent."""
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    if DEFAULT_AUDIENCE and not form.get("audience"):
        form["audience"] = DEFAULT_AUDIENCE
    headers = {}
    if request.headers.get("authorization"):
        headers["Authorization"] = request.headers["authorization"]
    status, body = await _post_auth0(form, headers)
    caller = form.get("client_id") or (_basic_credentials(request) or ("?",))[0]
    log.info(
        "token caller=%s grant=%s audience=%s status=%s",
        caller,
        form.get("grant_type"),
        form.get("audience"),
        status,
    )
    if isinstance(body, dict):
        return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})
    return Response(content=str(body), status_code=status, media_type="text/plain")


if __name__ == "__main__":
    if not AUTH0_DOMAIN:
        msg = "AUTH0_DOMAIN is required (e.g. your-tenant.eu.auth0.com)"
        raise SystemExit(msg)
    log.info("auth0-bridge on :%d, issuer %s, default audience %r", BRIDGE_PORT, ISSUER, DEFAULT_AUDIENCE)
    # Binds all interfaces inside the container; compose publishes no host port for the bridge.
    uvicorn.run(
        app,
        host="0.0.0.0",  # nosec B104  # noqa: S104
        port=BRIDGE_PORT,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
