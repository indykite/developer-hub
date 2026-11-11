# Copyright (c) 2026 IndyKite
"""auth0-bridge tests: local RSA key, stubbed JWKS lookup and Auth0 token endpoint."""

from __future__ import annotations

import asyncio
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
    """Mint an Auth0-shaped RS256 access token signed with the test key; overrides replace claims."""
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
    """HTTP Basic authorization header for the given client credentials."""
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
    server._tokens.clear()
    server._token_locks.clear()
    return calls


@pytest.fixture
def client():
    """Test client bound to the bridge application."""
    return TestClient(server.app)


def test_active_token(client, auth0):
    """A valid tenant token introspects as active with its claims and client_id taken from azp."""
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
    """An expired token answers active=false, still with status 200 as RFC 7662 requires."""
    expired = _token(exp=int(time.time()) - 5)
    resp = client.post("/oauth/introspect", data={"token": expired}, headers=_basic(*GOOD_CLIENT))
    assert resp.status_code == 200
    assert resp.json() == {"active": False}


def test_foreign_issuer_is_inactive(client, auth0):
    """A token issued by another issuer is not vouched for."""
    foreign = _token(iss="https://somebody-else.example/")
    resp = client.post("/oauth/introspect", data={"token": foreign}, headers=_basic(*GOOD_CLIENT))
    assert resp.json() == {"active": False}


def test_garbage_token_is_inactive(client, auth0):
    """A string that is not a JWT answers active=false instead of an error."""
    resp = client.post("/oauth/introspect", data={"token": "not.a.jwt"}, headers=_basic(*GOOD_CLIENT))  # nosec B105
    assert resp.json() == {"active": False}


def test_caller_must_authenticate(client, auth0):
    """Introspection without credentials, or with a wrong secret, is refused with 401 and a Basic challenge."""
    assert client.post("/oauth/introspect", data={"token": _token()}).status_code == 401
    bad = client.post("/oauth/introspect", data={"token": _token()}, headers=_basic("m2m-client", "wrong"))
    assert bad.status_code == 401
    assert bad.headers["WWW-Authenticate"].startswith("Basic")


def test_caller_verification_is_cached(client, auth0):
    """Repeated calls by one caller verify its credentials at Auth0 once, with the default audience."""
    for _ in range(3):
        client.post("/oauth/introspect", data={"token": _token()}, headers=_basic(*GOOD_CLIENT))
    verifications = [c for c in auth0 if c["form"].get("grant_type") == "client_credentials"]
    assert len(verifications) == 1
    assert verifications[0]["form"]["audience"] == "ibac-agents"


def test_form_credentials_accepted(client, auth0):
    """Client credentials may be sent in the form body instead of a Basic header."""
    resp = client.post(
        "/oauth/introspect",
        data={"token": _token(), "client_id": GOOD_CLIENT[0], "client_secret": GOOD_CLIENT[1]},
    )
    assert resp.json()["active"] is True


def test_token_passthrough_adds_default_audience(client, auth0):
    """A token request without audience is forwarded with the default audience and the caller's header."""
    resp = client.post("/oauth/token", data={"grant_type": "client_credentials"}, headers=_basic(*GOOD_CLIENT))
    assert resp.status_code == 200
    assert resp.json()["access_token"]
    assert auth0[-1]["form"]["audience"] == "ibac-agents"
    assert auth0[-1]["headers"]["Authorization"] == _basic(*GOOD_CLIENT)["Authorization"]


def test_token_passthrough_keeps_explicit_audience(client, auth0):
    """An audience named by the caller is forwarded unchanged."""
    client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials", "audience": "intent_agent"},
        headers=_basic(*GOOD_CLIENT),
    )
    assert auth0[-1]["form"]["audience"] == "intent_agent"


def test_token_passthrough_returns_upstream_error(client, auth0):
    """Auth0's status and error body are relayed to the caller as they are."""
    resp = client.post("/oauth/token", data={"grant_type": "client_credentials"}, headers=_basic("x", "y"))
    assert resp.status_code == 401
    assert resp.json()["error"] == "access_denied"


def test_healthz(client):
    """The health endpoint reports the issuer the bridge was configured with."""
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["issuer"] == "https://tenant.test.auth0.com/"


def _grants(calls):
    """The client-credentials grants that reached Auth0."""
    return [c for c in calls if c["form"].get("grant_type") == "client_credentials"]


def test_client_credentials_token_is_reused(client, auth0):
    """The same application asking twice for the same audience gets the kept token: one grant at Auth0."""
    first = client.post("/oauth/token", data={"grant_type": "client_credentials"}, headers=_basic(*GOOD_CLIENT))
    second = client.post("/oauth/token", data={"grant_type": "client_credentials"}, headers=_basic(*GOOD_CLIENT))
    assert second.status_code == 200
    assert second.json()["access_token"] == first.json()["access_token"]
    assert 0 < second.json()["expires_in"] <= 86400
    assert len(_grants(auth0)) == 1


def test_each_audience_has_its_own_token(client, auth0):
    """Another audience is another token, asked of Auth0 once."""
    for audience in ("ibac-agents", "intent_agent", "intent_agent"):
        client.post(
            "/oauth/token",
            data={"grant_type": "client_credentials", "audience": audience},
            headers=_basic(*GOOD_CLIENT),
        )
    assert [c["form"]["audience"] for c in _grants(auth0)] == ["ibac-agents", "intent_agent"]


