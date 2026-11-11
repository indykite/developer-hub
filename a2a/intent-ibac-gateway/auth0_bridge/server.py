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
  credentials are checked by a client-credentials grant at Auth0, the same
  kept token as the caller's own, so every machine-to-machine application of the tenant may
  introspect and nothing is listed here.
- ``POST /oauth/token`` forwards the request to Auth0 unchanged, adding
  ``audience=AUTH0_DEFAULT_AUDIENCE`` when the caller sent none: Auth0 refuses
  a client-credentials grant without an audience and the gateway sends none
  for its actor token.

A client-credentials token is kept in memory, per application and audience,
and handed back until shortly before it expires: a caller that restarts, and
the check of an introspection caller, cost no new token at Auth0.

The audience of an introspected token is not enforced here: the gateway and
the Intent Agent check it against their own configuration.
"""

from __future__ import annotations

import asyncio
import base64
import functools
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
# A kept client-credentials token is handed back until this many seconds
# before it expires; then Auth0 is asked for a new one.
TOKEN_CACHE_MIN_REMAINING = int(os.getenv("TOKEN_CACHE_MIN_REMAINING", "300"))
BRIDGE_PORT = int(os.getenv("BRIDGE_PORT", "8090"))
UPSTREAM_TIMEOUT = float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "10"))
ALGORITHMS = ["RS256"]

app = FastAPI(title="auth0-bridge", docs_url=None, redoc_url=None)

# (client_id, sha256 of the secret, the grant's other parameters) -> (Auth0's answer, monotonic
# time it expires at). In memory only: a restart of the bridge forgets every token.
TokenKey = tuple[str, str, tuple[tuple[str, str], ...]]
_tokens: dict[TokenKey, tuple[dict, float]] = {}
_token_locks: dict[TokenKey, asyncio.Lock] = {}


@functools.lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    """One JWKS client per process; it caches the tenant's keys for an hour."""
    return PyJWKClient(JWKS_URI, cache_keys=True, lifespan=3600)


async def _post_auth0(form: dict[str, str], headers: dict[str, str]) -> tuple[int, object]:
    """POST a form to the Auth0 token endpoint; returns (status, parsed JSON or text)."""
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
        resp = await client.post(TOKEN_ENDPOINT, data=form, headers=headers)
    try:
        body: object = resp.json()
    except ValueError:
        body = resp.text
    return resp.status_code, body


def _signing_key_for(jwt_text: str):
    """Return the tenant key that signed this JWT, by its kid."""
    return _jwks_client().get_signing_key_from_jwt(jwt_text).key


def _basic_credentials(request: Request) -> tuple[str, str] | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:].strip()).decode()
    except ValueError:  # bad base64 or bad UTF-8 alike
        return None
    client_id, sep, secret = decoded.partition(":")
    return (client_id, secret) if sep else None


def _caller_credentials(request: Request, form: dict[str, str]) -> tuple[str, str] | None:
    creds = _basic_credentials(request)
    if creds is None and form.get("client_id") and form.get("client_secret"):
        creds = (form["client_id"], form["client_secret"])
    return creds


def _single_credentials(request: Request, form: dict[str, str]) -> tuple[str, str] | None:
    """Return the caller's credentials when the request names one application only.

    A request that carries one pair in the Basic header and another in the form is ambiguous about
    which application Auth0 answers for, so its token is never kept.
    """
    basic = _basic_credentials(request)
    in_form = (
        (form["client_id"], form["client_secret"]) if form.get("client_id") and form.get("client_secret") else None
    )
    if basic and in_form and basic != in_form:
        return None
    if basic and not in_form and (form.get("client_id") or form.get("client_secret")):
        return None
    return basic or in_form


async def _client_credentials(
    client_id: str,
    secret: str,
    form: dict[str, str],
    headers: dict[str, str],
) -> tuple[int, object]:
    """Answer a client-credentials grant from the kept token, or ask Auth0 and keep what it issues.

    The token is kept per application, secret and parameters of the grant (audience, scope, ...), so
    only a caller presenting the same credentials and asking for the same thing gets it back. A
    refusal is never kept.
    """
    asked = tuple(sorted((k, v) for k, v in form.items() if k not in ("client_id", "client_secret")))
    key = (client_id, hashlib.sha256(secret.encode()).hexdigest(), asked)
    async with _token_locks.setdefault(key, asyncio.Lock()):
        kept = _tokens.get(key)
        remaining = kept[1] - time.monotonic() if kept else 0
        if kept and remaining > TOKEN_CACHE_MIN_REMAINING:
            return HTTPStatus.OK, {**kept[0], "expires_in": int(remaining)}
        status, body = await _post_auth0(form, headers)
        if status == HTTPStatus.OK and isinstance(body, dict) and body.get("access_token"):
            lifetime = body.get("expires_in")
            if isinstance(lifetime, (int, float)) and lifetime > TOKEN_CACHE_MIN_REMAINING:
                _tokens[key] = (dict(body), time.monotonic() + lifetime)
            log.info("token issued by Auth0 client=%s audience=%s", client_id, form.get("audience"))
        else:
            # Nothing is kept for refused credentials, not even their lock.
            _tokens.pop(key, None)
            _token_locks.pop(key, None)
        return status, body


async def _caller_is_tenant_client(client_id: str, secret: str) -> bool:
    """Return True when Auth0 accepts these credentials for a client-credentials grant.

    The grant is the one for the default audience, kept like any other: a caller that already
    holds that token, or was checked before, costs no new one.
    """
    form = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret}
    if DEFAULT_AUDIENCE:
        form["audience"] = DEFAULT_AUDIENCE
    status, body = await _client_credentials(client_id, secret, form, {})
    ok = status == HTTPStatus.OK and isinstance(body, dict) and bool(body.get("access_token"))
    if not ok:
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
    """Liveness: the settings the bridge runs with."""
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

    presented = form.get("token", "")
    if not presented:
        return JSONResponse({"error": "invalid_request", "error_description": "token is required"}, status_code=400)
    # token_type_hint is deliberately ignored: Auth0 issues one kind of JWT.
    try:
        # PyJWKClient fetches the tenant's keys with blocking I/O: off the event loop.
        key = await asyncio.to_thread(_signing_key_for, presented)
        claims = jwt.decode(
            presented,
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
    """Pass a token request through to Auth0, adding the default audience when absent.

    A client-credentials grant is answered from the kept token when there is one.
    """
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    if DEFAULT_AUDIENCE and not form.get("audience"):
        form["audience"] = DEFAULT_AUDIENCE
    headers = {}
    if request.headers.get("authorization"):
        headers["Authorization"] = request.headers["authorization"]
    creds = _single_credentials(request, form)
    if form.get("grant_type") == "client_credentials" and creds:
        status, body = await _client_credentials(*creds, form, headers)
    else:
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
