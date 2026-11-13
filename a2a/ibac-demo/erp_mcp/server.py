# Copyright (c) 2026 IndyKite
"""ERP MCP server - Postgres invoices with graph-driven row filtering.

A deliberately ordinary "ERP" (one Postgres invoices table) exposed over MCP,
where every query is pre-filtered by IndyKite AuthZEN: before touching SQL the
server calls ``POST /access/v1/search/resource`` for the calling subject and
the returned invoice ids become the ``WHERE`` clause. The database knows
nothing about the graph; the graph (Department-SERVES->customer,
customer-HAS_INVOICE->Invoice, KBAC policies) decides which rows exist for
whom. Same prompt, different rows.

With IBAC on, the delegation token also carries the intent of the prompt, and
the search runs with the action of that intent: an intent about unpaid
invoices returns the unpaid ones only, not everything the caller may view.

Trust boundary - enforced by topology, not assumed: in compose this server
(and its database) lives ONLY on the private ``erp-backend-network``; the
sole other member is the erp-mcp-iag gateway, which bridges to the
client-facing network. No host port is published. Every request therefore
comes through the gateway, which introspects the caller and forwards the
delegation token it minted at the Token Service in ``X-IK-Token`` (beside the
user's own bearer in Authorization) - so the ``sub`` claim decoded here
(unverified) was already verified upstream. If this
service is ever deployed reachable by anything but its gateway, that
assumption breaks and the token MUST be verified or introspected here
before trusting ``sub``.
"""

import base64
import json
import logging
import os

import httpx
import psycopg
from mcp.server.mcpserver import Context, MCPServer

ERP_MCP_PORT = int(os.getenv("ERP_MCP_PORT", "8010"))
ERP_DB_DSN = os.getenv("ERP_DB_DSN", "postgresql://erp:erp@erp-db:5432/erp")
INDYKITE_BASE_URL = (os.getenv("INDYKITE_BASE_URL") or "").strip().rstrip("/")
APP_AGENT_CREDENTIALS_TOKEN = (os.getenv("APP_AGENT_CREDENTIALS_TOKEN") or "").strip()
# Subject node type of the callers (Person: every login of the ibac dataset).
# The compose file feeds it from AUTHZEN_SUBJECT_TYPES; only the first token
# is used.
ERP_SUBJECT_TYPE = ((os.getenv("ERP_SUBJECT_TYPE") or "Person").split() or ["Person"])[0]
ERP_ACTION = os.getenv("ERP_ACTION", "CAN_VIEW").strip()
ERP_RESOURCE_TYPE = os.getenv("ERP_RESOURCE_TYPE", "Invoice").strip()
SEARCH_TIMEOUT = float(os.getenv("ERP_SEARCH_TIMEOUT", "15"))

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_logger = logging.getLogger(__name__)

mcp = MCPServer("erp-invoices")

# What each tool is for, published in the `_meta` of its tools/list entry as
# `x-coaz-mappings`. A gateway with IBAC on compares a call with the intent in
# the delegated token: the subject type, the resource type and one of the
# actions have to match an evaluation of the intent, or the call is refused.
# A mapping that names ids also makes the gateway ask the platform, before the
# call reaches this server, whether the caller may do that to that resource:
# `$token.sub` is the subject of the delegated token, `$request.arguments.<name>`
# an argument of the call.
# The search behind both tools runs with the action of the intent, so the
# rows returned are the ones for the action that matched.
INVOICE_ACTION_NAMES = (ERP_ACTION, "VIEW_INVOICE", "VIEW_UNPAID_INVOICE")
_INVOICE_ACTIONS = [{"name": name} for name in INVOICE_ACTION_NAMES]
LIST_INVOICES_MAPPINGS = [
    {
        "context": {},
        "subject": [{"type": ERP_SUBJECT_TYPE}],
        "resource": [{"type": ERP_RESOURCE_TYPE}],
        "action": _INVOICE_ACTIONS,
    },
]
GET_INVOICE_MAPPINGS = [
    {
        "context": {},
        "subject": [{"type": ERP_SUBJECT_TYPE, "id": "$token.sub"}],
        "resource": [{"type": ERP_RESOURCE_TYPE, "id": "$request.arguments.invoice_id"}],
        "action": _INVOICE_ACTIONS,
    },
]
COAZ_MAPPINGS_META_KEY = "x-coaz-mappings"

_COLUMNS = (
    "external_id",
    "customer_id",
    "customer_name",
    "policy_number",
    "amount",
    "currency",
    "due_date",
    "status",
    "description",
)
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM invoices"  # nosec B608 - constant column list  # noqa: S608


