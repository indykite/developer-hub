"""Chatbot web app - A2A client UI that forwards prompts to the Orchestrator Agent."""

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import urllib.parse
from queue import Empty, Queue

import httpx
from a2a_client import send_to_orchestrator, stream_to_orchestrator
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, request, send_from_directory, session
from flask_cors import CORS
from flask_session import Session

_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

load_dotenv()

ORCHESTRATOR_HOST = os.getenv("ORCHESTRATOR_HOST", "localhost")
ORCHESTRATOR_PORT = int(os.getenv("ORCHESTRATOR_PORT", 6001))  # noqa: PLW1508

ID_SERVER_BASE_URL = (os.getenv("ID_SERVER_BASE_URL") or "").rstrip("/")
ID_SERVER_AUTH_ENDPOINT = os.getenv("ID_SERVER_AUTHORIZE_ENDPOINT", "oauth-authorize")
ID_SERVER_TOKEN_ENDPOINT = os.getenv("ID_SERVER_TOKEN_ENDPOINT", "oauth-token")
CHATBOT_HOST = (os.getenv("CHATBOT_HOST") or "localhost").strip()
CHATBOT_PORT = str(os.getenv("CHATBOT_PORT", "3000")).strip()
CHATBOT_REDIRECT_URL = (os.getenv("CHATBOT_REDIRECT_URL") or "").strip()
OAUTH_CLIENT_ID = os.getenv("ID_SERVER_CLIENT_ID", "indykiteagent")
OAUTH_CLIENT_SECRET = (os.getenv("ID_SERVER_CLIENT_SECRET") or "").strip()
# Token endpoint auth: "basic" (Authorization header) or "post" (form body). Most IdPs use "basic".
OAUTH_TOKEN_AUTH = (os.getenv("ID_SERVER_TOKEN_AUTH", "basic") or "basic").lower()
OAUTH_SCOPES = os.getenv("ID_SERVER_SCOPES", "openid profile email").strip()
SECRET_KEY = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)

app = Flask(__name__)
app.secret_key = SECRET_KEY
# configure server-side session to make sure it works with Docker
app.config["SESSION_TYPE"] = "filesystem"
# Demo Flask session dir, lives inside the container.
app.config["SESSION_FILE_DIR"] = "/tmp/flask_session"  # nosec B108  # noqa: S108
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_USE_SIGNER"] = True
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
)
Session(app)
CORS(app, supports_credentials=True)


def _pkce_code_verifier() -> str:
    """Generate a cryptographically random code_verifier (43-128 chars)."""
    return secrets.token_urlsafe(48)


def _pkce_code_challenge(verifier: str) -> str:
    """Compute S256 code_challenge from code_verifier."""
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# single global queue for demo
update_queue = Queue()


@app.route("/api/push-update", methods=["POST"])
def push_update():
    data = request.json or {}
    logger.info("==============================")
    logger.info("RECEIVED DATA")
    logger.info(data)
    logger.info("==============================")
    decision = data.get("decision")
    reason = data.get("reason")
    subject = data.get("subject")
    actor = data.get("actor")
    action = data.get("action")
    timestamp = data.get("timestamp")
    service = data.get("service", "unknown")

    # store structured event
    event = {
        "decision": decision,
        "reason": reason,
        "subject": subject,
        "actor": actor,
        "action": action,
        "timestamp": timestamp,
        "service": service,
    }
    update_queue.put(event)
    return jsonify({"ok": True})


@app.route("/api/updates")
def updates_sse():
    def event_stream():
        # Optional: Send a comment to keep the connection alive immediately
        yield ": connected\n\n"

        while True:
            try:
                # Use a timeout so the loop can check for client disconnection
                # and doesn't hang the thread forever
                event = update_queue.get(timeout=20)
                yield f"data: {json.dumps(event)}\n\n"
            except Empty:
                # Send a "keep-alive" heart beat every 20 seconds
                yield ": ping\n\n"
            except GeneratorExit:
                # Clean up when the browser closes the connection
                break
            except Exception as e:
                logger.error(f"SSE Error: {e}")  # noqa: G004,TRY400
                break

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Critical for Docker/Nginx
            "Connection": "keep-alive",
        },
    )


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify(
        {
            "status": "healthy",
            "orchestrator": f"{ORCHESTRATOR_HOST}:{ORCHESTRATOR_PORT}",
        },
    )


@app.route("/api/auth/status", methods=["GET"])
def auth_status():
    """Return whether the user is authenticated."""
    return jsonify({"logged_in": bool(session.get("access_token"))})


