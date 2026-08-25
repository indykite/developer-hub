# Copyright (c) 2026 IndyKite
"""CRM agent - A2A-compliant agent that files Salesforce cases on behalf of a person.

The agent sits behind its own IAG gateway (workflow ``wf-crm``) and receives the
gateway-minted delegation token in ``Authorization: Bearer``. It then performs a
second, real token exchange: an OAuth 2.0 JWT Bearer assertion (RFC 7523) signed
with the connected app's private key is exchanged at the Salesforce token
endpoint for a Salesforce access token, and a Case is created through the
Salesforce REST API. The Case records who the request was filed on behalf of
(the delegation token's subject) and the agent actor chain, so the delegation
semantics stay visible end to end. Both tokens are reported to the console's
audit terminal as TOKEN cards.
"""

import asyncio
import base64
import json
import logging
import os
import time
import uuid
from contextlib import suppress
from pathlib import Path

import httpx
import jwt
import uvicorn
from a2a.helpers.proto_helpers import new_task_from_user_message, new_text_artifact, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.constants import DEFAULT_RPC_URL
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.exceptions import HTTPException

load_dotenv()

CRM_PORT = int(os.getenv("CRM_PORT", "6006"))
ADVERTISED_HOST = os.getenv("ADVERTISED_HOST", "crm")
CRM_AGENT_NAME = os.getenv("CRM_AGENT_NAME", "crm_agent")

# Salesforce connected-app configuration (JWT Bearer flow, RFC 7523). The
# private key pairs with the certificate uploaded to the connected app; the
# consumer key identifies it; the username is the pre-authorized integration
# user the minted access token acts as.
SF_LOGIN_URL = os.getenv("SF_LOGIN_URL", "https://login.salesforce.com").strip().rstrip("/")
SF_CONSUMER_KEY = os.getenv("SF_CONSUMER_KEY", "").strip()
SF_USERNAME = os.getenv("SF_USERNAME", "").strip()
SF_PRIVATE_KEY_FILE = os.getenv("SF_PRIVATE_KEY_FILE", "/app/keys/sf-jwt.key").strip()
SF_API_VERSION = os.getenv("SF_API_VERSION", "v61.0").strip()
SF_TIMEOUT = float(os.getenv("SF_TIMEOUT", "20"))
# How long a minted Salesforce access token is reused before re-minting. Kept
# well below typical org session timeouts; a 401 invalidates it early.
SF_TOKEN_TTL = float(os.getenv("SF_TOKEN_TTL", "600"))
# The console's second TOKEN card shows the Salesforce token REDACTED by
# default: unlike the IndyKite delegation token (short-lived, demo-scoped), it
# is a third-party credential. Set true to display it in full on stage.
SF_REPORT_FULL_TOKEN = os.getenv("SF_REPORT_FULL_TOKEN", "").lower() in ("true", "1", "yes")
# Lifetime of the RFC 7523 assertion itself (Salesforce rejects exp > 3 min out).
_SF_ASSERTION_LIFETIME_SECONDS = 180

HTTP_UNAUTHORIZED = 401

_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Third-party log verbosity is controlled separately from LOG_LEVEL: the A2A
# SDK alone logs every full protobuf event at DEBUG, drowning the agent's own
# narrative. Enforced by the handler filter below (no third-party logger is
# reconfigured); set LIB_LOG_LEVEL=DEBUG to see the SDK logs, condensed to
# one-line breadcrumbs.
_LIB_LOG_LEVEL = os.getenv("LIB_LOG_LEVEL", "INFO").upper()
_LIB_LOG_LEVELNO = {"CRITICAL": 50, "ERROR": 40, "WARNING": 30, "INFO": 20, "DEBUG": 10}.get(_LIB_LOG_LEVEL, 20)


class _CondenseLibLogs(logging.Filter):
    """Tame third-party log records at the handler, per LIB_LOG_LEVEL.

    Records from the noisy libraries are dropped below LIB_LOG_LEVEL, and the
    surviving multi-line payloads (the A2A SDK emits the same multi-KB
    protobuf dump several times in a second - once per state transition, per
    subscriber) are collapsed to their first line (task id + event type) plus
    a note of how much was elided.
    """

    _PREFIXES = ("a2a", "httpx", "httpcore")

    def filter(self, record: logging.LogRecord) -> bool:
        """Drop below-threshold third-party records; condense the rest."""
        if record.name.split(".", 1)[0] not in self._PREFIXES:
            return True
        if record.levelno < _LIB_LOG_LEVELNO:
            return False
        message = record.getMessage()
        first_line, newline, rest = message.partition("\n")
        if newline:
            record.msg = f"{first_line} ...(+{len(rest)} chars elided)"
            record.args = ()
        return True


