# Copyright (c) 2026 IndyKite
"""Chatbot web app - A2A client UI that forwards prompts to the Orchestrator Agent."""

import argparse
import asyncio
import base64
import concurrent.futures
import hashlib
import json
import logging
import os
import secrets
import threading
import urllib.parse
from datetime import UTC, datetime
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
ORCHESTRATOR_PORT = int(os.getenv("ORCHESTRATOR_PORT", "6001"))

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

# "Explain the decision": the audit cards' why? button renders the live
# authorization path via two CIQ explain queries (staff leg + direct leg)
# executed against the platform with the app-agent credentials. The feature is
# hidden unless all four values are configured (same gating as other optional
# routes). EXPLAIN_WORKFLOW_MAP ("service=wf,service=wf") lets the UI derive
# the workflow id for DENY cards, whose reason text does not name it.
INDYKITE_BASE_URL = (os.getenv("INDYKITE_BASE_URL") or "").strip().rstrip("/")
APP_AGENT_CREDENTIALS_TOKEN = (os.getenv("APP_AGENT_CREDENTIALS_TOKEN") or "").strip()
EXPLAIN_STAFF_QUERY_ID = (os.getenv("EXPLAIN_STAFF_QUERY_ID") or "").strip()
EXPLAIN_DIRECT_QUERY_ID = (os.getenv("EXPLAIN_DIRECT_QUERY_ID") or "").strip()
# Architecture page: active usecase + compose profiles, used only to badge the
# diagram and dim the optional (profile-gated) service groups.
USECASE = (os.getenv("USECASE") or "").strip()
COMPOSE_PROFILES = [p.strip() for p in (os.getenv("COMPOSE_PROFILES") or "").split(",") if p.strip()]
# Usecase branding (from the usecase bundle's usecase.env): the console
# presents itself as the demo organization, e.g. "SecureHome Insurance".
ORG_NAME = (os.getenv("ORG_NAME") or "").strip()
ORG_TAGLINE = (os.getenv("ORG_TAGLINE") or "").strip()
# App layer ("what the customer sees"): the console has a second face styled
# as the organization's own web app - sections behind a nav, action cards that
# send the demo prompts, an access-request inbox. All of it is usecase data:
# usecases/<usecase>/app.json, mounted at USECASE_APP_FILE by docker-compose.
USECASE_APP_FILE = (os.getenv("USECASE_APP_FILE") or "/app/usecase-app.json").strip()
# The platform's MCP server: the app layer's staff check runs a knowledge
# query there as the logged-in user (their bearer token), like the agents do.
MCP_SERVER_URL = (os.getenv("MCP_SERVER_URL") or "").strip()


