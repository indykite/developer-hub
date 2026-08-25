# Copyright (c) 2026 IndyKite
"""Orchestrator agent - A2A-compliant agent (a2a-sdk>=1.1.0) that receives and relays messages to the retriever."""

import asyncio
import base64
import json
import logging
import os
import re
import time
import uuid
from contextlib import suppress
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import httpx
import uvicorn
import yaml
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
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
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageRequest,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.constants import DEFAULT_RPC_URL
from dotenv import load_dotenv
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool, tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama.chat_models import ChatOllama
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.exceptions import HTTPException

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ORCHESTRATOR_PORT = int(os.getenv("ORCHESTRATOR_PORT", "6001"))
ADVERTISED_HOST = os.getenv("ADVERTISED_HOST", "orchestrator")
ORCHESTRATOR_AGENT_NAME = os.getenv("ORCHESTRATOR_AGENT_NAME", "orchestrator_agent")
LLM_MODEL = os.getenv("LLM_MODEL", "mistral-nemo:latest")
GEMINI_ENABLED = os.getenv("GEMINI_ENABLED", os.getenv("GEMENI_ENABLED", "")).lower() in ("true", "1", "yes")
GEMINI_DISABLED = os.getenv("GEMINI_ENABLED", os.getenv("GEMENI_ENABLED", "")).lower() in ("false", "0", "no")
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY", "") or "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
RETRIEVER_HOST = os.getenv("RETRIEVER_HOST", "retriever")
RETRIEVER_PORT = int(os.getenv("RETRIEVER_PORT", "6002"))
RETRIEVER_URL = (os.getenv("RETRIEVER_URL", "").strip() or f"http://{RETRIEVER_HOST}:{RETRIEVER_PORT}").rstrip("/")
WEATHER_HOST = os.getenv("WEATHER_HOST", "weather")
WEATHER_PORT = int(os.getenv("WEATHER_PORT", "6004"))
WEATHER_URL = (os.getenv("WEATHER_URL", "").strip() or f"http://{WEATHER_HOST}:{WEATHER_PORT}").rstrip("/")
# The analyst gateway relays Google Drive prompts (analyst -> drive-mcp-iag).
# Empty ANALYST_HOST disables the query_drive tool (base demo without Drive).
ANALYST_HOST = os.getenv("ANALYST_HOST", "").strip()
ANALYST_PORT = int(os.getenv("ANALYST_PORT", "8885"))
_ANALYST_DEFAULT_URL = f"http://{ANALYST_HOST}:{ANALYST_PORT}" if ANALYST_HOST else ""
ANALYST_URL = (os.getenv("ANALYST_URL", "").strip() or _ANALYST_DEFAULT_URL).rstrip("/")
# The CRM gateway relays Salesforce case prompts (crm-iag -> crm agent).
# Empty CRM_HOST disables the query_crm tool (usecases without a CRM story).
CRM_HOST = os.getenv("CRM_HOST", "").strip()
CRM_PORT = int(os.getenv("CRM_PORT", "8888"))
_CRM_DEFAULT_URL = f"http://{CRM_HOST}:{CRM_PORT}" if CRM_HOST else ""
CRM_URL = (os.getenv("CRM_URL", "").strip() or _CRM_DEFAULT_URL).rstrip("/")
ORCHESTRATOR_TIMEOUT = float(os.getenv("ORCHESTRATOR_TIMEOUT", "300"))
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("ddgs").setLevel(logging.WARNING)

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

    _PREFIXES = ("a2a", "mcp", "httpx", "httpcore")

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

# ---------------------------------------------------------------------------
# LLM setup
# ---------------------------------------------------------------------------
if GEMINI_API_KEY and (GEMINI_ENABLED or not GEMINI_DISABLED):
    _llm = ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        api_key=GEMINI_API_KEY,
        temperature=0,
    )
else:
    ollama_base_url = os.getenv("OLLAMA_HOST", "").strip()
    _llm = ChatOllama(
        model=LLM_MODEL,
        base_url=ollama_base_url or "http://localhost:11434",
        temperature=0,
    )

_search_tool = DuckDuckGoSearchRun()

# ---------------------------------------------------------------------------
# Context var: access token forwarded from the current inbound request
# ---------------------------------------------------------------------------
_current_access_token: ContextVar[str] = ContextVar("current_access_token", default="")


