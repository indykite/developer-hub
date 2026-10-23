# Copyright (c) 2026 IndyKite
import jwt
import pytest
from app.issuer import DemoIssuer, decode_unverified, generate_private_key_pem
from app.keygen import render


@pytest.fixture(scope="module")
def issuer():
    """Pytest fixture: a demo issuer with a throwaway key."""
    return DemoIssuer(generate_private_key_pem(), "https://demo-idp.test")


def test_minted_token_verifies_against_the_published_jwks(issuer):
    """Minted token verifies against the published jwks."""
    token = issuer.mint(subject="leslie", audience="intent-demo-console", ttl_seconds=60)
    [public_jwk] = issuer.jwks()["keys"]
    key = jwt.PyJWK(public_jwk).key
    claims = jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        audience="intent-demo-console",
        issuer="https://demo-idp.test",
    )
    assert claims["sub"] == "leslie"
    assert jwt.get_unverified_header(token)["kid"] == issuer.kid
    assert public_jwk["use"] == "sig"


def test_caller_claims_cannot_override_the_envelope(issuer):
    """Supplemental claims are added, but iss/sub/aud/exp/jti stay the issuer's own."""
    hostile = {"name": "Leslie", "sub": "roy", "iss": "https://evil.test", "aud": "intent_agent", "exp": 0, "jti": "x"}
    token = issuer.mint(subject="leslie", audience="a", ttl_seconds=60, claims=hostile)
    claims = decode_unverified(token)
    assert claims["name"] == "Leslie"
    assert claims["sub"] == "leslie"
    assert claims["iss"] == "https://demo-idp.test"
    assert claims["aud"] == "a"
    assert claims["exp"] - claims["iat"] == 60
    assert claims["jti"] != "x"


def test_decode_unverified_tolerates_garbage():
    """Decode unverified tolerates garbage."""
    assert decode_unverified("not-a-jwt") == {}


def test_render_fills_every_placeholder():
    """Render fills every placeholder."""
    template = (
        "a __ITS_SIGNING_JWK__ b __DEMO_IDP_PUBLIC_JWK__ c __TOKEN_SERVICE_CLIENT_SECRET__ d __AUDIT_WEBHOOK_API_KEY__"
    )
    out = render(template, its_jwk="J", demo_public_jwk="P", client_secret="S", audit_key="K")  # nosec B106
    assert out == "a J b P c S d K"


def test_render_refuses_a_template_missing_a_placeholder():
    """Render refuses a template missing a placeholder."""
    with pytest.raises(ValueError, match="__AUDIT_WEBHOOK_API_KEY__"):
        render(
            "__ITS_SIGNING_JWK__ __DEMO_IDP_PUBLIC_JWK__ __TOKEN_SERVICE_CLIENT_SECRET__",
            its_jwk="",
            demo_public_jwk="",
            client_secret="",  # nosec B106
            audit_key="",
        )
