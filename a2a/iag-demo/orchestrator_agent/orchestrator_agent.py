"""Orchestrator agent - A2A-compliant agent (a2a-sdk>=1.0.0a0) that receives and relays messages to the retriever."""

import asyncio
import logging
import os
import re
import time
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import httpx
import uvicorn
import yaml
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
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
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils import new_agent_text_message, new_task, new_text_artifact
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
ORCHESTRATOR_PORT = int(os.getenv("ORCHESTRATOR_PORT", 6001))  # noqa: PLW1508
ADVERTISED_HOST = os.getenv("ADVERTISED_HOST", "orchestrator")
ORCHESTRATOR_AGENT_NAME = os.getenv("ORCHESTRATOR_AGENT_NAME", "orchestrator_agent")
LLM_MODEL = os.getenv("LLM_MODEL", "mistral-nemo:latest")
GEMINI_ENABLED = os.getenv("GEMINI_ENABLED", os.getenv("GEMENI_ENABLED", "")).lower() in ("true", "1", "yes")
GEMINI_DISABLED = os.getenv("GEMINI_ENABLED", os.getenv("GEMENI_ENABLED", "")).lower() in ("false", "0", "no")
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY", "") or "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
RETRIEVER_HOST = os.getenv("RETRIEVER_HOST", "retriever")
RETRIEVER_PORT = int(os.getenv("RETRIEVER_PORT", 6002))  # noqa: PLW1508
RETRIEVER_URL = (os.getenv("RETRIEVER_URL", "").strip() or f"http://{RETRIEVER_HOST}:{RETRIEVER_PORT}").rstrip("/")
WEATHER_HOST = os.getenv("WEATHER_HOST", "weather")
WEATHER_PORT = int(os.getenv("WEATHER_PORT", 6004))  # noqa: PLW1508
WEATHER_URL = (os.getenv("WEATHER_URL", "").strip() or f"http://{WEATHER_HOST}:{WEATHER_PORT}").rstrip("/")
ORCHESTRATOR_TIMEOUT = float(os.getenv("ORCHESTRATOR_TIMEOUT", "300"))
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("ddgs").setLevel(logging.WARNING)

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
        max_polls = int(ORCHESTRATOR_TIMEOUT / 2)

        for _ in range(max_polls):
            await asyncio.sleep(2)
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
                await asyncio.sleep(2)
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


async def _call_agent_a2a(base_url: str, text: str, token: str) -> str:  # noqa: C901,PLR0912,PLR0915
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
        event_count = 0

        async for event in client.send_message(request):
            _logger.debug("send_message event type: %s", type(event).__name__)

            # SDK yields (Task, Task) tuples
            if isinstance(event, tuple):
                # event[0] is a wrapper with a .task field; event[1] is the Task directly
                task_obj = getattr(event[0], "task", None) or event[1]
                if task_id is None:
                    task_id = task_obj.id
                    _logger.info("A2A agent task submitted: %s", task_id)
                state = task_obj.status.state if task_obj.status else None
                if state in (
                    TaskState.TASK_STATE_COMPLETED,
                    TaskState.TASK_STATE_FAILED,
                    TaskState.TASK_STATE_REJECTED,
                    TaskState.TASK_STATE_CANCELED,
                ):
                    extracted = _extract_text_from_task(task_obj)
                    if extracted:
                        return extracted
                    break

            elif hasattr(event, "result"):
                inner = event.result
                if isinstance(inner, Message):
                    return _extract_text_from_task(inner)
                task_obj = inner if isinstance(inner, Task) else getattr(inner, "task", None)
                if task_obj is None:
                    continue
                if task_id is None:
                    task_id = task_obj.id
                state = task_obj.status.state if task_obj.status else None
                if state in (
                    TaskState.TASK_STATE_COMPLETED,
                    TaskState.TASK_STATE_FAILED,
                    TaskState.TASK_STATE_REJECTED,
                    TaskState.TASK_STATE_CANCELED,
                ):
                    break

            elif isinstance(event, Task):
                if task_id is None:
                    task_id = event.id
                state = event.status.state if event.status else None
                if state in (
                    TaskState.TASK_STATE_COMPLETED,
                    TaskState.TASK_STATE_FAILED,
                    TaskState.TASK_STATE_REJECTED,
                    TaskState.TASK_STATE_CANCELED,
                ):
                    break

        _logger.info("Event loop done. event_count=%d task_id=%s", event_count, task_id)

        if task_id:
            try:
                task = await client.get_task(task_id)
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
        "When a task matches a skill's description, call the activate_skill tool with the skill's name to load its full instructions.",  # noqa: E501
        "",
        "<available_skills>",
    ]
    for s in _DISCOVERED_SKILLS:
        lines.append(  # noqa: PERF401
            f"  <skill><name>{s['name']}</name><description>{s['description']}</description></skill>",
        )
    lines.append("</available_skills>")
    return "\n".join(lines)


_BASE_SYSTEM_PROMPT = (
    "You are an orchestrator. Your primary job is to relay prompts to downstream agents. "
    "Look at your available skills and choose the right one for each request. "
    "For weather/forecast/current conditions, use query_weather. "
    "Do not use query_retriever for weather. "
    "ALWAYS use the query_retriever tool for: MCP, MCP tools or resources, data retrieval, documents, real-time stock prices, "  # noqa: E501
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
]
_llm_with_tools = _llm.bind_tools(_orchestrator_tools)

# ---------------------------------------------------------------------------
# Agent Card - SDK 1.0 shape
# ---------------------------------------------------------------------------
# SDK 1.0 key changes vs 0.3:
#   • snake_case field names (default_input_modes, default_output_modes)
#   • supported_interfaces replaces url + preferredTransport + protocolVersion
#   • No more kind discriminator field
#   • AgentInterface(protocol_binding=..., url=...)

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
        task = context.current_task or new_task(context.message)
        await event_queue.enqueue_event(task)

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(
                    state=TaskState.TASK_STATE_WORKING,
                    message=new_agent_text_message("Processing request..."),
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
    # uvicorn must bind to 0.0.0.0 inside Docker; safe because the container
    # network exposes only the intended port via compose.
    uvicorn.run(
        app,
        host="0.0.0.0",  # nosec B104  # noqa: S104
        port=ORCHESTRATOR_PORT,
        log_level="info",
    )