def _get_access_token_from_context(context: "RequestContext | None") -> str:
    """Extract Bearer token from the Authorization header of the incoming request."""
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


def _report_exchanged_token(token: str) -> None:
    """Post the exchanged bearer token to the console's audit terminal (fire-and-forget).

    The gateways' audit events carry the decision and actors chain but not the
    minted delegation token itself, so each agent reports the token it
    received; the console renders it as a TOKEN card. Failures never affect
    the request.
    """
    if not CHATBOT_UPDATES_URL:
        return
    subject = actor = "?"
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        subject = claims.get("sub") or "?"
        actor = (claims.get("act") or {}).get("sub") or "?"
    except Exception:  # nosec B110 - opaque token: report it without claims  # noqa: S110
        pass
    event = {
        "service": ORCHESTRATOR_AGENT_NAME,
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


# ---------------------------------------------------------------------------
# Helper: extract text from a Task's artifacts (SDK 1.0 proto Part)
# ---------------------------------------------------------------------------
def _extract_text_from_task(obj: Any) -> str:  # noqa: ANN401
    """Extract plain text from a Task's artifacts."""
    if obj is None:
        return ""
    if isinstance(obj, Message):
        # Message.parts is a list[Part]; in 1.0 Part has a .text field directly.
        chunks: list[str] = []
        for part in getattr(obj, "parts", []) or []:
            text = getattr(part, "text", None)
            if text:
                chunks.append(text)
        return "".join(chunks)
    if hasattr(obj, "artifacts") and obj.artifacts:
        chunks = []
        for artifact in obj.artifacts:
            for part in getattr(artifact, "parts", []) or []:
                text = getattr(part, "text", None)
                if text:
                    chunks.append(text)
        return "".join(chunks) if chunks else ""
    return ""


# ---------------------------------------------------------------------------
# A2A client helpers - send text to downstream agents
# ---------------------------------------------------------------------------


def _build_client(url: str, token: str) -> tuple["ClientFactory", "AgentCard"]:
    """Build a ClientFactory + minimal card pointing at *url*."""
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    config = ClientConfig(
        httpx_client=httpx.AsyncClient(
            timeout=httpx.Timeout(ORCHESTRATOR_TIMEOUT),
            headers=headers,
        ),
    )
    # Resolve the remote agent card (synchronously bootstrapped per call; cheap for our use-case).
    # We use a minimal card because we know the URL and don't need full discovery for routing.
    factory = ClientFactory(config=config)
    return factory, url


async def _call_agent(base_url: str, text: str, token: str) -> str:  # noqa: C901,PLR0911
    """Send *text* to a downstream A2A gateway and return the response text.

    Speaks raw JSON-RPC 2.0 directly — no card resolution or SDK client.
    """
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    timeout = httpx.Timeout(ORCHESTRATOR_TIMEOUT)

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        # ── 1. Send the message ──────────────────────────────────────────────
        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "ROLE_USER",
                    "parts": [{"text": text}],
                    "messageId": uuid.uuid4().hex,
                },
            },
        }
        try:
            resp = await client.post(base_url, json=payload)
            resp.raise_for_status()
        except Exception as e:
            _logger.warning("Agent send failed (%s): %s", base_url, e)
            return f"Agent unavailable: {e}"

        body = resp.json()
        if "error" in body:
            _logger.warning("JSON-RPC error from agent: %s", body["error"])
            return ""

        result = body.get("result") or {}

        # ── 2. Check for immediate terminal result ───────────────────────────
        state = (result.get("status") or {}).get("state")
        if state in ("TASK_STATE_COMPLETED", "completed"):
            return _extract_text_from_gateway_result(result)
        if state in (
            "TASK_STATE_FAILED",
            "TASK_STATE_REJECTED",
            "TASK_STATE_CANCELED",
            "failed",
            "rejected",
            "canceled",
            "cancelled",
        ):
            _logger.warning("Agent task ended immediately with state: %s", state)
            return ""

        # Direct Message response (no task lifecycle)
        if result.get("parts"):
            return _extract_text_from_gateway_result(result)

        # ── 3. Poll until terminal ───────────────────────────────────────────
        task_id = result.get("id")
        if not task_id:
            _logger.warning("No task id in agent response from %s: %s", base_url, result)
            return ""

        _logger.info("Polling agent task: %s", task_id)
        # Poll fast: downstream answers are often ready within seconds, and a
        # coarse interval adds straight latency to every user prompt.
        poll_interval = 0.5
        max_polls = int(ORCHESTRATOR_TIMEOUT / poll_interval)

        for _ in range(max_polls):
            await asyncio.sleep(poll_interval)
            poll_payload = {
                "jsonrpc": "2.0",
                "id": uuid.uuid4().hex,
                "method": "tasks/get",
                "params": {"id": task_id},
            }
            try:
                poll_resp = await client.post(base_url, json=poll_payload)
                poll_resp.raise_for_status()
            except Exception as e:
                _logger.warning("Error polling agent task %s: %s", task_id, e)
                await asyncio.sleep(poll_interval)
                continue

            poll_body = poll_resp.json()
            if "error" in poll_body:
                _logger.warning("JSON-RPC poll error for task %s: %s", task_id, poll_body["error"])
                return ""

            poll_result = poll_body.get("result") or {}
            state = (poll_result.get("status") or {}).get("state")
            _logger.info("Agent task %s state: %s", task_id, state)

            if state in ("TASK_STATE_COMPLETED", "completed"):
                return _extract_text_from_gateway_result(poll_result)
            if state in (
                "TASK_STATE_FAILED",
                "TASK_STATE_REJECTED",
                "TASK_STATE_CANCELED",
                "failed",
                "rejected",
                "canceled",
                "cancelled",
            ):
                _logger.warning("Agent task %s ended with state: %s", task_id, state)
                return ""

        _logger.warning("Agent task %s timed out", task_id)
        return ""