for _root_handler in logging.getLogger().handlers:
    _root_handler.addFilter(_CondenseLibLogs())

_logger = logging.getLogger(__name__)

crm_card = AgentCard(
    name=CRM_AGENT_NAME,
    description=(
        "CRM agent that files Salesforce cases on behalf of a person, using the "
        "OAuth 2.0 JWT Bearer flow (RFC 7523) to obtain a Salesforce access token."
    ),
    version="1.0.0",
    provider={"organization": "Indykite", "url": "https://www.indykite.com"},
    capabilities=AgentCapabilities(
        streaming=True,
        push_notifications=False,
        extended_agent_card=False,
    ),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    supported_interfaces=[
        AgentInterface(
            protocol_binding="JSONRPC",
            url=f"http://{ADVERTISED_HOST}:{CRM_PORT}",
            protocol_version="1.0",
        ),
    ],
    skills=[
        AgentSkill(
            id="create-crm-case",
            name="Create CRM Case",
            description=(
                "File a support case in Salesforce on behalf of a person. The case "
                "records the delegated subject and the agent actor chain. Send the "
                "request as 'subject: <title>' on the first line and the case "
                "description below it; mention who the case is for."
            ),
            tags=["crm", "salesforce", "case", "ticket", "delegation"],
            examples=[
                (
                    "subject: Water backup claim\n"
                    "description: Open a case for James Mitchell about his water-backup claim."
                ),
                "Open a Salesforce case for James Mitchell about his home policy renewal",
                "File a CRM ticket about the Mitchell household teen-driver quote",
            ],
            input_modes=["text/plain"],
            output_modes=["text/plain"],
        ),
    ],
)


def _get_access_token_from_context(context: "RequestContext | None") -> str:
    """Extract Bearer token from incoming Authorization header."""
    if not context or not context.call_context:
        return ""
    req_headers = context.call_context.state.get("headers") or {}
    auth = req_headers.get("authorization") or req_headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return auth.strip() if auth else ""


CHATBOT_UPDATES_URL = os.getenv("CHATBOT_UPDATES_URL", "http://chatbot:3000/api/push-update").strip()


# Strong references to in-flight report tasks: without this the event loop
# holds only a weak reference and the task can be GC'd before it runs.
_background_tasks: set = set()


def _decode_claims(token: str) -> dict:
    """Best-effort unverified JWT payload decode; {} for opaque tokens."""
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception:  # opaque token: no claims available
        return {}


def _actor_chain(claims: dict) -> list[str]:
    """Flatten the nested RFC 8693 ``act`` claim into an ordered actor list."""
    chain: list[str] = []
    act = claims.get("act")
    while isinstance(act, dict):
        sub = act.get("sub")
        if sub:
            chain.append(sub)
        act = act.get("act")
    return chain


def _report_exchanged_token(token: str, *, subject: str = "", actor: str = "") -> None:
    """Post an exchanged token to the console's audit terminal (fire-and-forget).

    Called once for the IndyKite delegation token this agent received and once
    for the Salesforce access token it minted via RFC 7523, so the console
    shows both hops of the exchange chain as TOKEN cards. When subject/actor
    are not given they are read from the token's claims (JWTs only; Salesforce
    access tokens are opaque). Failures never affect the request.
    """
    if not CHATBOT_UPDATES_URL:
        return
    if not (subject and actor):
        claims = _decode_claims(token)
        subject = subject or claims.get("sub") or "?"
        actor = actor or (claims.get("act") or {}).get("sub") or "?"
    event = {
        "service": CRM_AGENT_NAME,
        "decision": "TOKEN_EXCHANGED",
        "subject": subject,
        "actor": actor,
        "reason": token,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    async def _post() -> None:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                await client.post(CHATBOT_UPDATES_URL, json=event)
        except Exception:
            _logger.debug("Exchanged-token report to the console failed")

    # no running loop (sync caller): skip reporting
    with suppress(RuntimeError):
        task = asyncio.get_running_loop().create_task(_post())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)


def _message_text(context: RequestContext) -> str:
    chunks: list[str] = []
    if context.message:
        for part in context.message.parts or []:
            text = getattr(part, "text", None)
            if text:
                chunks.append(text)
    return "".join(chunks).strip()


def _parse_case_fields(prompt: str) -> tuple[str, str]:
    """Split the prompt into (subject, description).

    The orchestrator's query_crm tool sends 'subject: <title>' on the first
    line with the description below; free-form text falls back to first-line /
    full-text. Deterministic on purpose - no LLM in this agent.
    """
    subject = ""
    description_lines: list[str] = []
    for line in prompt.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if not subject and lowered.startswith("subject:"):
            subject = stripped[len("subject:") :].strip()
            continue
        if lowered.startswith("description:"):
            description_lines.append(stripped[len("description:") :].strip())
            continue
        description_lines.append(stripped)
    description = "\n".join(line for line in description_lines if line).strip()
    if not subject:
        first_line = prompt.splitlines()[0].strip() if prompt else "Support case"
        subject = first_line[:255] or "Support case"
    if not description:
        description = prompt.strip()
    return subject[:255], description


