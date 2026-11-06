# Copyright (c) 2026 IndyKite
"""The console's login at Auth0: authorization code with PKCE, a server-side session, the person's token.

The console is a Regular Web Application of the tenant. A visitor is sent to Auth0's authorize endpoint
with a PKCE challenge and the people's API as audience; the callback exchanges the code for the person's
access token (what every gateway receives as Bearer) and an id token (names and emails, shown only). Both
live in a server-side session behind an HttpOnly cookie; nothing of them reaches the browser's scripts.

Sessions and pending logins are kept in memory: one console process, demo lifetimes.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

if TYPE_CHECKING:
    from collections.abc import Callable

SESSION_COOKIE = "intent_ibac_session"
LOGIN_TTL_SECONDS = 600
HTTP_UNAUTHORIZED = 401


def decode_unverified(token: str) -> dict[str, Any]:
    """Read a JWT's claims without verifying it: for display, never for a decision."""
    try:
        return jwt.decode(token, options={"verify_signature": False, "verify_exp": False, "verify_aud": False})
    except jwt.PyJWTError:
        return {}


@dataclass
class Session:
    """One logged-in person: the tokens Auth0 issued and what they say."""

    access_token: str
    id_token: str
    created: float = field(default_factory=time.time)

    @property
    def access_claims(self) -> dict[str, Any]:
        """Claims of the access token (unverified; the gateways verify it)."""
        return decode_unverified(self.access_token)

    @property
    def id_claims(self) -> dict[str, Any]:
        """Claims of the id token: name, email, picture."""
        return decode_unverified(self.id_token) if self.id_token else {}

    @property
    def sub(self) -> str:
        """The person's subject, the Person node's external_id."""
        return str(self.access_claims.get("sub") or self.id_claims.get("sub") or "")

    @property
    def name(self) -> str:
        """Display name from the id token, the email or the subject."""
        claims = self.id_claims
        return str(claims.get("name") or claims.get("nickname") or claims.get("email") or self.sub)

    @property
    def email(self) -> str:
        """Email from the id token, if the tenant released it."""
        return str(self.id_claims.get("email") or "")

    @property
    def expires_at(self) -> int:
        """Expiry of the access token (epoch seconds), 0 when unknown."""
        return int(self.access_claims.get("exp") or 0)

    def expired(self, now: float | None = None) -> bool:
        """Whether the access token is past its expiry."""
        return bool(self.expires_at) and (now or time.time()) >= self.expires_at

    def view(self) -> dict[str, Any]:
        """Describe the session the way /api/me tells the page."""
        claims = self.access_claims
        return {
            "logged_in": True,
            "sub": self.sub,
            "name": self.name,
            "email": self.email,
            "picture": self.id_claims.get("picture"),
            "aud": claims.get("aud"),
            "iss": claims.get("iss"),
            "expires_at": self.expires_at,
        }


def tamper(token: str) -> str:
    """Break a JWT's signature while keeping it a well-formed token: what a replayed-then-edited token looks like."""
    head, _, signature = token.rpartition(".")
    flipped = "".join("A" if c != "A" else "B" for c in signature[:6]) + signature[6:]
    return f"{head}.{flipped}"


def bearer_for(session: Session | None, mode: str) -> str | None:
    """Pick what goes in the Authorization header for one of the console's four token modes.

    `delegation` is the person's access token, the normal path; `wrong_audience` is the person's id
    token, a real token of the tenant issued for the console, not for the people's API, which the gateway
    refuses as not issued for this agent; `tampered` is the access token with a broken signature, which
    introspection at the bridge reports inactive; `none` sends nothing.
    """
    if session is None or mode == "none":
        return None
    if mode == "wrong_audience":
        return session.id_token or tamper(session.access_token)
    if mode == "tampered":
        return tamper(session.access_token)
    return session.access_token


BEARER_KIND = {
    "delegation": "the person's Auth0 access token",
    "wrong_audience": "the person's id token: a tenant token, issued for the console and not for the people's API",
    "tampered": "the access token with its signature broken",
    "none": "no Authorization header",
}