def _extract_text_from_gateway_result(result: dict) -> str:
    """Extract plain text from a raw JSON-RPC result dict."""
    # Task shape: artifacts[].parts[].text
    for artifact in result.get("artifacts") or []:
        chunks = [p["text"] for p in artifact.get("parts") or [] if p.get("text")]
        if chunks:
            return "".join(chunks)
    # Direct Message shape: parts[].text
    chunks = [p["text"] for p in result.get("parts") or [] if p.get("text")]
    return "".join(chunks)


async def _call_retriever(text: str) -> str:
    """Send *text* to the retriever agent."""
    return await _call_agent(RETRIEVER_URL, text, _current_access_token.get())


async def _call_weather(text: str) -> str:
    """Send *text* to the weather agent."""
    return await _call_agent(WEATHER_URL, text, _current_access_token.get())


async def _call_analyst(text: str) -> str:
    """Send *text* to the analyst agent (Google Drive access)."""
    return await _call_agent(ANALYST_URL, text, _current_access_token.get())


async def _call_crm(text: str) -> str:
    """Send *text* to the CRM agent (Salesforce cases)."""
    return await _call_agent(CRM_URL, text, _current_access_token.get())


async def _call_agent_a2a(base_url: str, text: str, token: str) -> str:  # noqa: C901, PLR0912
    """Send *text* to a downstream A2A agent using card resolution and the SDK client."""
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    httpx_client = httpx.AsyncClient(
        timeout=httpx.Timeout(ORCHESTRATOR_TIMEOUT),
        headers=headers,
    )
    try:
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=base_url)
        try:
            card = await resolver.get_agent_card()
        except Exception as e:
            _logger.warning("Could not resolve agent card at %s: %s", base_url, e)
            return ""

        factory = ClientFactory(config=ClientConfig(httpx_client=httpx_client))
        client = factory.create(card)

        message = Message(
            role=Role.ROLE_USER,
            parts=[Part(text=text)],
            message_id=uuid.uuid4().hex,
        )
        request = SendMessageRequest(message=message)

        task_id: str | None = None
        artifact_text = ""
        event_count = 0

        # a2a-sdk 1.1.0: send_message yields proto StreamResponse objects, each a
        # oneof payload of {task, message, status_update, artifact_update}. A
        # direct `message` reply is returned immediately; otherwise we track the
        # task id and accumulate any artifact text, then fetch the final task.
        async for event in client.send_message(request):
            event_count += 1
            which = event.WhichOneof("payload")
            _logger.debug("send_message event payload: %s", which)

            if which == "message":
                extracted = _extract_text_from_task(event.message)
                if extracted:
                    return extracted
            elif which == "task":
                task_id = event.task.id or task_id
                extracted = _extract_text_from_task(event.task)
                if extracted:
                    artifact_text = extracted
            elif which == "artifact_update":
                for part in event.artifact_update.artifact.parts:
                    if part.text:
                        artifact_text += part.text
            elif which == "status_update":
                task_id = event.status_update.task_id or task_id

        _logger.info("Event loop done. event_count=%d task_id=%s", event_count, task_id)

        if artifact_text:
            return artifact_text

        if task_id:
            try:
                task = await client.get_task(GetTaskRequest(id=task_id))
                _logger.info("Fetched task %s — artifacts: %r", task_id, getattr(task, "artifacts", None))
                extracted = _extract_text_from_task(task)
                _logger.info("Extracted text (len=%d): %s", len(extracted), extracted[:200])
                return extracted  # noqa: TRY300
            except Exception as e:
                _logger.warning("Failed to fetch A2A agent task %s: %s", task_id, e)

        _logger.warning("_call_agent_a2a returning empty — task_id=%s event_count=%d", task_id, event_count)
        return ""
    finally:
        await httpx_client.aclose()


