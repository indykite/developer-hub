# Copyright (c) 2026 IndyKite
"""The console's Auth0 login: PKCE, the session, the four token modes."""

import base64
import hashlib
import json
import time
import urllib.parse

import jwt
import pytest
from app.auth import Auth0Login, LoginError, Session, bearer_for, decode_unverified, tamper


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