class _SfConfigError(RuntimeError):
    """Raised when the Salesforce connected-app configuration is incomplete."""


def _sf_config_problems() -> list[str]:
    """List the missing pieces of the Salesforce configuration, if any."""
    problems = []
    if not SF_CONSUMER_KEY:
        problems.append("SF_CONSUMER_KEY is not set")
    if not SF_USERNAME:
        problems.append("SF_USERNAME is not set")
    if not Path(SF_PRIVATE_KEY_FILE).is_file():
        problems.append(f"private key not found at {SF_PRIVATE_KEY_FILE}")
    return problems


def _mint_sf_assertion() -> str:
    """Mint the RFC 7523 JWT Bearer assertion for the Salesforce token endpoint.

    iss = connected app consumer key, sub = pre-authorized integration user,
    aud = the login host, exp <= 3 minutes out - signed RS256 with the private
    key whose certificate is uploaded to the connected app.
    """
    problems = _sf_config_problems()
    if problems:
        raise _SfConfigError("; ".join(problems))
    private_key = Path(SF_PRIVATE_KEY_FILE).read_bytes()
    now = int(time.time())
    claims = {
        "iss": SF_CONSUMER_KEY,
        "sub": SF_USERNAME,
        "aud": SF_LOGIN_URL,
        "exp": now + _SF_ASSERTION_LIFETIME_SECONDS,
    }
    return jwt.encode(claims, private_key, algorithm="RS256")


# Minted Salesforce access token, reused across requests until TTL expiry or a
# 401 from the API invalidates it.
_sf_token_cache: dict = {"at": 0.0, "access_token": "", "instance_url": ""}  # nosec B105 - empty init, not a password


def _invalidate_sf_token() -> None:
    _sf_token_cache["at"] = 0.0
    _sf_token_cache["access_token"] = ""  # nosec B105 - cache reset, not a password