# ---------------------------------------------------------------------------
# LangChain tool: query_retriever
# ---------------------------------------------------------------------------


@tool
async def query_retriever(query: str) -> str:
    """Forward the user's question to the data retriever agent.

    Use this for: MCP, MCP tools or resources, data retrieval, documents,
    questions about employees, the enterprise, authorization (AuthZEN), or any
    internal/knowledge-graph data. The retriever uses an MCP server with access
    to enterprise data.
    """
    return await _call_retriever(query)


# ---------------------------------------------------------------------------
# LangChain tool: query_weather
# ---------------------------------------------------------------------------


@tool
async def query_weather(query: str) -> str:
    """Forward the user's question to the weather agent.

    Use this for: weather, temperature, forecast, rain, wind, city conditions.
    """
    return await _call_weather(query)


# ---------------------------------------------------------------------------
# LangChain tool: query_drive
# ---------------------------------------------------------------------------


@tool
async def query_drive(query: str) -> str:
    """Forward the user's question to the analyst agent for Google Drive.

    Use this for: Google Drive, Drive files or folders, searching Drive,
    reading or summarizing a document stored in Google Drive.
    """
    # The downstream analyst routes by tool prefix; make the Drive intent
    # explicit so a rephrased query ("search for files named X") cannot be
    # routed to the knowledge-graph tools instead.
    return await _call_analyst(
        f"Google Drive request - answer ONLY with the drive_* tools (e.g. drive_search), "
        f"never with knowledge-graph or CIQ tools: {query}",
    )


# ---------------------------------------------------------------------------
# LangChain tool: query_crm
# ---------------------------------------------------------------------------


@tool
async def query_crm(query: str) -> str:
    """Forward a Salesforce case request to the CRM agent.

    Use this for: opening/filing a support case or ticket in Salesforce or the
    CRM, on behalf of a customer. Compose the argument as
    'subject: <short case title>' on the first line, then the case description
    (include who the case is for, e.g. 'on behalf of James Mitchell').
    """
    return await _call_crm(query)


