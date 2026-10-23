# Copyright (c) 2026 IndyKite
"""`python -m app.keygen`: generate the demo's key material and render the Token Service config.

Writes
  - the demo issuer private key (PEM) the console signs persona/actor JWTs with,
  - token-service.yaml from token-service.template.yaml, with a fresh private
    signing JWK for the Token Service, the demo issuer's public JWK, a client
    secret and an audit webhook key,
and prints the two secrets that must also go into .env. Existing files are
never overwritten unless --force is given.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path
from typing import TextIO

import jwt
from app.issuer import DemoIssuer, generate_private_key_pem
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_PLACEHOLDERS = (
    "__ITS_SIGNING_JWK__",
    "__DEMO_IDP_PUBLIC_JWK__",
    "__TOKEN_SERVICE_CLIENT_SECRET__",
    "__AUDIT_WEBHOOK_API_KEY__",
)


def _its_private_jwk(kid: str) -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key))
    return json.dumps({"alg": "RS256", "kid": kid, "use": "sig", **jwk}, separators=(",", ":"))


def render(template: str, *, its_jwk: str, demo_public_jwk: str, client_secret: str, audit_key: str) -> str:
    """Fill every placeholder; a template missing one is a packaging error, not a silent default."""
    missing = [p for p in _PLACEHOLDERS if p not in template]
    if missing:
        msg = f"template lacks placeholders: {', '.join(missing)}"
        raise ValueError(msg)
    return (
        template.replace("__ITS_SIGNING_JWK__", its_jwk)
        .replace("__DEMO_IDP_PUBLIC_JWK__", demo_public_jwk)
        .replace("__TOKEN_SERVICE_CLIENT_SECRET__", client_secret)
        .replace("__AUDIT_WEBHOOK_API_KEY__", audit_key)
    )


def main(argv: list[str] | None = None) -> int:
    """Parse the arguments, generate the material, render the config; return the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path, help="token-service.yaml to write")
    parser.add_argument("--key", required=True, type=Path, help="demo issuer private key (PEM) to write")
    parser.add_argument("--force", action="store_true", help="overwrite existing files")
    args = parser.parse_args(argv)

    for path in (args.out, args.key):
        if path.exists() and not args.force:
            _say(f"refusing to overwrite {path} (use --force)", stream=sys.stderr)
            return 2

    # 0644 on purpose: the files are bind-mounted into containers that run as
    # other uids (console 1001, Token Service its own) and only need to read them.
    pem = generate_private_key_pem()
    args.key.write_bytes(pem)
    args.key.chmod(0o644)
    demo_public_jwk = json.dumps(DemoIssuer(pem, "unused").public_jwk(), separators=(",", ":"))

    client_secret = secrets.token_urlsafe(32)
    audit_key = secrets.token_urlsafe(32)
    rendered = render(
        args.template.read_text(),
        its_jwk=_its_private_jwk(kid=f"its-{secrets.token_hex(4)}"),
        demo_public_jwk=demo_public_jwk,
        client_secret=client_secret,
        audit_key=audit_key,
    )
    args.out.write_text(rendered)
    args.out.chmod(0o644)

    # Confirm the PEM round-trips before anyone depends on it.
    serialization.load_pem_private_key(args.key.read_bytes(), password=None)

    _say(f"wrote {args.key} and {args.out}")
    _say("Add these two lines to .env:")
    _say(f"TOKEN_SERVICE_CLIENT_SECRET={client_secret}")
    _say(f"AUDIT_WEBHOOK_API_KEY={audit_key}")
    return 0


def _say(line: str, stream: TextIO = sys.stdout) -> None:
    stream.write(line + "\n")


if __name__ == "__main__":
    sys.exit(main())
