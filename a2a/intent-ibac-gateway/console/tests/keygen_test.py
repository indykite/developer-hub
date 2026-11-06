# Copyright (c) 2026 IndyKite
"""The Token Service config renderer: every placeholder, or nothing."""

import pytest
from app.keygen import render


def test_render_fills_every_placeholder():
    """Render fills every placeholder."""
    template = "a __ITS_SIGNING_JWK__ b __AUTH0_DOMAIN__ c __TOKEN_SERVICE_CLIENT_SECRET__ d __AUDIT_WEBHOOK_API_KEY__"
    out = render(template, its_jwk="J", auth0_domain="tenant.test", client_secret="S", audit_key="K")  # nosec B106
    assert out == "a J b tenant.test c S d K"


def test_render_refuses_a_template_missing_a_placeholder():
    """A template that lost a placeholder is a packaging error, not a silent default."""
    with pytest.raises(ValueError, match="__AUDIT_WEBHOOK_API_KEY__"):
        render(
            "__ITS_SIGNING_JWK__ __AUTH0_DOMAIN__ __TOKEN_SERVICE_CLIENT_SECRET__",
            its_jwk="",
            auth0_domain="",
            client_secret="",  # nosec B106
            audit_key="",
        )