# ---------------------------------------------------------------------------
# Agent Skills (agentskills.io) - discovery & skill catalog
# ---------------------------------------------------------------------------

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def _parse_skill_file(location: Path) -> dict[str, Any] | None:  # noqa: C901,PLR0911,PLR0912
    """Parse a SKILL.md file: extract YAML frontmatter and body. Returns skill record or None."""
    try:
        raw = location.read_text(encoding="utf-8")
    except OSError as e:
        _logger.warning("Could not read skill file %s: %s", location, e)
        return None
    if not raw.strip():
        return None
    parts = re.split(r"^---\s*$", raw.strip(), maxsplit=2, flags=re.MULTILINE)
    if len(parts) < 3:  # noqa: PLR2004
        _logger.warning("Skill file %s has no valid frontmatter (--- ... ---)", location)
        return None
    try:
        meta = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        _logger.warning("Skill file %s invalid YAML: %s", location, e)
        return None
    if not isinstance(meta, dict):
        return None
    name = meta.get("name") or meta.get("title")
    description = meta.get("description")
    if not name or not description:
        _logger.warning("Skill file %s missing name or description", location)
        return None
    name = str(name).strip()
    description = str(description).strip() if description else ""
    if not description:
        return None
    body = parts[2].strip()
    tags = meta.get("tags")
    if isinstance(tags, list):
        tags = [str(t) for t in tags]
    elif isinstance(tags, str):
        tags = [s.strip() for s in tags.split(",") if s.strip()]
    else:
        tags = []
    examples = meta.get("examples")
    if isinstance(examples, list):
        examples = [str(ex) for ex in examples]
    elif isinstance(examples, str):
        examples = [s.strip() for s in examples.split("\n") if s.strip()]
    else:
        examples = []
    return {
        "name": name,
        "description": description,
        "location": str(location),
        "body": body,
        "tags": tags,
        "examples": examples,
    }


def _discover_skills() -> list[dict[str, Any]]:
    """Discover skills under _SKILLS_DIR: subdirs containing SKILL.md."""
    skills: list[dict[str, Any]] = []
    if not _SKILLS_DIR.is_dir():
        _logger.info("Skills directory not found: %s", _SKILLS_DIR)
        return skills
    seen: set[str] = set()
    try:
        for entry in _SKILLS_DIR.iterdir():
            if entry.name.startswith(".") or entry.name in ("node_modules", ".git"):
                continue
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                continue
            record = _parse_skill_file(skill_md)
            if not record:
                continue
            name = record["name"]
            if name in seen:
                _logger.warning("Duplicate skill name %r, skipping %s", name, skill_md)
                continue
            seen.add(name)
            skills.append(record)
    except OSError as e:
        _logger.warning("Error scanning skills dir %s: %s", _SKILLS_DIR, e)
    skills.sort(key=lambda s: s["name"])
    return skills


