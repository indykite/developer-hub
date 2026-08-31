# Copyright (c) 2026 IndyKite
"""ERP MCP server - Postgres invoices with graph-driven row filtering.

A deliberately ordinary "ERP" (one Postgres invoices table) exposed over MCP,
where every query is pre-filtered by IndyKite AuthZEN: before touching SQL the
server calls ``POST /access/v1/search/resource`` for the calling subject and
the returned invoice ids become the ``WHERE`` clause. The database knows
nothing about the graph; the graph (Department-SERVES->customer,
customer-HAS_INVOICE->Invoice, KBAC policies) decides which rows exist for
whom. Same prompt, different rows.

Trust boundary - enforced by topology, not assumed: in compose this server
(and its database) lives ONLY on the private ``erp-backend-network``; the
sole other member is the erp-mcp-iag gateway, which bridges to the
client-facing network. No host port is published. Every request therefore
comes through the gateway, which introspects the caller and replaces the
Authorization header with its own minted delegation token - so the ``sub``
claim decoded here (unverified) was already verified upstream. If this
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
from mcp.server.fastmcp import Context, FastMCP

ERP_MCP_PORT = int(os.getenv("ERP_MCP_PORT", "8010"))
ERP_DB_DSN = os.getenv("ERP_DB_DSN", "postgresql://erp:erp@erp-db:5432/erp")
INDYKITE_BASE_URL = (os.getenv("INDYKITE_BASE_URL") or "").strip().rstrip("/")
APP_AGENT_CREDENTIALS_TOKEN = (os.getenv("APP_AGENT_CREDENTIALS_TOKEN") or "").strip()
# Subject node type per usecase (insurance: Person, canbank: User). The compose
# file feeds it from AUTHZEN_SUBJECT_TYPES; only the first token is used.
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

mcp = FastMCP("erp-invoices", host="0.0.0.0", port=ERP_MCP_PORT)  # nosec B104  # noqa: S104

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


def _subject_from_context(ctx: Context) -> str:
    """Extract the calling subject's external_id from the request's bearer token.

    The gateway minted and verified this delegation token; its ``sub`` is the
    authenticated person the whole chain acts on behalf of.
    """
    try:
        headers = ctx.request_context.request.headers
    except Exception:
        return ""
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        auth = auth[7:]
    return str(_decode_claims(auth.strip()).get("sub") or "")


def _allowed_invoice_ids(subject_id: str) -> list[str]:
    """Ask AuthZEN search/resource which invoices the subject may CAN_VIEW.

    Returns the allowed external_ids; an empty list is a normal answer (the
    KBAC policies matched nothing for this subject).
    """
    response = httpx.post(
        f"{INDYKITE_BASE_URL}/access/v1/search/resource",
        headers={"Content-Type": "application/json", "X-IK-ClientKey": APP_AGENT_CREDENTIALS_TOKEN},
        json={
            "subject": {"type": ERP_SUBJECT_TYPE, "id": subject_id},
            "resource": {"type": ERP_RESOURCE_TYPE},
            "action": {"name": ERP_ACTION},
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


@mcp.tool()
def list_invoices(ctx: Context) -> str:
    """List the invoices the calling user is allowed to see.

    The rows are pre-filtered by IndyKite authorization (AuthZEN
    search/resource over the knowledge graph) before the SQL query runs -
    the answer already IS the caller's complete visible set.
    """
    subject_id = _subject_from_context(ctx)
    if not subject_id:
        return json.dumps({"error": "no authenticated subject on the request"})
    try:
        allowed = _allowed_invoice_ids(subject_id)
    except httpx.HTTPError as exc:
        _logger.warning("search/resource failed for %s: %s", subject_id, exc)
        return json.dumps({"error": f"authorization search failed: {exc}"})
    try:
        rows = _fetch_rows(allowed)
    except psycopg.Error as exc:
        _logger.warning("ERP database query failed for %s: %s", subject_id, exc)
        return json.dumps({"error": f"ERP database unavailable: {exc}"})
    _logger.info("list_invoices: subject=%s allowed=%d rows=%d", subject_id, len(allowed), len(rows))
    return json.dumps(
        {
            "subject": subject_id,
            "authorized_invoice_count": len(allowed),
            "invoices": _rows_as_json(rows),
            "note": (
                f"These are ALL invoices visible to {subject_id}; the set was "
                "determined by the knowledge graph (AuthZEN search/resource), "
                "not by this database."
            ),
        },
    )


@mcp.tool()
def get_invoice(invoice_id: str, ctx: Context) -> str:
    """Read one invoice by id - only if the calling user is authorized to see it.

    Membership is checked against the same AuthZEN search result that filters
    list_invoices, so there is no bypass path for guessing ids.
    """
    subject_id = _subject_from_context(ctx)
    if not subject_id:
        return json.dumps({"error": "no authenticated subject on the request"})
    try:
        allowed = _allowed_invoice_ids(subject_id)
    except httpx.HTTPError as exc:
        _logger.warning("search/resource failed for %s: %s", subject_id, exc)
        return json.dumps({"error": f"authorization search failed: {exc}"})
    if invoice_id not in allowed:
        _logger.info("get_invoice DENIED: subject=%s invoice=%s", subject_id, invoice_id)
        return json.dumps(
            {
                "error": f"{subject_id} is not authorized to view invoice {invoice_id}",
                "reason": "no CAN_VIEW path in the knowledge graph connects them",
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
    mcp.run(transport="streamable-http")
