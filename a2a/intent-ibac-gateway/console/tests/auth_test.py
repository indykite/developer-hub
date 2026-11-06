# Copyright (c) 2026 IndyKite
"""The console's Auth0 login: PKCE, the session, the four token modes."""

import base64
import hashlib
import json
import time
import urllib.parse

import jwt
import pytest
from app.auth import (
    LOGIN_COOKIE,
    SESSION_COOKIE,
    Auth0Login,
    LoginError,
    Session,
    bearer_for,
    decode_unverified,
    router,
    tamper,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


def token(**claims):
    """An unsigned-looking JWT (HS256 with a throwaway key): the console never verifies, the gateways do."""
    now = int(time.time())
    return jwt.encode({"iat": now, "exp": now + 600, **claims}, "k", algorithm="HS256")


def login():
    """The console's login against a fake tenant."""
    return Auth0Login(
        domain="tenant.test",
        client_id="console",
        client_secret="s",
        audience="ibac-people",
        public_url="http://localhost:3300",
    )  # nosec B106


def test_tenant_domain_accepts_a_url_too():
    """A tenant written as https://tenant/ in .env still gives the issuer https://tenant/."""
    lg = Auth0Login(
        domain="https://tenant.test/",
        client_id="c",
        client_secret="s",
        audience="a",
        public_url="http://localhost:3300",
    )  # nosec B106
    assert lg.issuer == "https://tenant.test/"
    assert lg.authorize_url().startswith("https://tenant.test/authorize?")


def test_authorize_url_carries_pkce_state_and_the_people_audience():
    """The authorize URL names the redirect, the audience, a state and an S256 challenge of a remembered verifier."""
    lg = login()
    url = lg.authorize_url(login_hint="bambi@stardust.com")
    parsed = urllib.parse.urlparse(url)
    q = dict(urllib.parse.parse_qsl(parsed.query))
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "tenant.test", "/authorize")
    assert q["redirect_uri"] == "http://localhost:3300/auth/callback"
    assert (q["audience"], q["response_type"], q["code_challenge_method"]) == ("ibac-people", "code", "S256")
    assert (q["login_hint"], q["prompt"]) == ("bambi@stardust.com", "login")
    verifier, _ = lg.pending[q["state"]]
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert q["code_challenge"] == expected


@pytest.mark.asyncio
async def test_exchange_refuses_an_unknown_state():
    """A callback whose state the console never issued is refused before any call to the tenant."""
    with pytest.raises(LoginError, match="state"):
        await login().exchange("code", "never-issued")


def test_session_reads_the_person_from_the_tokens():
    """Subject and expiry come from the access token, name and email from the id token."""
    s = Session(
        access_token=token(sub="auth0|1", aud=["ibac-people"]),
        id_token=token(sub="auth0|1", name="Bambi Stardust", email="bambi@stardust.com"),
    )
    assert (s.sub, s.name, s.email) == ("auth0|1", "Bambi Stardust", "bambi@stardust.com")
    assert not s.expired()
    assert s.view()["aud"] == ["ibac-people"]
    assert Session(access_token=token(sub="x", exp=int(time.time()) - 5), id_token="").expired()  # nosec B106


def test_bearer_modes_send_the_right_token():
    """delegation = the access token; wrong_audience = the id token; tampered = a broken signature; none = nothing."""
    access, ident = token(sub="auth0|1", aud="ibac-people"), token(sub="auth0|1", aud="console")
    s = Session(access_token=access, id_token=ident)
    assert bearer_for(s, "delegation") == access
    assert bearer_for(s, "wrong_audience") == ident
    broken = bearer_for(s, "tampered")
    assert broken != access
    assert (broken.count("."), decode_unverified(broken)["sub"]) == (2, "auth0|1")
    assert bearer_for(s, "none") is None
    assert bearer_for(None, "delegation") is None


def test_tamper_keeps_the_token_well_formed_and_breaks_only_the_signature():
    """The header and payload stay readable, so introspection reaches the signature check and fails there."""
    t = token(sub="auth0|1")
    head, payload, sig = t.split(".")
    bhead, bpayload, bsig = tamper(t).split(".")
    assert (bhead, bpayload) == (head, payload)
    assert bsig != sig
    assert len(bsig) == len(sig)


def test_decode_unverified_tolerates_garbage_and_expired_tokens():
    """Display-only decoding never raises."""
    assert decode_unverified("not-a-jwt") == {}
    assert decode_unverified(token(sub="x", exp=1))["sub"] == "x"
    assert json.dumps(decode_unverified(token(sub="x"))) != "{}"


class _Exchanging(Auth0Login):
    """A login whose code exchange needs no tenant: any known state becomes a session."""

    async def exchange(self, _code, state):
        if self.pending.pop(state, None) is None:
            msg = "unknown or expired login state"
            raise LoginError(msg)
        return Session(
            access_token=token(sub="auth0|1", aud="ibac-people"),
            id_token=token(sub="auth0|1", name="Bambi"),
        )


def _client():
    lg = _Exchanging(
        domain="tenant.test",
        client_id="c",
        client_secret="s",
        audience="ibac-people",
        public_url="http://localhost:3300",
    )  # nosec B106
    app = FastAPI()
    app.include_router(router(lambda _request: lg))
    return TestClient(app, follow_redirects=False), lg


def test_callback_installs_a_session_only_in_the_browser_that_started_the_login():
    """The login cookie binds the state to one browser: a callback replayed elsewhere installs nothing."""
    client, lg = _client()
    started = client.get("/auth/login")
    assert started.status_code == 302
    state = urllib.parse.parse_qs(urllib.parse.urlparse(started.headers["location"]).query)["state"][0]
    assert started.cookies.get(LOGIN_COOKIE) == state

    other = TestClient(client.app, follow_redirects=False)  # a browser that never started this login
    hijack = other.get(f"/auth/callback?code=x&state={state}")
    assert hijack.status_code == 302
    assert "login_error" in hijack.headers["location"]
    assert SESSION_COOKIE not in hijack.cookies
    assert state not in lg.pending  # the stolen state is burnt, the real browser cannot finish with it either

    second = client.get("/auth/login")
    state2 = urllib.parse.parse_qs(urllib.parse.urlparse(second.headers["location"]).query)["state"][0]
    done = client.get(f"/auth/callback?code=x&state={state2}")
    assert done.status_code == 302
    assert done.headers["location"] == "/"
    assert done.cookies.get(SESSION_COOKIE) in lg.sessions