def _skill_registry_from_list(skill_list: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {s["name"]: s for s in skill_list}


_DISCOVERED_SKILLS = _discover_skills()
_SKILL_REGISTRY = _skill_registry_from_list(_DISCOVERED_SKILLS)

# SDK 1.0: AgentSkill uses snake_case field names
orchestrator_skills: list[AgentSkill] = [
    AgentSkill(
        id=s["name"].replace(" ", "-").lower()[:64].strip("-"),
        name=s["name"].replace("-", " ").title(),
        description=s["description"],
        tags=s.get("tags") or ["orchestrator", "demo"],
        examples=s.get("examples") or [],
        input_modes=["text/plain"],
        output_modes=["text/plain"],
    )
    for s in _DISCOVERED_SKILLS
]

if not orchestrator_skills:
    orchestrator_skills = [
        AgentSkill(
            id="query-retriever",
            name="Query Retriever",
            description=(
                "Relays prompts to the data retriever agent for MCP, data retrieval, "
                "documents, employees, enterprise data, and AuthZEN."
            ),
            tags=["retriever", "mcp", "data", "enterprise"],
            examples=[],
            input_modes=["text/plain"],
            output_modes=["text/plain"],
        ),
    ]


# ---------------------------------------------------------------------------
# activate_skill tool
# ---------------------------------------------------------------------------


class _ActivateSkillArgs(BaseModel):
    name: str


def _make_activate_skill_tool(registry: dict[str, dict[str, Any]]) -> StructuredTool | None:
    if not registry:
        return None
    valid_names = sorted(registry.keys())
    desc = (
        "Load the full instructions for a skill by name. Call this when a task matches a skill's description. "
        f"Valid skill names: {', '.join(valid_names)}."
    )

    async def _invoke(name: str) -> str:
        name = (name or "").strip()
        if name not in registry:
            return f"Unknown skill: {name!r}. Valid skills: {', '.join(valid_names)}."
        _logger.info("Activating skill: %s", name)
        skill = registry[name]
        body = skill.get("body") or ""
        location = skill.get("location", "")
        return f"<skill_content name={name!r}>\n{body}\n\nSkill directory: {Path(location).parent}\n</skill_content>"

    async def _call(**kwargs: Any) -> str:  # noqa: ANN401
        return await _invoke(kwargs["name"])

    return StructuredTool(
        name="activate_skill",
        description=desc,
        args_schema=_ActivateSkillArgs,
        coroutine=_call,
    )


def _build_skill_catalog_prompt() -> str:
    if not _DISCOVERED_SKILLS:
        return ""
    lines = [
        "The following skills provide specialized instructions for specific tasks.",
        (
            "When a task matches a skill's description, call the activate_skill tool "
            "with the skill's name to load its full instructions."
        ),
        "",
        "<available_skills>",
    ]
    for s in _DISCOVERED_SKILLS:
        lines.append(  # noqa: PERF401
            f"  <skill><name>{s['name']}</name><description>{s['description']}</description></skill>",
        )
    lines.append("</available_skills>")
    return "\n".join(lines)


# Only steer the LLM towards query_drive when the tool is actually registered
# (ANALYST_URL set) - otherwise it would call a non-existent tool.
_DRIVE_PROMPT = (
    "For Google Drive - searching Drive, Drive files or folders, reading a document stored in Google "
    "Drive - use query_drive. Do not use query_retriever for Google Drive. "
    if ANALYST_URL
    else ""
)
# Same gating for the CRM tool: only steer towards query_crm when registered.
_CRM_PROMPT = (
    "For opening or filing a support case or ticket in Salesforce/the CRM, use query_crm and compose "
    "its argument as 'subject: <title>' on the first line then the description, naming who the case "
    "is for. Do not use query_retriever for Salesforce cases. "
    if CRM_URL
    else ""
)
_BASE_SYSTEM_PROMPT = (
    "You are an orchestrator. Your primary job is to relay prompts to downstream agents. "
    "Look at your available skills and choose the right one for each request. "
    "For weather/forecast/current conditions, use query_weather. "
    "Do not use query_retriever for weather. "
    + _DRIVE_PROMPT
    + _CRM_PROMPT
    + "ALWAYS use the query_retriever tool for: MCP, MCP tools or resources, data retrieval, "
    "internal documents, real-time stock prices, "
    "questions about employees, enterprise data, authorization (AuthZEN), knowledge queries, or any "
    "internal/knowledge-graph data. Forward the user's question to the retriever and return its response. "
    "Use your other skills from the skill catalog for other requests. "
    "If the user's request is not clear, ask for clarification. "
    "If the user's request is not possible, say 'I'm sorry, I can't help with that.'"
)
_SKILL_CATALOG_APPENDIX = _build_skill_catalog_prompt()
_SYSTEM_PROMPT = _BASE_SYSTEM_PROMPT + ("\n\n" + _SKILL_CATALOG_APPENDIX if _SKILL_CATALOG_APPENDIX else "")

_activate_skill_tool = _make_activate_skill_tool(_SKILL_REGISTRY)
_orchestrator_tools = ([_activate_skill_tool] if _activate_skill_tool is not None else []) + [
    query_retriever,
    query_weather,
    _search_tool,  # duckduckgo_search - the tool the web-search skill advertises
]
if ANALYST_URL:
    _orchestrator_tools.append(query_drive)
if CRM_URL:
    _orchestrator_tools.append(query_crm)
_llm_with_tools = _llm.bind_tools(_orchestrator_tools)

orchestrator_card = AgentCard(
    name=ORCHESTRATOR_AGENT_NAME,
    description=(
        "Orchestrator agent that relays MCP, data retrieval, document, employee, and enterprise queries "
        "to the data retriever. Uses web search only when absolutely necessary."
    ),
    version="1.0.0",
    provider={
        "organization": "Indykite",
        "url": "https://www.indykite.com",
    },
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
            url=f"http://{ADVERTISED_HOST}:{ORCHESTRATOR_PORT}",
            protocol_version="1.0",
        ),
    ],
    skills=orchestrator_skills,
)

# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

_TOOL_CALL_MAX_ITERATIONS = 5