def _decode_claims(token: str) -> dict:
    """Best-effort unverified JWT payload decode; {} for opaque tokens."""
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception:  # opaque token: no claims available
        return {}


def _claims_from_context(ctx: Context) -> dict:
    """Return the claims of the request's delegation token.

    The gateway minted and verified this delegation token; its ``sub`` is the
    authenticated person the whole chain acts on behalf of. In token-service
    mode it arrives in ``X-IK-Token`` next to the user's own bearer; without
    that header the Authorization bearer is the delegation token itself.
    """
    try:
        headers = ctx.request_context.request.headers
    except Exception:
        return {}
    token = headers.get("x-ik-token") or headers.get("X-IK-Token") or ""
    if not token:
        token = headers.get("authorization") or headers.get("Authorization") or ""
        if token.lower().startswith("bearer "):
            token = token[7:]
    return _decode_claims(token.strip())


def _current_intent(claims: dict) -> list[dict]:
    """Return the evaluations of the newest intent in the token's ``act`` chain, or [].

    Each hop of the chain is an ``act`` element nesting the previous one; a hop
    whose gateway asked the Intent Agent carries ``intent.evaluations``. The
    newest hop with an intent is the one a gateway checks a call against.
    """
    act = claims.get("act")
    while isinstance(act, dict):
        intent = act.get("intent")
        evaluations = intent.get("evaluations") if isinstance(intent, dict) else None
        if isinstance(evaluations, list) and evaluations:
            return [e for e in evaluations if isinstance(e, dict)]
        act = act.get("act")
    return []


def _invoice_actions(claims: dict) -> list[str]:
    """Return the actions the invoice search runs with for this request.

    The actions of the intent's evaluations about a caller of the ERP's subject
    type and an invoice, when the token carries such an intent: the answer
    then holds what the prompt asked for and nothing wider. Without one (IBAC
    off, or an intent about something else) the search runs with ERP_ACTION.
    """
    actions: list[str] = []
    for evaluation in _current_intent(claims):
        subject, resource, action = (evaluation.get(k) or {} for k in ("subject", "resource", "action"))
        if not all(isinstance(part, dict) for part in (subject, resource, action)):
            continue
        name = action.get("name")
        if (
            subject.get("type") == ERP_SUBJECT_TYPE
            and resource.get("type") == ERP_RESOURCE_TYPE
            and name in INVOICE_ACTION_NAMES
            and name not in actions
        ):
            actions.append(name)
    return actions or [ERP_ACTION]


def _allowed_invoice_ids(subject_id: str, actions: list[str]) -> list[str]:
    """Ask AuthZEN search/resource which invoices the subject may see under any of the actions.

    Returns the allowed external_ids, each once; an empty list is a normal
    answer (the KBAC policies matched nothing for this subject).
    """
    allowed: list[str] = []
    for action in actions:
        allowed.extend(i for i in _search_invoice_ids(subject_id, action) if i not in allowed)
    return allowed


def _search_invoice_ids(subject_id: str, action: str) -> list[str]:
    """Run one AuthZEN search/resource: the invoices the subject may `action`."""
    response = httpx.post(
        f"{INDYKITE_BASE_URL}/access/v1/search/resource",
        headers={"Content-Type": "application/json", "X-IK-ClientKey": APP_AGENT_CREDENTIALS_TOKEN},
        json={
            "subject": {"type": ERP_SUBJECT_TYPE, "id": subject_id},
            "resource": {"type": ERP_RESOURCE_TYPE},
            "action": {"name": action},
        },
        timeout=SEARCH_TIMEOUT,
    )
    response.raise_for_status()
    # Fold parse problems into httpx.HTTPError so the tools' existing
    # error handling covers a non-JSON or unexpectedly-shaped body too.
    try:
        payload = response.json()
    except ValueError as exc:
        message = f"search/resource returned a non-JSON response: {exc}"
        raise httpx.HTTPError(message) from exc
    if not isinstance(payload, dict):
        message = "search/resource returned an unexpected JSON shape"
        raise httpx.HTTPError(message)
    results = payload.get("results") or []
    return [r["id"] for r in results if isinstance(r, dict) and r.get("id")]


