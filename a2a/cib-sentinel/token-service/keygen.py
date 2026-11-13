# Copyright (c) 2026 IndyKite
"""Render token-service.yaml from the example with a fresh signing key and client secret.

Prints the two values that go elsewhere: TOKEN_SERVICE_CLIENT_SECRET (this
app's .env) and TOKEN_SERVICE_PUBLIC_JWK (instant-stack's .env).
"""

import base64
import json
import os
import secrets
import sys
import uuid
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import rsa

HERE = Path(__file__).resolve().parent


def b64(number: int) -> str:
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def main() -> None:
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
    text = (
        text.replace("<CLIENT_SECRET>", secret)
        .replace("<SIGNING_JWK>", json.dumps(private, separators=(",", ":")))
        .replace("<AUTH0_DOMAIN>", domain)
    )
    target.write_text(text, encoding="utf-8")
    target.chmod(0o644)  # read by the Token Service container, which runs as another user
    print(f"wrote {target.name}\n")
    print("this app's .env:")
    print(f"TOKEN_SERVICE_CLIENT_SECRET={secret}\n")
    print("instant-stack's .env (one line):")
    print(f"TOKEN_SERVICE_PUBLIC_JWK={json.dumps(public, separators=(',', ':'))}")


if __name__ == "__main__":
    main()