class OrchestratorExecutor(AgentExecutor):
    """AgentExecutor that routes incoming messages through an LLM + tool loop."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:  # noqa: C901,D102,PLR0912
        access_token = _get_access_token_from_context(context)
        if not access_token:
            raise HTTPException(status_code=401, detail="Authorization required")
        # Demo feature: the short-lived delegation token is intentionally
        # surfaced in the console audit terminal to show the exchange chain;
        # logs carry only a redacted fingerprint to avoid credential leaks.
        _logger.info("Exchanged bearer token (redacted): %s...%s", access_token[:6], access_token[-6:])
        _report_exchanged_token(access_token)
        _current_access_token.set(access_token)

        # SDK 1.0: context.message.parts is list[Part]; Part.text is the text field directly.
        raw_text = ""
        if context.message:
            for part in context.message.parts or []:
                t = getattr(part, "text", None)
                if t:
                    raw_text += t
        _logger.info("Received message for %s: %s", ORCHESTRATOR_AGENT_NAME, raw_text)

        prompt = raw_text or "(empty)"

        # Establish task lifecycle via direct event enqueuing (SDK 1.0 pattern)
        task = context.current_task or new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(
                    state=TaskState.TASK_STATE_WORKING,
                    message=new_text_message("Processing request..."),
                ),
            ),
        )

        # LLM + tool loop
        messages = [
            HumanMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]
        final_text = ""
        for _ in range(_TOOL_CALL_MAX_ITERATIONS):
            time_start = time.time()
            response = await _llm_with_tools.ainvoke(messages)
            _logger.info("LLM invocation time: %.2fs", time.time() - time_start)

            tool_calls = getattr(response, "tool_calls", None) or []
            if not tool_calls:
                final_text = str(getattr(response, "text", "") or getattr(response, "content", "") or "")
                break

            messages.append(response)
            for tc in tool_calls:
                name = tc.get("name", "")
                args = tc.get("args", {}) or {}
                tid = tc.get("id", "")
                tool_obj = next((t for t in _orchestrator_tools if t.name == name), None)
                if name == "activate_skill":
                    _logger.info(
                        "Using skill: activate_skill (loading instructions for: %s)",
                        args.get("name", "?"),
                    )
                else:
                    _logger.info("Using skill/tool: %s", name)
                result = "Tool not found"
                if tool_obj:
                    try:
                        if hasattr(tool_obj, "ainvoke"):
                            result = await tool_obj.ainvoke(args)
                        else:
                            result = tool_obj.invoke(args)
                    except Exception as e:
                        result = f"Error: {e}"
                messages.append(ToolMessage(content=str(result), tool_call_id=tid))

        if not final_text:
            final_text = "(No response generated)"

        # SDK 1.0: enqueue artifact update then complete status
        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                artifact=new_text_artifact(name="result", text=final_text),
            ),
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            ),
        )
        _logger.info("LLM response for %s: %s...", ORCHESTRATOR_AGENT_NAME, final_text[:200])

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:  # noqa: D102
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id or str(uuid.uuid4()),
                status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
            ),
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    handler = DefaultRequestHandler(
        agent_executor=OrchestratorExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=orchestrator_card,
    )
    app = Starlette(
        routes=[
            *create_agent_card_routes(agent_card=orchestrator_card),
            *create_jsonrpc_routes(request_handler=handler, rpc_url=DEFAULT_RPC_URL),
        ],
    )

    llm_info = f"Gemini {GEMINI_MODEL}" if (GEMINI_ENABLED and GEMINI_API_KEY) else LLM_MODEL
    _logger.info(
        "Starting %s on port %d (LLM: %s)",
        ORCHESTRATOR_AGENT_NAME,
        ORCHESTRATOR_PORT,
        llm_info,
    )
    _logger.info("Retriever URL: %s", RETRIEVER_URL)
    _logger.info("Weather URL: %s", WEATHER_URL)
    if ANALYST_URL:
        _logger.info("Analyst URL (query_drive): %s", ANALYST_URL)
    else:
        _logger.info("ANALYST_HOST not set - query_drive tool disabled")
    if CRM_URL:
        _logger.info("CRM URL (query_crm): %s", CRM_URL)
    else:
        _logger.info("CRM_HOST not set - query_crm tool disabled")
    # uvicorn must bind to 0.0.0.0 inside Docker; safe because the container
    # network exposes only the intended port via compose.
    uvicorn.run(
        app,
        host="0.0.0.0",  # nosec B104  # noqa: S104
        port=ORCHESTRATOR_PORT,
        log_level="info",
    )