def _fetch_rows(invoice_ids: list[str]) -> list[dict]:
    """Read the allowed invoice rows from Postgres (empty ids -> no query)."""
    if not invoice_ids:
        return []
    with psycopg.connect(ERP_DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(f"{_SELECT} WHERE external_id = ANY(%s) ORDER BY due_date", (invoice_ids,))
        return [dict(zip(_COLUMNS, row, strict=True)) for row in cur.fetchall()]


def _rows_as_json(rows: list[dict]) -> list[dict]:
    """Make rows JSON-safe (dates and decimals to strings)."""
    return [{k: (v if isinstance(v, (int, float)) or v is None else str(v)) for k, v in row.items()} for row in rows]


@mcp.tool(meta={COAZ_MAPPINGS_META_KEY: LIST_INVOICES_MAPPINGS})
def list_invoices(ctx: Context) -> str:
    """List the invoices the calling user is allowed to see.

    The rows are pre-filtered by IndyKite authorization (AuthZEN
    search/resource over the knowledge graph) before the SQL query runs -
    the answer already IS the caller's complete visible set. When the
    request carries an intent, the set is the one for the intent's action.
    """
    claims = _claims_from_context(ctx)
    subject_id = str(claims.get("sub") or "")
    if not subject_id:
        return json.dumps({"error": "no authenticated subject on the request"})
    actions = _invoice_actions(claims)
    try:
        allowed = _allowed_invoice_ids(subject_id, actions)
    except httpx.HTTPError as exc:
        _logger.warning("search/resource failed for %s: %s", subject_id, exc)
        return json.dumps({"error": f"authorization search failed: {exc}"})
    try:
        rows = _fetch_rows(allowed)
    except psycopg.Error as exc:
        _logger.warning("ERP database query failed for %s: %s", subject_id, exc)
        return json.dumps({"error": f"ERP database unavailable: {exc}"})
    _logger.info(
        "list_invoices: subject=%s actions=%s allowed=%d rows=%d",
        subject_id,
        ",".join(actions),
        len(allowed),
        len(rows),
    )
    return json.dumps(
        {
            "subject": subject_id,
            "actions": actions,
            "authorized_invoice_count": len(allowed),
            "invoices": _rows_as_json(rows),
            "note": (
                f"These are ALL invoices visible to {subject_id} for {' or '.join(actions)}; "
                "the set was determined by the knowledge graph (AuthZEN "
                "search/resource), not by this database."
            ),
        },
    )


@mcp.tool(meta={COAZ_MAPPINGS_META_KEY: GET_INVOICE_MAPPINGS})
def get_invoice(invoice_id: str, ctx: Context) -> str:
    """Read one invoice by id - only if the calling user is authorized to see it.

    Membership is checked against the same AuthZEN search result that filters
    list_invoices, so there is no bypass path for guessing ids.
    """
    claims = _claims_from_context(ctx)
    subject_id = str(claims.get("sub") or "")
    if not subject_id:
        return json.dumps({"error": "no authenticated subject on the request"})
    actions = _invoice_actions(claims)
    try:
        allowed = _allowed_invoice_ids(subject_id, actions)
    except httpx.HTTPError as exc:
        _logger.warning("search/resource failed for %s: %s", subject_id, exc)
        return json.dumps({"error": f"authorization search failed: {exc}"})
    if invoice_id not in allowed:
        _logger.info("get_invoice DENIED: subject=%s invoice=%s", subject_id, invoice_id)
        return json.dumps(
            {
                "error": f"{subject_id} is not authorized to view invoice {invoice_id}",
                "reason": f"no {' or '.join(actions)} path in the knowledge graph connects them",
            },
        )
    try:
        rows = _fetch_rows([invoice_id])
    except psycopg.Error as exc:
        _logger.warning("ERP database query failed for %s: %s", subject_id, exc)
        return json.dumps({"error": f"ERP database unavailable: {exc}"})
    if not rows:
        return json.dumps({"error": f"invoice {invoice_id} is authorized but not in the ERP database"})
    return json.dumps({"subject": subject_id, "invoice": _rows_as_json(rows)[0]})


if __name__ == "__main__":
    problems = []
    if not INDYKITE_BASE_URL:
        problems.append("INDYKITE_BASE_URL is not set")
    if not APP_AGENT_CREDENTIALS_TOKEN:
        problems.append("APP_AGENT_CREDENTIALS_TOKEN is not set")
    if problems:
        _logger.warning("ERP authorization not fully configured (%s) - queries will fail", "; ".join(problems))
    _logger.info(
        "Starting erp-mcp on port %d (subject type %s, action %s, resource %s)",
        ERP_MCP_PORT,
        ERP_SUBJECT_TYPE,
        ERP_ACTION,
        ERP_RESOURCE_TYPE,
    )
    mcp.run(transport="streamable-http", host="0.0.0.0", port=ERP_MCP_PORT)  # nosec B104  # noqa: S104
