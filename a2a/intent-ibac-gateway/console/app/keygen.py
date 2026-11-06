# Copyright (c) 2026 IndyKite
"""`python -m app.keygen`: generate the demo's key material and render the Token Service config.

Writes
  - token-service.yaml from token-service.template.yaml, with a fresh private
    signing JWK for the Token Service, the Auth0 tenant domain (AUTH0_DOMAIN
    from the environment), a client secret and an audit webhook key,
  - audit-config.yaml of the gateways from audit-config.template.yaml, with
    the same audit webhook key,
and prints the two secrets that must also go into the demo's .env, plus the
Token Service public key instant-stack needs in its .env to provision the
intent-ibac dataset. Existing files are never overwritten unless --force is given.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path
from typing import TextIO

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

_PLACEHOLDERS = (
    "__ITS_SIGNING_JWK__",
    "__AUTH0_DOMAIN__",
    "__TOKEN_SERVICE_CLIENT_SECRET__",
    "__AUDIT_WEBHOOK_API_KEY__",
)


def _its_jwks(kid: str) -> tuple[str, str]:
    """Return the Token Service's signing key as (private JWK, public JWK), both single-line JSON."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key))
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    meta = {"alg": "RS256", "kid": kid, "use": "sig"}
    return (
        json.dumps({**meta, **private}, separators=(",", ":")),
        json.dumps({f: {**meta, **public}[f] for f in ("kty", "n", "e", "alg", "kid", "use")}, separators=(",", ":")),
    )


def render(template: str, *, its_jwk: str, auth0_domain: str, client_secret: str, audit_key: str) -> str:
    """Fill every placeholder; a template missing one is a packaging error, not a silent default."""
    missing = [p for p in _PLACEHOLDERS if p not in template]
    if missing:
        msg = f"template lacks placeholders: {', '.join(missing)}"
        raise ValueError(msg)
    return (
        template.replace("__ITS_SIGNING_JWK__", its_jwk)
        .replace("__AUTH0_DOMAIN__", auth0_domain)
        .replace("__TOKEN_SERVICE_CLIENT_SECRET__", client_secret)
        .replace("__AUDIT_WEBHOOK_API_KEY__", audit_key)
    )


def main(argv: list[str] | None = None) -> int:
    """Parse the arguments, generate the material, render the config; return the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path, help="token-service.yaml to write")
    parser.add_argument("--audit-template", type=Path, help="audit-config.template.yaml of the gateways")
    parser.add_argument("--audit-out", type=Path, help="audit-config.yaml to write (with --audit-template)")
    parser.add_argument("--force", action="store_true", help="overwrite existing files")
    parser.add_argument(
        "--auth0-domain",
        default=os.environ.get("AUTH0_DOMAIN", ""),
        help="Auth0 tenant domain written into the Token Service matchers (default: $AUTH0_DOMAIN)",
    )
    args = parser.parse_args(argv)
    auth0_domain = args.auth0_domain.strip().removeprefix("https://").rstrip("/")
    if not auth0_domain:
        parser.error("--auth0-domain (or AUTH0_DOMAIN in .env) is required: the tenant the gateways' callers log in at")
    if bool(args.audit_template) != bool(args.audit_out):
        parser.error("--audit-template and --audit-out go together")

    for path in (args.out, args.audit_out):
        if path is None:
            continue
        if path.exists() and not args.force:
            _say(f"refusing to overwrite {path} (use --force)", stream=sys.stderr)
            return 2

    # 0644 on purpose: the files are bind-mounted into containers that run as
    # other uids (Token Service, gateways) and only need to read them.

    client_secret = secrets.token_urlsafe(32)
    audit_key = secrets.token_urlsafe(32)
    its_private_jwk, its_public_jwk = _its_jwks(kid=f"its-{secrets.token_hex(4)}")
    rendered = render(
        args.template.read_text(),
        its_jwk=its_private_jwk,
        auth0_domain=auth0_domain,
        client_secret=client_secret,
        audit_key=audit_key,
    )
    # Render both files before writing either: a bad audit template must not leave a half-done key set behind.
    audit_rendered = None
    if args.audit_template:
        audit_template = args.audit_template.read_text()
        if "__AUDIT_WEBHOOK_API_KEY__" not in audit_template:
            _say("audit template lacks the __AUDIT_WEBHOOK_API_KEY__ placeholder", stream=sys.stderr)
            return 2
        audit_rendered = audit_template.replace("__AUDIT_WEBHOOK_API_KEY__", audit_key)
    args.out.write_text(rendered)
    args.out.chmod(0o644)
    if args.audit_out is not None and audit_rendered is not None:
        args.audit_out.write_text(audit_rendered)
        args.audit_out.chmod(0o644)

    _say(f"wrote {args.out}" + (f" and {args.audit_out}" if args.audit_out else ""))
    _say("Add these two lines to the demo's .env:")
    _say(f"TOKEN_SERVICE_CLIENT_SECRET={client_secret}")
    _say(f"AUDIT_WEBHOOK_API_KEY={audit_key}")
    _say("")
    _say("And this one to instant-stack's .env before provisioning the intent-ibac dataset")
    _say("(the platform verifies the Token Service's delegated tokens with it):")
    _say(f"TOKEN_SERVICE_PUBLIC_JWK={its_public_jwk}")
    return 0


def _say(line: str, stream: TextIO = sys.stdout) -> None:
    stream.write(line + "\n")


if __name__ == "__main__":
    sys.exit(main())