async def _get_sf_token(client: httpx.AsyncClient, *, on_behalf_of: str) -> tuple[str, str]:
    """Exchange the RFC 7523 assertion for a Salesforce access token.

    Returns (access_token, instance_url); serves from the short-lived cache
    when fresh. The freshly minted token is reported to the console as a TOKEN
    card with actor 'salesforce', making the second exchange hop visible.
    """
    now = time.monotonic()
    if _sf_token_cache["access_token"] and (now - _sf_token_cache["at"]) < SF_TOKEN_TTL:
        return _sf_token_cache["access_token"], _sf_token_cache["instance_url"]

    assertion = _mint_sf_assertion()
    response = await client.post(
        f"{SF_LOGIN_URL}/services/oauth2/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code != httpx.codes.OK or "access_token" not in body:
        detail = body.get("error_description") or body.get("error") or f"status {response.status_code}"
        message = f"Salesforce token exchange failed: {detail}"
        raise RuntimeError(message)
    # Fail fast on a malformed success: without instance_url every later API
    # call would target an empty base URL and fail confusingly.
    instance_url = (body.get("instance_url") or "").rstrip("/")
    if not instance_url:
        message = "Salesforce token exchange response missing instance_url"
        raise RuntimeError(message)

    sf_token = body["access_token"]
    _sf_token_cache["at"] = now
    _sf_token_cache["access_token"] = sf_token
    _sf_token_cache["instance_url"] = instance_url
    redacted = f"{sf_token[:6]}...{sf_token[-6:]}"
    _logger.info("Salesforce access token minted via RFC 7523 (redacted): %s", redacted)
    # Report the exchange to the console. The Salesforce token is a
    # third-party credential, so the card carries a redacted form unless
    # SF_REPORT_FULL_TOKEN explicitly opts into full display.
    reported = sf_token if SF_REPORT_FULL_TOKEN else f"{redacted} (Salesforce access token, redacted)"
    _report_exchanged_token(reported, subject=on_behalf_of, actor="salesforce")
    return sf_token, instance_url


async def _create_case(
    client: httpx.AsyncClient,
    sf_token: str,
    instance_url: str,
    subject: str,
    description: str,
) -> tuple[str, str]:
    """Create a Salesforce Case; returns (case_id, case_number - may be '')."""
    headers = {"Authorization": f"Bearer {sf_token}", "Content-Type": "application/json"}
    response = await client.post(
        f"{instance_url}/services/data/{SF_API_VERSION}/sobjects/Case",
        headers=headers,
        json={"Subject": subject, "Description": description, "Origin": "Web"},
    )
    if response.status_code == HTTP_UNAUTHORIZED:
        _invalidate_sf_token()
        message = "Salesforce rejected the access token (401) - it was invalidated; retry the request"
        raise RuntimeError(message)
    response.raise_for_status()
    case_id = (response.json() or {}).get("id") or ""

    # Best effort: the human-friendly CaseNumber needs a read-back.
    case_number = ""
    if case_id:
        try:
            read = await client.get(
                f"{instance_url}/services/data/{SF_API_VERSION}/sobjects/Case/{case_id}",
                headers=headers,
                params={"fields": "CaseNumber"},
            )
            if read.status_code == httpx.codes.OK:
                case_number = (read.json() or {}).get("CaseNumber") or ""
        except httpx.HTTPError:
            _logger.debug("CaseNumber read-back failed; answering with the id only")
    return case_id, case_number


async def _file_case(prompt: str, access_token: str) -> str:
    """Full CRM flow: parse fields, exchange for a SF token, create the Case.

    Trust boundary: the token's claims are decoded WITHOUT signature
    verification because the agent only receives tokens that crm-iag already
    introspected and minted - the agent's port is not published to the host
    (compose), so the gateway is the only caller. Do not expose this agent
    directly: a caller bypassing the gateway could spoof sub/act and the
    spoofed attribution would be persisted in the Salesforce Case.
    """
    claims = _decode_claims(access_token)
    on_behalf_of = claims.get("sub") or "?"
    chain = _actor_chain(claims)
    subject, description = _parse_case_fields(prompt)
    provenance = f"Filed on behalf of {on_behalf_of}"
    if chain:
        provenance += f" via agent chain {' -> '.join(chain)}"
    description = f"{description}\n\n[{provenance}]"

    async with httpx.AsyncClient(timeout=SF_TIMEOUT) as client:
        sf_token, instance_url = await _get_sf_token(client, on_behalf_of=on_behalf_of)
        case_id, case_number = await _create_case(client, sf_token, instance_url, subject, description)

    label = f"Case {case_number}" if case_number else f"Case {case_id}"
    link = f"{instance_url}/lightning/r/Case/{case_id}/view" if case_id else instance_url
    return (
        f"{label} created in Salesforce on behalf of {on_behalf_of}: '{subject}'. "
        f"The case records the delegation chain. View it at {link}"
    )


class CrmExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:  # noqa: D102
        access_token = _get_access_token_from_context(context)
        if not access_token:
            raise HTTPException(status_code=401, detail="Authorization required")
        # Demo feature: the short-lived delegation token is intentionally
        # surfaced in the console audit terminal to show the exchange chain;
        # logs carry only a redacted fingerprint to avoid credential leaks.
        _logger.info("Exchanged bearer token (redacted): %s...%s", access_token[:6], access_token[-6:])
        _report_exchanged_token(access_token)

        prompt = _message_text(context)
        _logger.info("Received message for %s: %s", CRM_AGENT_NAME, prompt)

        task = context.current_task or new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(
                    state=TaskState.TASK_STATE_WORKING,
                    message=new_text_message("Filing the Salesforce case..."),
                ),
            ),
        )

        try:
            result_text = await _file_case(prompt, access_token)
        except _SfConfigError as exc:
            _logger.warning("Salesforce configuration incomplete: %s", exc)
            result_text = f"The Salesforce connection is not configured: {exc}. Set the SF_* variables in .env."
        except Exception as exc:
            _logger.warning("Case creation failed: %s", exc)
            result_text = f"I couldn't create the Salesforce case: {exc}"

        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                artifact=new_text_artifact(name="result", text=result_text),
            ),
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            ),
        )
        _logger.info("CRM response: %s", result_text[:200])

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:  # noqa: D102
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id or str(uuid.uuid4()),
                status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
            ),
        )


if __name__ == "__main__":
    handler = DefaultRequestHandler(
        agent_executor=CrmExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=crm_card,
    )
    app = Starlette(
        routes=[
            *create_agent_card_routes(agent_card=crm_card),
            *create_jsonrpc_routes(request_handler=handler, rpc_url=DEFAULT_RPC_URL),
        ],
    )
    _logger.info("Starting %s on port %d", CRM_AGENT_NAME, CRM_PORT)
    _problems = _sf_config_problems()
    if _problems:
        _logger.warning("Salesforce not fully configured (%s) - cases will fail until fixed", "; ".join(_problems))
    else:
        _logger.info("Salesforce JWT Bearer configured for %s at %s", SF_USERNAME, SF_LOGIN_URL)
    uvicorn.run(
        app,
        host="0.0.0.0",  # nosec B104  # noqa: S104
        port=CRM_PORT,
        log_level="info",
    )
