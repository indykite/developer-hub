# Copyright (c) 2026 IndyKite
"""Demo issuer: signs the persona (subject) and console (actor) JWTs the Token Service exchanges.

It stands in for the corporate IdP of a real deployment and does nothing an
IdP would not: RS256 JWTs with iss/sub/aud/iat/exp/jti, published through a
JWKS. The Token Service verifies them offline against the public key that
`make keys` copied into its configuration.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import TYPE_CHECKING, Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

if TYPE_CHECKING:
    from pathlib import Path


def generate_private_key_pem() -> bytes:
    """Return a fresh RSA-2048 private key in PKCS#8 PEM."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


class DemoIssuer:
    """Mints RS256 JWTs for one issuer identifier and publishes the matching JWKS."""

    def __init__(self, private_key_pem: bytes, issuer: str) -> None:
        """`issuer` becomes the `iss` claim; the kid is derived from the public modulus."""
        self._private_key = serialization.load_pem_private_key(private_key_pem, password=None)
        if not isinstance(self._private_key, rsa.RSAPrivateKey):
            msg = "demo issuer key must be an RSA private key"
            raise TypeError(msg)
        self.issuer = issuer
        public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self._private_key.public_key()))
        self.kid = hashlib.sha256(public_jwk["n"].encode()).hexdigest()[:16]
        self._public_jwk = {**public_jwk, "alg": "RS256", "use": "sig", "kid": self.kid}

    @classmethod
    def from_file(cls, path: Path, issuer: str) -> DemoIssuer:
        """Load the PEM `make keys` wrote."""
        return cls(path.read_bytes(), issuer)

    def mint(self, *, subject: str, audience: str, ttl_seconds: int, claims: dict[str, Any] | None = None) -> str:
        """Sign a token; iat/exp/jti are set here, the caller never fakes them."""
        now = int(time.time())
        payload: dict[str, Any] = {
            "iss": self.issuer,
            "sub": subject,
            "aud": audience,
            "iat": now,
            "exp": now + ttl_seconds,
            "jti": secrets.token_urlsafe(16),
        }
        if claims:
            # Supplemental claims never override the envelope: iss, sub, aud, iat, exp and jti stay ours.
            payload = {**claims, **payload}
        return jwt.encode(payload, self._private_key, algorithm="RS256", headers={"kid": self.kid})

    def public_jwk(self) -> dict[str, Any]:
        """Public half as a JWK (kty, n, e, alg, kid, use) - what the Token Service verifies with."""
        return dict(self._public_jwk)

    def jwks(self) -> dict[str, Any]:
        """RFC 7517 key set served at /.well-known/jwks.json."""
        return {"keys": [self.public_jwk()]}


def decode_unverified(token: str) -> dict[str, Any]:
    """Claims of a JWT for display only - no signature check, never used for a decision."""
    try:
        return jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return {}