@dataclass
class Auth0Login:
    """The console's end of the authorization-code flow and the sessions it produced."""

    domain: str
    client_id: str
    client_secret: str
    audience: str
    public_url: str
    scopes: str = "openid profile email"
    timeout: float = 10.0
    sessions: dict[str, Session] = field(default_factory=dict)
    pending: dict[str, tuple[str, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Accept the tenant as a bare domain or as a URL: only the host is kept."""
        self.domain = self.domain.strip().removeprefix("https://").removeprefix("http://").strip("/")

    @property
    def issuer(self) -> str:
        """The tenant's issuer, as its tokens spell it."""
        return f"https://{self.domain}/"

    @property
    def redirect_uri(self) -> str:
        """The callback registered on the Console application."""
        return f"{self.public_url.rstrip('/')}/auth/callback"

    def authorize_url(self, login_hint: str = "") -> str:
        """Start a login: remember the PKCE verifier under a fresh state, return where to send the browser."""
        self._expire_pending()
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(24)
        self.pending[state] = (verifier, time.time())
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scopes,
            "audience": self.audience,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if login_hint:
            params["login_hint"] = login_hint
            params["prompt"] = "login"
        return f"{self.issuer}authorize?{urllib.parse.urlencode(params)}"

    async def exchange(self, code: str, state: str) -> Session:
        """Finish a login: the code and the remembered verifier for the person's tokens."""
        self._expire_pending()
        pending = self.pending.pop(state, None)
        if pending is None:
            msg = "unknown or expired login state"
            raise LoginError(msg)
        verifier, _ = pending
        form = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": self.redirect_uri,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.issuer}oauth/token", data=form)
        except httpx.HTTPError as exc:
            msg = f"Auth0 could not be reached for the code exchange: {type(exc).__name__}: {exc}"
            raise LoginError(msg) from exc
        if response.status_code != httpx.codes.OK:
            msg = f"Auth0 refused the code: HTTP {response.status_code} {response.text[:200]}"
            raise LoginError(msg)
        body = response.json()
        access_token = body.get("access_token") or ""
        claims = decode_unverified(access_token)
        if not claims:
            msg = (
                "Auth0 answered an opaque access token: "
                "the Console application must request the people's API as audience"
            )
            raise LoginError(msg)
        return Session(access_token=access_token, id_token=body.get("id_token") or "")

    def remember(self, session: Session) -> str:
        """Store a session, return the cookie value that names it."""
        sid = secrets.token_urlsafe(32)
        self.sessions[sid] = session
        return sid

    def session_of(self, request: Request) -> Session | None:
        """Return the live session behind the request's cookie, or None (unknown, expired)."""
        sid = request.cookies.get(SESSION_COOKIE)
        session = self.sessions.get(sid) if sid else None
        if session is None:
            return None
        if session.expired():
            self.sessions.pop(sid, None)
            return None
        return session

    def forget(self, request: Request) -> None:
        """Drop the request's session, if any."""
        sid = request.cookies.get(SESSION_COOKIE)
        if sid:
            self.sessions.pop(sid, None)

    def logout_url(self) -> str:
        """Auth0's logout, back to the console's front page."""
        params = {"client_id": self.client_id, "returnTo": f"{self.public_url.rstrip('/')}/"}
        return f"{self.issuer}v2/logout?{urllib.parse.urlencode(params)}"

    def _expire_pending(self) -> None:
        cutoff = time.time() - LOGIN_TTL_SECONDS
        for state in [s for s, (_, started) in self.pending.items() if started < cutoff]:
            self.pending.pop(state, None)


class LoginError(Exception):
    """A login that cannot be finished: the state is unknown, Auth0 refused the code, the token is opaque."""


def router(login_of: Callable[[Request], Auth0Login]) -> APIRouter:
    """Mount the login round trip and the "who am I" endpoint."""
    api = APIRouter()

    @api.get("/auth/login", include_in_schema=False)
    async def login(request: Request, hint: str = "") -> RedirectResponse:
        return RedirectResponse(login_of(request).authorize_url(login_hint=hint), status_code=302)

    @api.get("/auth/callback", include_in_schema=False)
    async def callback(
        request: Request,
        code: str = "",
        state: str = "",
        error: str = "",
        error_description: str = "",
    ) -> Response:
        if error:
            return RedirectResponse(f"/?login_error={urllib.parse.quote(error_description or error)}", status_code=302)
        try:
            session = await login_of(request).exchange(code, state)
        except LoginError as exc:
            return RedirectResponse(f"/?login_error={urllib.parse.quote(str(exc))}", status_code=302)
        sid = login_of(request).remember(session)
        response = RedirectResponse("/", status_code=302)
        response.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax", path="/")
        return response

    @api.get("/auth/logout", include_in_schema=False)
    async def logout(request: Request) -> Response:
        login = login_of(request)
        login.forget(request)
        response = RedirectResponse(login.logout_url(), status_code=302)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @api.get("/api/me")
    async def me(request: Request) -> JSONResponse:
        login = login_of(request)
        session = login.session_of(request)
        if session is None:
            return JSONResponse(
                {"logged_in": False, "login_url": "/auth/login", "issuer": login.issuer, "audience": login.audience},
            )
        return JSONResponse({**session.view(), "login_url": "/auth/login", "logout_url": "/auth/logout"})

    return api


def require_session(login: Auth0Login, request: Request) -> Session:
    """Return the request's session, or raise 401 with a hint the page turns into a login button."""
    session = login.session_of(request)
    if session is None:
        raise HTTPException(
            status_code=HTTP_UNAUTHORIZED,
            detail="log in first: the gateways accept only a person's Auth0 token",
        )
    return session
