# Copyright (c) 2026 IndyKite
"""Render token-service.yaml from the example with a fresh signing key and client secret.

Prints the two values that go elsewhere: TOKEN_SERVICE_CLIENT_SECRET (this
app's .env) and TOKEN_SERVICE_PUBLIC_JWK (instant-stack's .env).
"""

import base64
import json
import os
import re
import secrets
import sys
import uuid
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import rsa

HERE = Path(__file__).resolve().parent


def b64(number: int) -> str:
    """Encode an integer as unpadded base64url, as a JWK field."""
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def main() -> None:
    """Render token-service.yaml and print the two values that go elsewhere."""
    domain = (os.getenv("AUTH0_DOMAIN") or "").strip().removeprefix("https://").strip("/")
    if not domain or "<" in domain:
        sys.exit("AUTH0_DOMAIN is not set (your tenant domain, e.g. your-tenant.eu.auth0.com)")
    target = HERE / "token-service.yaml"
    if target.exists() and "--force" not in sys.argv:
        sys.exit(f"{target.name} exists; pass --force to replace its key and secret")
    numbers = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_numbers()
    public = {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": uuid.uuid4().hex[:16],
        "n": b64(numbers.public_numbers.n),
        "e": b64(numbers.public_numbers.e),
    }
    private = {
        **public,
        "d": b64(numbers.d),
        "p": b64(numbers.p),
        "q": b64(numbers.q),
        "dp": b64(numbers.dmp1),
        "dq": b64(numbers.dmq1),
        "qi": b64(numbers.iqmp),
    }
    secret = secrets.token_urlsafe(32)
    text = (HERE / "token-service.example.yaml").read_text(encoding="utf-8")
    # The example leaves the client secret empty, with a note; the rendered file gets the secret and no note.
    text = (
        re.sub(r'client_secret: ""[^\n]*', f'client_secret: "{secret}"', text, count=1)
        .replace("<SIGNING_JWK>", json.dumps(private, separators=(",", ":")))
        .replace("<AUTH0_DOMAIN>", domain)
    )
    # The file holds the private signing key and the client secret: owner-only from the moment it exists.
    # The Token Service container runs as that owner (TOKEN_SERVICE_RUN_AS in .env).
    target.unlink(missing_ok=True)
    with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"wrote {target.name}\n")
    print("this app's .env:")
    print(f"TOKEN_SERVICE_CLIENT_SECRET={secret}\n")
    print("instant-stack's .env (one line):")
    print(f"TOKEN_SERVICE_PUBLIC_JWK={json.dumps(public, separators=(',', ':'))}")


if __name__ == "__main__":
    main()