def _load_app_config() -> dict:
    """Read the usecase's app.json; an absent or broken file means 'no sections' (Home only)."""
    try:
        with open(USECASE_APP_FILE, encoding="utf-8") as f:  # noqa: PTH123
            data = json.load(f)
    except FileNotFoundError:
        logger.info("No app layer config at %s - the app view shows Home only", USECASE_APP_FILE)
        return {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not read app layer config %s: %s", USECASE_APP_FILE, e)
        return {}
    return data if isinstance(data, dict) else {}


APP_CONFIG = _load_app_config()
EXPLAIN_ENABLED = all(
    (INDYKITE_BASE_URL, APP_AGENT_CREDENTIALS_TOKEN, EXPLAIN_STAFF_QUERY_ID, EXPLAIN_DIRECT_QUERY_ID),
)
EXPLAIN_WORKFLOW_MAP = {
    svc.strip(): wf.strip()
    for svc, _, wf in (pair.partition("=") for pair in (os.getenv("EXPLAIN_WORKFLOW_MAP") or "").split(","))
    if svc.strip() and wf.strip()
}
# Deny -> remediate -> allow: which gateways' DENY cards offer a one-click
# CAN_TRIGGER grant, and the workflow bundle one click writes
# ("service=wf-a:wf-b,service2=..."). The write spends the app-agent key;
# /api/grant guards it with a live AuthZEN self-check on the caller.
AUTHZEN_SUBJECT_TYPE = ((os.getenv("AUTHZEN_SUBJECT_TYPES") or "User").replace(",", " ").split() or ["User"])[0]
GRANT_WORKFLOW_MAP = {
    svc.strip(): [wf.strip() for wf in wfs.split(":") if wf.strip()]
    for svc, _, wfs in (pair.partition("=") for pair in (os.getenv("GRANT_WORKFLOW_MAP") or "").split(","))
    if svc.strip() and wfs.strip()
}
GRANT_ENABLED = bool(INDYKITE_BASE_URL and APP_AGENT_CREDENTIALS_TOKEN and GRANT_WORKFLOW_MAP)
# Cached grant rights (/api/auth/status) are invalid once the map changes.
GRANT_MAP_FINGERPRINT = os.getenv("GRANT_WORKFLOW_MAP") or ""
CAN_GRANT_TTL_S = 60

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


# One queue per connected console, so every audit event reaches EVERY open
# console (a single shared queue hands each event to only one competing
# reader - with two browsers the cards split randomly between them, and the
# grant beat needs millicent to see james's DENY card in her own console).
update_clients: set[Queue] = set()
update_clients_lock = threading.Lock()


def _broadcast_update(event: dict) -> None:
    """Fan an audit event out to every connected SSE client."""
    with update_clients_lock:
        clients = list(update_clients)
    for q in clients:
        q.put(event)


@app.route("/api/push-update", methods=["POST"])
def push_update():
    """Receive an audit/decision event (gateway or token webhook) and queue it for the SSE stream."""
    data = request.json or {}
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
    _broadcast_update(event)
    return jsonify({"ok": True})


@app.route("/api/updates")
def updates_sse():
    """Stream queued audit events to the browser as Server-Sent Events."""

    def event_stream():
        # Per-client queue registered for the broadcast fan-out.
        client_queue = Queue()
        with update_clients_lock:
            update_clients.add(client_queue)
        # Optional: Send a comment to keep the connection alive immediately
        yield ": connected\n\n"

        try:
            while True:
                try:
                    # Use a timeout so the loop can check for client disconnection
                    # and doesn't hang the thread forever
                    event = client_queue.get(timeout=20)
                    yield f"data: {json.dumps(event)}\n\n"
                except Empty:
                    # Send a "keep-alive" heart beat every 20 seconds
                    yield ": ping\n\n"
                except GeneratorExit:
                    # Clean up when the browser closes the connection
                    break
                except Exception as e:
                    logger.error("SSE Error: %s", e)  # noqa: TRY400
                    break
        finally:
            with update_clients_lock:
                update_clients.discard(client_queue)

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
    """Report chatbot health and the configured orchestrator target."""
    return jsonify(
        {
            "status": "healthy",
            "orchestrator": f"{ORCHESTRATOR_HOST}:{ORCHESTRATOR_PORT}",
        },
    )


def _id_token_display_name() -> str:
    """Best-effort display name from the session's id_token (unverified decode, cosmetic only)."""
    token = session.get("id_token") or ""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception:  # opaque/absent token: no name to show
        return ""
    return str(claims.get("preferred_username") or claims.get("name") or claims.get("email") or claims.get("sub") or "")


def _id_token_sub() -> str:
    """Return the session user's subject external id (id_token ``sub``, unverified decode)."""
    token = session.get("id_token") or ""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception:  # opaque/absent token: no subject available
        return ""
    return str(claims.get("sub") or "")


_MCP_PROTOCOL = "2026-07-28"
_MCP_LEGACY_PROTOCOL = "2025-11-25"


def _mcp_headers(token, method, name=None, session_id=None):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
        "Mcp-Method": method,
    }
    # Stateless calls carry the current revision; the legacy session handshake
    # (initialize and every call bound to its session id) carries the legacy one.
    if session_id:
        headers["Mcp-Session-Id"] = session_id
        headers["Mcp-Protocol-Version"] = _MCP_LEGACY_PROTOCOL
    else:
        headers["Mcp-Protocol-Version"] = _MCP_LEGACY_PROTOCOL if method == "initialize" else _MCP_PROTOCOL
    if name:
        headers["Mcp-Name"] = name
    return headers


