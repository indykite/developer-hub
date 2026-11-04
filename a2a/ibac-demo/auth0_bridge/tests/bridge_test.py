# Copyright (c) 2026 IndyKite
"""auth0-bridge tests: local RSA key, stubbed JWKS lookup and Auth0 token endpoint."""

from __future__ import annotations

import base64
import time

import jwt
import pytest
import server
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_KEY = PRIVATE_KEY.public_key()
GOOD_CLIENT = ("m2m-client", "m2m-secret")


def _token(**overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": server.ISSUER,
        "sub": "auth0|69c410bfb501d3a02bd01ea2",
        "aud": ["ibac-console", "https://tenant.test.auth0.com/userinfo"],
        "iat": now - 10,
        "exp": now + 600,
        "azp": "console-client-id",
        "scope": "openid profile email",
    }
    claims.update(overrides)
    return jwt.encode(claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": "k1"})


def _basic(client_id: str, secret: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()}


@pytest.fixture
def auth0(monkeypatch):
    """Stub Auth0: records every token-endpoint call, accepts GOOD_CLIENT only."""
    calls: list[dict] = []

    async def fake_post(form, headers):
        calls.append({"form": dict(form), "headers": dict(headers)})
        creds = (form.get("client_id"), form.get("client_secret"))
        if headers.get("Authorization", "").startswith("Basic "):
            decoded = base64.b64decode(headers["Authorization"][6:]).decode()
            creds = tuple(decoded.split(":", 1))
        if creds == GOOD_CLIENT:
            return 200, {
                "access_token": _token(sub=f"{GOOD_CLIENT[0]}@clients", aud=form.get("audience")),
                "token_type": "Bearer",  # nosec B105 - OAuth token type
                "expires_in": 86400,
            }
        return 401, {"error": "access_denied", "error_description": "Unauthorized"}

    monkeypatch.setattr(server, "_post_auth0", fake_post)
    monkeypatch.setattr(server, "_signing_key_for", lambda token: PUBLIC_KEY)
    server._verified_clients.clear()
    return calls


@pytest.fixture
def client():
    return TestClient(server.app)


def test_active_token(client, auth0):
    resp = client.post("/oauth/introspect", data={"token": _token()}, headers=_basic(*GOOD_CLIENT))
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is True
    assert body["sub"] == "auth0|69c410bfb501d3a02bd01ea2"
    assert body["aud"] == ["ibac-console", "https://tenant.test.auth0.com/userinfo"]
    assert body["client_id"] == "console-client-id"
    assert body["scope"] == "openid profile email"
    assert body["token_type"] == "Bearer"
    assert isinstance(body["exp"], int)


def test_expired_token_is_inactive(client, auth0):
    expired = _token(exp=int(time.time()) - 5)
    resp = client.post("/oauth/introspect", data={"token": expired}, headers=_basic(*GOOD_CLIENT))
    assert resp.status_code == 200
    assert resp.json() == {"active": False}


def test_foreign_issuer_is_inactive(client, auth0):
    foreign = _token(iss="https://somebody-else.example/")
    resp = client.post("/oauth/introspect", data={"token": foreign}, headers=_basic(*GOOD_CLIENT))
    assert resp.json() == {"active": False}


def test_garbage_token_is_inactive(client, auth0):
    resp = client.post("/oauth/introspect", data={"token": "not.a.jwt"}, headers=_basic(*GOOD_CLIENT))  # nosec B105
    assert resp.json() == {"active": False}


def test_caller_must_authenticate(client, auth0):
    assert client.post("/oauth/introspect", data={"token": _token()}).status_code == 401
    bad = client.post("/oauth/introspect", data={"token": _token()}, headers=_basic("m2m-client", "wrong"))
    assert bad.status_code == 401
    assert bad.headers["WWW-Authenticate"].startswith("Basic")


def test_caller_verification_is_cached(client, auth0):
    for _ in range(3):
        client.post("/oauth/introspect", data={"token": _token()}, headers=_basic(*GOOD_CLIENT))
    verifications = [c for c in auth0 if c["form"].get("grant_type") == "client_credentials"]
    assert len(verifications) == 1
    assert verifications[0]["form"]["audience"] == "ibac-agents"


def test_form_credentials_accepted(client, auth0):
    resp = client.post(
        "/oauth/introspect",
        data={"token": _token(), "client_id": GOOD_CLIENT[0], "client_secret": GOOD_CLIENT[1]},
    )
    assert resp.json()["active"] is True


def test_token_passthrough_adds_default_audience(client, auth0):
    resp = client.post("/oauth/token", data={"grant_type": "client_credentials"}, headers=_basic(*GOOD_CLIENT))
    assert resp.status_code == 200
    assert resp.json()["access_token"]
    assert auth0[-1]["form"]["audience"] == "ibac-agents"
    assert auth0[-1]["headers"]["Authorization"] == _basic(*GOOD_CLIENT)["Authorization"]


def test_token_passthrough_keeps_explicit_audience(client, auth0):
    client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials", "audience": "intent_agent"},
        headers=_basic(*GOOD_CLIENT),
    )
    assert auth0[-1]["form"]["audience"] == "intent_agent"


def test_token_passthrough_returns_upstream_error(client, auth0):
    resp = client.post("/oauth/token", data={"grant_type": "client_credentials"}, headers=_basic("x", "y"))
    assert resp.status_code == 401
    assert resp.json()["error"] == "access_denied"


def test_healthz(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["issuer"] == "https://tenant.test.auth0.com/"