def test_introspection_caller_check_reuses_the_callers_token(client, auth0):
    """A caller that already got its default-audience token is checked against it, without a new grant."""
    client.post("/oauth/token", data={"grant_type": "client_credentials"}, headers=_basic(*GOOD_CLIENT))
    resp = client.post("/oauth/introspect", data={"token": _token()}, headers=_basic(*GOOD_CLIENT))
    assert resp.json()["active"] is True
    assert len(_grants(auth0)) == 1


def test_kept_token_needs_the_same_secret(client, auth0):
    """A kept token is never handed to a caller with another secret: Auth0 is asked, and refuses."""
    client.post("/oauth/token", data={"grant_type": "client_credentials"}, headers=_basic(*GOOD_CLIENT))
    resp = client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials"},
        headers=_basic(GOOD_CLIENT[0], "wrong"),
    )
    assert resp.status_code == 401
    assert len(_grants(auth0)) == 2


def test_token_close_to_expiry_is_asked_again(client, auth0, monkeypatch):
    """Once a kept token is inside the margin before its expiry, Auth0 issues a new one."""
    client.post("/oauth/token", data={"grant_type": "client_credentials"}, headers=_basic(*GOOD_CLIENT))
    monkeypatch.setattr(server, "TOKEN_CACHE_MIN_REMAINING", 86400)
    client.post("/oauth/token", data={"grant_type": "client_credentials"}, headers=_basic(*GOOD_CLIENT))
    assert len(_grants(auth0)) == 2


def test_another_scope_is_another_token(client, auth0):
    """Every parameter of the grant is part of what is kept: another scope asks Auth0 again."""
    for scope in ("read", "write", "read"):
        client.post(
            "/oauth/token",
            data={"grant_type": "client_credentials", "scope": scope},
            headers=_basic(*GOOD_CLIENT),
        )
    assert [c["form"]["scope"] for c in _grants(auth0)] == ["read", "write"]


def test_two_different_credentials_in_one_request_are_never_kept(client, auth0):
    """A Basic header for one application and form credentials for another: forwarded, nothing kept."""
    for _ in range(2):
        client.post(
            "/oauth/token",
            data={"grant_type": "client_credentials", "client_id": "other", "client_secret": "pair"},  # nosec B105
            headers=_basic(*GOOD_CLIENT),
        )
    assert len(_grants(auth0)) == 2
    assert not server._tokens


def test_refused_credentials_leave_nothing_behind(client, auth0):
    """A refusal keeps neither a token nor a lock, so wrong credentials cannot fill the memory."""
    client.post("/oauth/token", data={"grant_type": "client_credentials"}, headers=_basic("x", "y"))
    assert not server._tokens
    assert not server._token_locks


def test_failed_auth0_call_leaves_no_lock(auth0, monkeypatch):
    """A call to Auth0 that raises keeps neither a token nor a lock."""

    async def broken_post(form, headers):
        raise ConnectionError

    monkeypatch.setattr(server, "_post_auth0", broken_post)
    with pytest.raises(ConnectionError):
        asyncio.run(server._client_credentials("x", "y", {"grant_type": "client_credentials"}, {}))
    assert not server._tokens
    assert not server._token_locks


def test_cancelled_caller_leaves_no_lock_and_waiters_stay_in_line(auth0, monkeypatch):
    """A cancelled call releases its place; a caller waiting for the same grant is still answered."""
    release = asyncio.Event()
    asked = 0

    async def slow_post(form, headers):
        nonlocal asked
        asked += 1
        await release.wait()
        return 200, {"access_token": "t", "token_type": "Bearer", "expires_in": 86400}  # nosec B105

    async def scenario():
        grant = ("x", "y", {"grant_type": "client_credentials"}, {})
        first = asyncio.create_task(server._client_credentials(*grant))
        await asyncio.sleep(0)
        second = asyncio.create_task(server._client_credentials(*grant))
        await asyncio.sleep(0)
        assert asked == 1  # the second waits for the first
        first.cancel()
        release.set()
        return await second

    monkeypatch.setattr(server, "_post_auth0", slow_post)
    status, _ = asyncio.run(scenario())
    assert status == 200
    assert asked == 2
    assert not server._token_locks


def test_lock_is_released_after_a_kept_token(client, auth0):
    """A kept token needs no lock between requests."""
    client.post("/oauth/token", data={"grant_type": "client_credentials"}, headers=_basic(*GOOD_CLIENT))
    assert len(server._tokens) == 1
    assert not server._token_locks


def test_expired_tokens_are_dropped(client, auth0, monkeypatch):
    """A token past its expiry is removed when the next one is kept."""
    form = {"grant_type": "client_credentials", "scope": "old"}
    client.post("/oauth/token", data=form, headers=_basic(*GOOD_CLIENT))
    (old_key,) = server._tokens
    server._tokens[old_key] = (server._tokens[old_key][0], time.monotonic() - 1)
    form = {"grant_type": "client_credentials", "scope": "new"}
    client.post("/oauth/token", data=form, headers=_basic(*GOOD_CLIENT))
    assert old_key not in server._tokens
    assert len(server._tokens) == 1


def test_kept_tokens_are_bounded(client, auth0, monkeypatch):
    """Beyond TOKEN_CACHE_MAX_ENTRIES a new token replaces the one closest to its expiry."""
    monkeypatch.setattr(server, "TOKEN_CACHE_MAX_ENTRIES", 2)
    for scope in ("a", "b", "c"):
        form = {"grant_type": "client_credentials", "scope": scope}
        client.post("/oauth/token", data=form, headers=_basic(*GOOD_CLIENT))
    kept_scopes = sorted(dict(key[2])["scope"] for key in server._tokens)
    assert kept_scopes == ["b", "c"]
    assert not server._token_locks