def _mcp_message(resp):
    """JSON-RPC message from an MCP response body (plain JSON or an SSE stream)."""
    if resp.headers.get("content-type", "").startswith("text/event-stream"):
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return {}
    return resp.json() if resp.content else {}


def _mcp_ciq_rows(query_id, token):
    """Run a knowledge query on the IndyKite MCP server AS THE USER; return its rows.

    Stateless protocol first (self-contained tools/call); if the server only
    speaks the session style, fall back to initialize -> Mcp-Session-Id ->
    tools/call. The Bearer token is the console's user token - the same way
    the agents reach the platform through their MCP gateway.
    """
    call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "ciq_execute",
            "arguments": {"id": query_id, "input_params": {}},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": _MCP_PROTOCOL,
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": {"name": "chatbot", "version": "1.0"},
            },
        },
    }
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(MCP_SERVER_URL, headers=_mcp_headers(token, "tools/call", "ciq_execute"), json=call)
        if resp.status_code in (400, 404):
            # Legacy server: open a session, then call.
            init = client.post(
                MCP_SERVER_URL,
                headers=_mcp_headers(token, "initialize"),
                json={
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": _MCP_LEGACY_PROTOCOL,
                        "capabilities": {},
                        "clientInfo": {"name": "chatbot", "version": "1.0"},
                    },
                },
            )
            init.raise_for_status()
            sid = init.headers.get("mcp-session-id")
            if not sid:
                msg = "MCP initialize returned no session id"
                raise RuntimeError(msg)
            client.post(
                MCP_SERVER_URL,
                headers=_mcp_headers(token, "notifications/initialized", session_id=sid),
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
            legacy_call = {**call, "params": {"name": "ciq_execute", "arguments": call["params"]["arguments"]}}
            resp = client.post(
                MCP_SERVER_URL,
                headers=_mcp_headers(token, "tools/call", "ciq_execute", session_id=sid),
                json=legacy_call,
            )
        resp.raise_for_status()
    message = _mcp_message(resp)
    if message.get("error"):
        msg = f"MCP error: {message['error']}"
        raise RuntimeError(msg)
    content = (message.get("result") or {}).get("content") or []
    text = next((c.get("text") for c in content if c.get("type") == "text"), "") or ""
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        return payload.get("data") or payload.get("rows") or []
    return payload if isinstance(payload, list) else []


def _role_by_explain(explain: dict) -> str:
    """Staff leg of the explain queries, run with the app key.

    Rows exist iff the person WORKS_IN a department that CAN_TRIGGER the
    given workflow - the dataset's definition of staff. No user token involved.
    """
    subject = _id_token_sub()
    workflow = str(explain.get("workflow") or "").strip()
    if not (subject and workflow and EXPLAIN_ENABLED):
        if not EXPLAIN_ENABLED:
            logger.warning("staff check needs the explain queries configured (EXPLAIN_*_QUERY_ID)")
        return "unknown"
    try:
        rows = _ciq_execute(EXPLAIN_STAFF_QUERY_ID, {"subject_id": subject, "workflow_id": workflow})
    except httpx.HTTPError as e:
        logger.warning("staff check (explain %s on %s) failed: %s", subject, workflow, e)
        return "unknown"
    return "staff" if rows else "customer"


def _role_by_mcp_query(check: dict) -> str:
    """Knowledge query run AS THE USER on the IndyKite MCP server.

    A non-empty value in ``field`` of any returned row means staff. Only works
    where the MCP server accepts the console's user token directly.
    """
    query_id = str(check.get("query") or "").strip()
    field = str(check.get("field") or "").strip()
    token = session.get("access_token")
    if not (query_id and field and token and MCP_SERVER_URL):
        if query_id and not MCP_SERVER_URL:
            logger.warning("staff check configured but MCP_SERVER_URL is not set")
        return "unknown"
    try:
        rows = _mcp_ciq_rows(query_id, token)
    except (httpx.HTTPError, RuntimeError, ValueError) as e:
        logger.warning("staff check (%s via MCP) failed: %s", query_id, e)
        return "unknown"
    for row in rows:
        nodes = row.get("nodes") if isinstance(row, dict) else None
        if (nodes or {}).get(field) not in (None, ""):
            return "staff"
    return "customer"


def _resolve_role() -> str:
    """Tell staff from customers by the user's own graph relationships, not by a list.

    app.json's ``staff_check`` is one of:
      - ``{"explain": {"workflow": "wf1"}}`` - the explain queries' staff leg
        with the app key (preferred, already provisioned for the why? cards).
      - ``{"query": "get-self", "field": "department.property.name"}`` - a
        knowledge query as the user on the IndyKite MCP server.
    When the check is not configured or fails: "unknown" (every group shows).
    """
    check = APP_CONFIG.get("staff_check") or {}
    explain = check.get("explain") or {}
    if explain:
        return _role_by_explain(explain)
    return _role_by_mcp_query(check)


@app.route("/api/auth/status", methods=["GET"])
def auth_status():
    """Return whether the user is authenticated, who (header chip), and their role for the app layer."""
    logged_in = bool(session.get("access_token"))
    if not logged_in:
        return jsonify({"logged_in": False, "username": "", "role": "unknown"})
    role = session.get("role")
    if role not in ("staff", "customer"):
        role = _resolve_role()
        if role != "unknown":
            session["role"] = role
    # Grant rights follow the graph and the grant map, both of which change
    # during a demo: keep them for a minute, keyed to the current map.
    cached = session.get("can_grant")
    fresh = (
        isinstance(cached, dict)
        and cached.get("map") == GRANT_MAP_FINGERPRINT
        and datetime.now(UTC).timestamp() - float(cached.get("at") or 0) < CAN_GRANT_TTL_S
    )
    can_grant = cached["services"] if fresh else _grantable_services()
    if not fresh and can_grant is not None:
        session["can_grant"] = {
            "services": can_grant,
            "map": GRANT_MAP_FINGERPRINT,
            "at": datetime.now(UTC).timestamp(),
        }
    return jsonify(
        {
            "logged_in": True,
            "username": _id_token_display_name(),
            "subject": _id_token_sub(),
            "role": role,
            "can_grant": can_grant or [],
        },
    )


def _grantable_services() -> list[str] | None:
    """Return the grant-mapped services whose whole workflow bundle the user can trigger.

    Same AuthZEN self-check as /api/grant, run once per login so the app can
    show the staff inbox and grant buttons only to people whose clicks would
    succeed. None when the check could not run (not configured, platform
    unreachable), so it is retried on the next status call.
    """
    if not GRANT_ENABLED:
        return []
    caller = _id_token_sub()
    if not caller:
        return None
    try:
        return [svc for svc, wfs in GRANT_WORKFLOW_MAP.items() if wfs and _grant_guard_error(caller, wfs) is None]
    except httpx.HTTPError as e:
        logger.warning("grant self-check for %s failed: %s", caller, e)
        return None


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
def auth_callback():  # noqa: PLR0911, C901
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
            session["id_token"] = id_token
        return redirect("/")
    except httpx.HTTPStatusError as e:
        logger.warning("Token exchange failed: %s %s", e.response.status_code, e.response.text[:200])
        return redirect("/?error=token_exchange_failed")
    except Exception as e:
        logger.exception("Token exchange error")
        return redirect("/?error=" + urllib.parse.quote(str(e)[:100]))


def _ciq_execute(query_id, input_params):
    """Run one CIQ explain query against the platform; returns the data rows.

    _Application-subject policy: the app-agent key alone authenticates - no
    user token involved. An empty list means the policy matched nothing (for
    the explain queries: no such path in the graph), not an error.
    """
    resp = httpx.post(
        f"{INDYKITE_BASE_URL}/contx-iq/v1/execute",
        headers={"Content-Type": "application/json", "X-IK-ClientKey": APP_AGENT_CREDENTIALS_TOKEN},
        json={"id": query_id, "input_params": input_params},
        timeout=20.0,
    )
    resp.raise_for_status()
    return (resp.json() or {}).get("data") or []


def _authzen_can_trigger(subject_id, workflow_id):
    """Ask AuthZEN (live) whether subject_id may CAN_TRIGGER workflow_id right now."""
    resp = httpx.post(
        f"{INDYKITE_BASE_URL}/access/v1/evaluation",
        headers={"Content-Type": "application/json", "X-IK-ClientKey": APP_AGENT_CREDENTIALS_TOKEN},
        json={
            "subject": {"type": AUTHZEN_SUBJECT_TYPE, "id": subject_id},
            "resource": {"type": "Workflow", "id": workflow_id},
            "action": {"name": "CAN_TRIGGER"},
        },
        timeout=20.0,
    )
    resp.raise_for_status()
    return bool((resp.json() or {}).get("decision"))


def _capture_can_trigger(subject_id, workflow_ids, *, revoke=False):
    """Upsert (or delete) ``subject -CAN_TRIGGER-> Workflow`` edges via the Capture API.

    Authorization is data: this one write is what flips the next gateway
    decision from DENY to ALLOW (and back). Upserts are idempotent.
    """
    edges = [
        {
            "source": {"type": AUTHZEN_SUBJECT_TYPE, "external_id": subject_id},
            "target": {"type": "Workflow", "external_id": wf},
            "type": "CAN_TRIGGER",
            **({} if revoke else {"properties": []}),
        }
        for wf in workflow_ids
    ]
    path = "/capture/v1/relationships/delete" if revoke else "/capture/v1/relationships"
    resp = httpx.post(
        f"{INDYKITE_BASE_URL}{path}",
        headers={"Content-Type": "application/json", "X-IK-ClientKey": APP_AGENT_CREDENTIALS_TOKEN},
        json={"relationships": edges},
        timeout=30.0,
    )
    resp.raise_for_status()


def _row_node(row_nodes, alias, node_type):
    """Build one cytoscape node element from a CIQ row's alias.* keys, or None."""
    ext_id = row_nodes.get(f"{alias}.external_id")
    if not ext_id:
        return None
    label = row_nodes.get(f"{alias}.property.name") or row_nodes.get(f"{alias}.property.first_name") or ext_id
    return {"data": {"id": ext_id, "label": label, "type": node_type}}


def _explain_elements(subject_id, workflow_id):
    """Query both explain legs and map the rows to cytoscape elements.

    Edge types are known by construction (each leg's cypher fixes them), so
    the queries declare only nodes and the edges are emitted here.
    """
    params = {"subject_id": subject_id, "workflow_id": workflow_id}
    nodes, edges = {}, {}

    def add_node(element):
        if element:
            nodes[element["data"]["id"]] = element
        return element

    def add_edge(source, target, rel):
        if source and target:
            key = f"{source['data']['id']}->{rel}->{target['data']['id']}"
            edges[key] = {
                "data": {"id": key, "source": source["data"]["id"], "target": target["data"]["id"], "label": rel},
            }

    # Staff leg: subject -WORKS_IN-> department -CAN_TRIGGER-> workflow.
    # Alias "p" (insurance Person) or "u" (canbank User) - accept either.
    for row in _ciq_execute(EXPLAIN_STAFF_QUERY_ID, params):
        row_nodes = row.get("nodes") or {}
        alias = "p" if "p.external_id" in row_nodes else "u"
        subject = add_node(_row_node(row_nodes, alias, "Subject"))
        department = add_node(_row_node(row_nodes, "d", "Department"))
        workflow = add_node(_row_node(row_nodes, "wf", "Workflow"))
        add_edge(subject, department, "WORKS_IN")
        add_edge(department, workflow, "CAN_TRIGGER")

    # Direct leg: subject -CAN_TRIGGER-> workflow.
    for row in _ciq_execute(EXPLAIN_DIRECT_QUERY_ID, params):
        row_nodes = row.get("nodes") or {}
        alias = "p" if "p.external_id" in row_nodes else "u"
        subject = add_node(_row_node(row_nodes, alias, "Subject"))
        workflow = add_node(_row_node(row_nodes, "wf", "Workflow"))
        add_edge(subject, workflow, "CAN_TRIGGER")

    found = bool(edges)
    if not found:
        # Deny view: render the two endpoints disconnected.
        nodes[subject_id] = {"data": {"id": subject_id, "label": subject_id, "type": "Subject"}}
        nodes[workflow_id] = {"data": {"id": workflow_id, "label": workflow_id, "type": "Workflow"}}
    return {"found": found, "elements": {"nodes": list(nodes.values()), "edges": list(edges.values())}}


@app.route("/api/explain", methods=["GET"])
def explain_decision():
    """Explain an audit decision: return the live authorization path as cytoscape elements."""
    if not EXPLAIN_ENABLED:
        return jsonify({"error": "explain is not configured"}), 404
    # The route spends the app-agent credentials: when the console has a login
    # flow, require a session so anonymous callers can't probe the graph.
    if ID_SERVER_BASE_URL and not session.get("access_token"):
        return jsonify({"error": "authentication required"}), 401
    subject_id = (request.args.get("subject") or "").strip()
    workflow_id = (request.args.get("workflow") or "").strip()
    if not subject_id or not workflow_id:
        return jsonify({"error": "subject and workflow are required"}), 400
    try:
        return jsonify(_explain_elements(subject_id, workflow_id))
    except httpx.HTTPStatusError as e:
        logger.warning("Explain query failed: %s", e)
        return jsonify({"error": f"explain query failed: {e.response.status_code}"}), 502
    except httpx.HTTPError as e:
        logger.warning("Explain query unreachable: %s", e)
        return jsonify({"error": "platform unreachable"}), 502


def _grant_guard_error(caller, workflows):
    """Return the 403 reason if the caller may not grant these workflows, else None.

    The self-check is a live AuthZEN evaluation per workflow: only someone who
    can CAN_TRIGGER a workflow themselves may grant it to someone else.
    """
    if not caller:
        return "cannot identify the logged-in user"
    for wf in workflows:
        if not _authzen_can_trigger(caller, wf):
            return f"{caller} is not allowed to grant {wf} (no CAN_TRIGGER path of their own)"
    return None


@app.route("/api/grant", methods=["POST"])
def grant_access():
    """One-click remediate/revoke: write CAN_TRIGGER edges for a denied subject.

    The grant itself is authorization-guarded by a live AuthZEN self-check:
    the caller may only grant workflows they can trigger themselves
    (millicent passes via her department; james self-granting gets a 403).
    """
    if not GRANT_ENABLED:
        return jsonify({"error": "grant is not configured"}), 404
    # The route spends the app-agent credentials: when the console has a login
    # flow, require a session so anonymous callers can't write to the graph.
    if ID_SERVER_BASE_URL and not session.get("access_token"):
        return jsonify({"error": "authentication required"}), 401
    body = request.get_json(silent=True) or {}
    subject_id = str(body.get("subject") or "").strip()
    service = str(body.get("service") or "").strip()
    revoke = bool(body.get("revoke"))
    workflows = GRANT_WORKFLOW_MAP.get(service) or []
    if not subject_id or not workflows:
        return jsonify({"error": "subject and a grant-mapped service are required"}), 400
    caller = _id_token_sub()
    try:
        denied = _grant_guard_error(caller, workflows)
        if denied:
            return jsonify({"error": denied}), 403
        _capture_can_trigger(subject_id, workflows, revoke=revoke)
    except httpx.HTTPError as e:
        logger.warning("grant %s for %s failed: %s", "revoke" if revoke else "write", subject_id, e)
        return jsonify({"error": "platform unreachable"}), 502
    verb = "revoked" if revoke else "granted"
    logger.info("%s CAN_TRIGGER %s -> %s (by %s)", verb, subject_id, workflows, caller)
    # Tell every open console (the requester's app page included) that the
    # grant happened; the audit terminal ignores these non-gateway events.
    _broadcast_update(
        {
            "decision": "ACCESS_REVOKED" if revoke else "ACCESS_GRANTED",
            "subject": subject_id,
            "actor": caller,
            "service": service,
            "reason": ", ".join(workflows),
            "timestamp": _now_iso(),
        },
    )
    return jsonify(
        {
            verb: workflows,
            "subject": subject_id,
            # the -console workflow: what the why? modal should re-query
            "explain_workflow": workflows[-1],
        },
    )


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@app.route("/api/access-request", methods=["POST"])
def access_request():
    """Fan a denied user's access request out to every open console.

    Staff see it in their app's Support inbox and grant it there (which goes
    through /api/grant and its AuthZEN self-check). Nothing is written to the
    graph here - this is only the message.
    """
    if ID_SERVER_BASE_URL and not session.get("access_token"):
        return jsonify({"error": "authentication required"}), 401
    body = request.get_json(silent=True) or {}
    service = str(body.get("service") or "").strip()
    resource = str(body.get("resource") or "").strip()
    if not service:
        return jsonify({"error": "service is required"}), 400
    subject = _id_token_sub()
    if not subject:
        return jsonify({"error": "cannot identify the logged-in user"}), 400
    event = {
        "decision": "ACCESS_REQUESTED",
        "subject": subject,
        "requester": _id_token_display_name() or subject,
        "service": service,
        "resource": resource,
        "reason": f"{subject} asked for access to {resource or service}",
        "timestamp": _now_iso(),
    }
    _broadcast_update(event)
    logger.info("access request from %s for %s (%s)", subject, resource or service, service)
    return jsonify({"ok": True, "grantable": bool(GRANT_ENABLED and GRANT_WORKFLOW_MAP.get(service))})


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
    return jsonify(
        {
            "logout_url": logout_url,
            # why? buttons on the audit cards (hidden when not configured);
            # the map derives a DENY card's workflow id from its gateway name.
            "explain_enabled": EXPLAIN_ENABLED,
            "explain_workflow_map": EXPLAIN_WORKFLOW_MAP,
            # grant buttons on DENY cards (deny -> remediate -> allow)
            "grant_enabled": GRANT_ENABLED,
            "grant_map": GRANT_WORKFLOW_MAP,
            # architecture page: badge + dimming of profile-gated groups
            "usecase": USECASE,
            "profiles": COMPOSE_PROFILES,
            # usecase branding for the console chrome
            "org_name": ORG_NAME,
            "org_tagline": ORG_TAGLINE,
            # app layer: sections, action cards, prompts (usecases/<usecase>/app.json)
            "app": APP_CONFIG,
        },
    )


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

    # Run the orchestrator round-trip in a worker thread and keep bytes
    # flowing to the browser meanwhile: a response body that stays silent for
    # the whole task (5-200s) gets killed by stale keep-alives / middleboxes,
    # surfacing as "Error in input stream" with the finished answer lost.
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run_async)
            while True:
                try:
                    events = future.result(timeout=2.0)
                    break
                except concurrent.futures.TimeoutError:
                    yield ": ping\n\n"
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
    """Forward a prompt to the orchestrator and stream the response back as SSE."""
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
    """Forward a prompt to the orchestrator and return the full response as JSON (non-streaming)."""
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
    """Serve the chatbot single-page UI."""
    return send_from_directory("static", "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    """Serve a static asset (CSS, JS, images) from the static directory."""
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