@app.route("/api/auth/login", methods=["GET"])
def auth_login():
    """Redirect to OAuth2 authorization endpoint with PKCE."""
    if not ID_SERVER_BASE_URL or not CHATBOT_REDIRECT_URL:
        return jsonify({"error": "OAuth not configured (ID_SERVER_BASE_URL, CHATBOT_REDIRECT_URL)"}), 500

    code_verifier = _pkce_code_verifier()
    code_challenge = _pkce_code_challenge(code_verifier)
    state = secrets.token_urlsafe(24)

    session["oauth_code_verifier"] = code_verifier
    session["oauth_state"] = state

    auth_url = f"{ID_SERVER_BASE_URL}/{ID_SERVER_AUTH_ENDPOINT}"
    params = {
        "client_id": OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": CHATBOT_REDIRECT_URL,
        "state": state,
        "scope": OAUTH_SCOPES,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    url = f"{auth_url}?{urllib.parse.urlencode(params)}"
    return redirect(url)


@app.route("/auth/callback", methods=["GET"])
def auth_callback():  # noqa: C901,PLR0911
    """Handle OAuth2 callback, exchange code for token, store in session."""
    state = request.args.get("state")
    code = request.args.get("code")
    error = request.args.get("error")

    if error:
        logger.warning("OAuth error: %s", error)
        return redirect("/?error=" + urllib.parse.quote(str(error)))

    if not state or state != session.get("oauth_state"):
        return redirect("/?error=invalid_state")
    if not code:
        return redirect("/?error=missing_code")

    code_verifier = session.pop("oauth_code_verifier", None)
    session.pop("oauth_state", None)
    if not code_verifier:
        return redirect("/?error=missing_verifier")

    token_url = f"{ID_SERVER_BASE_URL}/{ID_SERVER_TOKEN_ENDPOINT}"
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": CHATBOT_REDIRECT_URL,
        "client_id": OAUTH_CLIENT_ID,
        "code_verifier": code_verifier,
    }
    # "post" / "basic" are OAuth token-endpoint auth method names, not passwords.
    if OAUTH_CLIENT_SECRET and OAUTH_TOKEN_AUTH == "post":  # nosec B105  # noqa: S105
        payload["client_secret"] = OAUTH_CLIENT_SECRET

    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if OAUTH_CLIENT_SECRET and OAUTH_TOKEN_AUTH == "basic":  # nosec B105  # noqa: S105
        creds = base64.b64encode(f"{OAUTH_CLIENT_ID}:{OAUTH_CLIENT_SECRET}".encode()).decode()
        headers["Authorization"] = f"Basic {creds}"

    try:
        resp = httpx.post(token_url, data=payload, headers=headers, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
        access_token = data.get("access_token")
        if not access_token:
            return redirect("/?error=no_access_token")
        session["access_token"] = access_token
        id_token = data.get("id_token")
        if id_token:
            logger.debug(f"ID Token: {id_token}")  # noqa: G004
            session["id_token"] = id_token
        logger.info("User logged in successfully")
        return redirect("/")
    except httpx.HTTPStatusError as e:
        logger.warning("Token exchange failed: %s %s", e.response.status_code, e.response.text[:200])
        return redirect("/?error=token_exchange_failed")
    except Exception as e:
        logger.exception("Token exchange error")
        return redirect("/?error=" + urllib.parse.quote(str(e)[:100]))


@app.route("/api/config", methods=["GET"])
def get_config():
    """Public config for the frontend (e.g. logout URL from ID_SERVER_BASE_URL)."""
    # Where to send the user after IdP logout (main chatbot page)
    post_logout_redirect = f"http://{CHATBOT_HOST}:{CHATBOT_PORT}/"
    logout_url = ""
    if ID_SERVER_BASE_URL:
        query_parts = [f"post_logout_redirect_uri={urllib.parse.quote(post_logout_redirect, safe='')}"]
        id_token = session.get("id_token")
        if id_token:
            query_parts.append(f"id_token_hint={urllib.parse.quote(id_token, safe='')}")
        logout_url = f"{ID_SERVER_BASE_URL}/oauth-session/logout?{'&'.join(query_parts)}"
        logger.debug(f"Logout URL: {logout_url}")  # noqa: G004
    return jsonify({"logout_url": logout_url})


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    """Clear session and log out."""
    session.clear()
    return jsonify({"ok": True})


def _get_access_token():
    """Get access token from session (for API routes)."""
    return session.get("access_token")


def _stream_sse(message: str, context_id: str | None, access_token: str | None = None):
    """Collect stream fully in async context, then yield SSE.

    Avoids generator cleanup issues when mixing threads + nested async
    generators (JsonRpcTransport, aconnect_sse).
    """
    # Send immediately so headers flush; avoids proxy/browser TTFB timeouts while orchestrator runs
    yield ": connecting\n\n"

    def run_async():
        async def collect_all():
            events = []
            async for ev in stream_to_orchestrator(
                message,
                host=ORCHESTRATOR_HOST,
                port=ORCHESTRATOR_PORT,
                context_id=context_id,
                access_token=access_token,
            ):
                events.append(ev)  # noqa: PERF401
            return events

        return asyncio.run(collect_all())

    try:
        events = run_async()
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Received %d event(s) from orchestrator", len(events))
            for ev in events:
                ev_preview = (ev.get("text", "") or "")[:200]
                logger.debug("Orchestrator response: type=%s, text_preview=%r", ev.get("type"), ev_preview)
    except Exception as e:
        logger.exception("Orchestrator request failed")
        yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
        return

    for ev in events:
        yield f"data: {json.dumps(ev)}\n\n"


@app.route("/api/chat/stream", methods=["POST"])
def chat_stream():
    access_token = _get_access_token()
    if not access_token:
        logger.warning("Warning: Prompting user not authenticated.")

    data = request.json or {}
    message = data.get("message", "").strip()
    context_id = (data.get("context_id") or "").strip() or None
    if not message:
        return jsonify({"error": "Message is required"}), 400

    return Response(
        _stream_sse(message, context_id, access_token),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/chat", methods=["POST"])
def chat():
    access_token = _get_access_token()
    if not access_token:
        logger.warning("Warning: Prompting user not authenticated.")

    data = request.json or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "Message is required"}), 400

    try:
        response = send_to_orchestrator(
            message,
            host=ORCHESTRATOR_HOST,
            port=ORCHESTRATOR_PORT,
            access_token=access_token,
        )
        return jsonify({"response": response or ""})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", "-p", type=int, default=int(CHATBOT_PORT))
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    print(f"Chatbot running at http://{args.host}:{args.port}")  # noqa: T201
    print(f"Orchestrator: http://{ORCHESTRATOR_HOST}:{ORCHESTRATOR_PORT}")  # noqa: T201
    # Demo chatbot - Flask debug mode is intentional.
    app.run(host=args.host, port=args.port, debug=True)  # nosec B201  # noqa: S201
