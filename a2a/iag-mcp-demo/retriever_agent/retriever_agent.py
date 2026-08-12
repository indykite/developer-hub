"""Retriever agent - A2A-compliant agent (a2a-sdk>=1.1.0) that uses a remote MCP server via the official MCP SDK."""
# Reason: warnings.filterwarnings() must run before LangChain imports to suppress
# the Pydantic V1 deprecation warning on Python 3.14+, so all other imports
# necessarily follow a small block of setup code - each carries a per-line
# E402 suppression for ruff and flake8.

# Suppress LangChain Pydantic V1 warning on Python 3.14+ (LangChain imports pydantic.v1 for checks)
import warnings

warnings.filterwarnings(
    "ignore",
    message=".*Pydantic V1.*isn't compatible with Python 3.14.*",
    category=UserWarning,
)

import asyncio  # noqa: E402
import base64  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from contextlib import AsyncExitStack, asynccontextmanager, suppress  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import httpx  # noqa: E402
import uvicorn  # noqa: E402
import yaml  # noqa: E402
from a2a.helpers.proto_helpers import (  # noqa: E402
    new_task_from_user_message,
    new_text_artifact,
    new_text_message,
)

# SDK 1.0 imports
from a2a.server.agent_execution import (  # noqa: E402
    AgentExecutor,
    RequestContext,
)
from a2a.server.events import EventQueue  # noqa: E402
from a2a.server.request_handlers import (  # noqa: E402
    DefaultRequestHandler,
)
from a2a.server.routes import (  # noqa: E402
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore  # noqa: E402
from a2a.types import (  # noqa: E402
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.constants import DEFAULT_RPC_URL  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from langchain_core.messages import (  # noqa: E402
    HumanMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool  # noqa: E402
from langchain_google_genai import (  # noqa: E402
    ChatGoogleGenerativeAI,
)
from langchain_ollama.chat_models import ChatOllama  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import (  # noqa: E402
    StreamableHTTPTransport,
    streamable_http_client,
)
from mcp.shared._httpx_utils import (  # noqa: E402
    create_mcp_http_client,
)
from mcp.types import (  # noqa: E402
    CallToolResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)
from pydantic import (  # noqa: E402
    BaseModel,
    ConfigDict,
    Field,
    create_model,
)
from starlette.applications import Starlette  # noqa: E402
from starlette.exceptions import HTTPException  # noqa: E402

load_dotenv()

# ---------------------------------------------------------------------------
# MCP transport workaround for Indykite
# ---------------------------------------------------------------------------
# Indykite MCP returns 202 Accepted with Mcp-Session-Id in headers. The Python MCP SDK
# returns early on 202 without extracting the session ID, causing the subsequent GET
# to fail with 404. Patch to extract session ID from 202 responses.

import mcp.client.streamable_http as _mcp_streamable_http  # noqa: E402


async def _patched_handle_post_request(self, ctx):
    """Patched _handle_post_request that extracts session ID from 202 responses."""
    headers = self._prepare_headers()  # skipcq: PYL-W0212
    message = ctx.session_message.message
    is_initialization = self._is_initialization_request(message)  # skipcq: PYL-W0212

    async with ctx.client.stream(
        "POST",
        self.url,
        json=message.model_dump(by_alias=True, mode="json", exclude_none=True),
        headers=headers,
    ) as response:
        if response.status_code == 202:  # noqa: PLR2004
            _mcp_streamable_http.logger.debug("Received 202 Accepted")
            if is_initialization:
                self._maybe_extract_session_id_from_response(response)  # skipcq: PYL-W0212
            return

        if response.status_code == 404:  # noqa: PLR2004
            if isinstance(message.root, _mcp_streamable_http.JSONRPCRequest):
                await self._send_session_terminated_error(  # skipcq: PYL-W0212
                    ctx.read_stream_writer,
                    message.root.id,
                )
            return

        if response.status_code >= 400:  # noqa: PLR2004
            # best-effort: buffer error body so it survives stream close
            with suppress(Exception):
                await response.aread()
        response.raise_for_status()
        if is_initialization:
            self._maybe_extract_session_id_from_response(response)  # skipcq: PYL-W0212

        if isinstance(message.root, _mcp_streamable_http.JSONRPCRequest):
            content_type = response.headers.get(_mcp_streamable_http.CONTENT_TYPE, "").lower()
            if content_type.startswith(_mcp_streamable_http.JSON):
                await self._handle_json_response(  # skipcq: PYL-W0212
                    response,
                    ctx.read_stream_writer,
                    is_initialization,
                )
            elif content_type.startswith(_mcp_streamable_http.SSE):
                await self._handle_sse_response(response, ctx, is_initialization)  # skipcq: PYL-W0212
            else:
                await self._handle_unexpected_content_type(content_type, ctx.read_stream_writer)  # skipcq: PYL-W0212


StreamableHTTPTransport._handle_post_request = _patched_handle_post_request  # noqa: SLF001  # skipcq: PYL-W0212

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
RETRIEVER_PORT = int(os.getenv("RETRIEVER_PORT", "6002"))
ADVERTISED_HOST = os.getenv("ADVERTISED_HOST", "retriever")
RETRIEVER_AGENT_NAME = os.getenv("RETRIEVER_AGENT_NAME", "retriever_agent")
LLM_MODEL = os.getenv("LLM_MODEL", "mistral-nemo:latest")
GEMINI_ENABLED = os.getenv("GEMINI_ENABLED", os.getenv("GEMENI_ENABLED", "")).lower() in ("true", "1", "yes")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "").strip()
INDYKITE_BASE_URL = os.getenv("INDYKITE_BASE_URL", "").strip()
# Session-setup deadline (initialize + tools/list + resources/list); a hung
# downstream would otherwise leave the owner task waiting forever.
MCP_SETUP_TIMEOUT = float(os.getenv("MCP_SETUP_TIMEOUT", "20"))
# How long (seconds) the per-user MCP session is reused across requests
# before being rebuilt. Keep it below the access-token lifetime; 0 restores
# the old behavior of a fresh session per request.
MCP_SESSION_TTL = float(os.getenv("MCP_SESSION_TTL", "300"))
CIQ_QUERY_STOCK_PRICE = os.getenv("CIQ_QUERY_STOCK_PRICE", "").strip() or "get-stock-quote"
CIQ_QUERY_PURCHASE_LIMIT = os.getenv("CIQ_QUERY_PURCHASE_LIMIT", "").strip() or "get-stock-trade-threshold"
CIQ_QUERY_STORE_DECISION = os.getenv("CIQ_QUERY_STORE_DECISION", "").strip() or "store-decision"
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
_LIB_LOG_LEVELNO = getattr(logging, _LIB_LOG_LEVEL, logging.INFO)


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
_t0 = time.time()
if GEMINI_ENABLED and GEMINI_API_KEY:
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
_logger.info("LLM initialization time: %.2fs", time.time() - _t0)

_TOOL_CALL_MAX_ITERATIONS = 8

# ---------------------------------------------------------------------------
# Agent Skills (agentskills.io) - discovery
# ---------------------------------------------------------------------------
_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


# A frontmatter split yields at least: leading text, YAML block, body.
_FRONTMATTER_PARTS = 3


def _str_list(value: Any, sep: str) -> list[str]:  # noqa: ANN401
    """Coerce a frontmatter list-or-string value into a list of strings."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [s.strip() for s in value.split(sep) if s.strip()]
    return []


def _load_frontmatter(location: Path) -> tuple[dict, str] | None:
    """Read a SKILL.md file and split it into (frontmatter dict, body)."""
    try:
        raw = location.read_text(encoding="utf-8")
    except OSError as e:
        _logger.warning("Could not read skill file %s: %s", location, e)
        return None
    if not raw.strip():
        return None
    parts = re.split(r"^---\s*$", raw.strip(), maxsplit=2, flags=re.MULTILINE)
    if len(parts) < _FRONTMATTER_PARTS:
        _logger.warning("Skill file %s has no valid frontmatter (--- ... ---)", location)
        return None
    try:
        meta = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        _logger.warning("Skill file %s invalid YAML: %s", location, e)
        return None
    if not isinstance(meta, dict):
        return None
    return meta, parts[2].strip()


def _parse_skill_file(location: Path) -> dict[str, Any] | None:
    """Parse a SKILL.md file: extract YAML frontmatter and body. Returns skill record or None."""
    loaded = _load_frontmatter(location)
    if loaded is None:
        return None
    meta, body = loaded
    name = meta.get("name") or meta.get("title")
    description = str(meta.get("description") or "").strip()
    if not name or not description:
        _logger.warning("Skill file %s missing name or description", location)
        return None
    return {
        "name": str(name).strip(),
        "description": description,
        "location": str(location),
        "body": body,
        "tags": _str_list(meta.get("tags"), ","),
        "examples": _str_list(meta.get("examples"), "\n"),
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
retriever_skills: list[AgentSkill] = [
    AgentSkill(
        id=s["name"].replace(" ", "-").lower()[:64].strip("-"),
        name=s["name"].replace("-", " ").title(),
        description=s["description"],
        tags=s.get("tags") or ["retriever", "demo"],
        examples=s.get("examples") or [],
        input_modes=["text/plain"],
        output_modes=["text/plain"],
    )
    for s in _DISCOVERED_SKILLS
]

if not retriever_skills:
    retriever_skills = [
        AgentSkill(
            id="retriever",
            name="Retriever",
            description="Data retriever using MCP server tools and resources.",
            tags=["retriever", "demo"],
            examples=[],
            input_modes=["text/plain"],
            output_modes=["text/plain"],
        ),
    ]


def _build_skill_catalog_prompt() -> str:
    """Build tier-1 skill catalog for the system prompt (progressive disclosure)."""
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
            f"  <skill><n>{s['name']}</n><description>{s['description']}</description></skill>",
        )
    lines.append("</available_skills>")
    return "\n".join(lines)


_BASE_SYSTEM_PROMPT = (
    "You are a helpful data retriever assistant that uses the Indykite MCP server to retrieve data. "
    "The backend exposes both MCP Resources and MCP Tools; both should be listed and considered at runtime.\n"
    "Upon receiving a request, you must:\n"
    "1. Consider the combination of (a) your skills (authzen, ciq-execute, max-purchase-amount, "
    "retriever) and (b) available MCP resources and MCP tools. Use list_resources to discover MCP "
    "resources (URIs, names, descriptions). Your bound tools already include the MCP server's tools "
    "(e.g. list_resources, read_resource, ciq_execute, plus any AuthZEN or other tools from the server).\n"
    "2. Select from this combined list which to run: either call an MCP tool (list_resources, "
    "read_resource, ciq_execute, or an AuthZEN tool), or use max_purchase_amount, or activate a "
    "skill (activate_skill) and then run the matching tool, or reply without a tool (retriever).\n"
    "3. Run the selected tool or skill. Skills authzen and ciq-execute match MCP resources or tools "
    "exposed by the server—use list_resources and the tool list to see what is available and choose "
    "the right resource or tool for the request.\n"
    'Use ciq_execute with a concrete {"id": "<query-id>"} and its "input_params" — '
    "NEVER call ciq_execute with empty arguments. Known query ids (call with these exact shapes):\n"
    '  {"id": "get-stock-quote", "input_params": {"ticker": "NVDA"}}\n'
    '  {"id": "get-stock-trade-threshold", "input_params": {"customer_external_id": "rebecca"}}\n'
    '  {"id": "get-self", "input_params": {}}\n'
    '  {"id": "get-internal-documents", "input_params": {"taxonomy_external_id": "policy"}}\n'
    '  {"id": "get-decisions", "input_params": {"document_external_id": "refund_policy"}}\n'
    '  {"id": "get-customer-facing-documents", "input_params": {}}\n'
    '  {"id": "get-regulatory-agreements", "input_params": {}}\n'
    "Substitute the real ticker / customer / document / taxonomy from the user question; "
    "if unsure which id fits, call list_resources first.\n"
    "AuthZEN (evaluation, evaluations, resource_search, subject_search, action_search): example "
    '{"subject":{"type":"user","id":"alice"},"action":{"name":"view"},"resource":{"type":"record","id":"109"}}; '
    'response {"decision":true} or {"decision":false}. Return data only when evaluation is '
    "successful; otherwise say 'Authorization evaluation failed.'\n"
    "If no tool or resource is found, say 'No tool or resource found'. If no data is found, say 'No data found'."
)
_SKILL_CATALOG_APPENDIX = _build_skill_catalog_prompt()
_SYSTEM_PROMPT = _BASE_SYSTEM_PROMPT + ("\n\n" + _SKILL_CATALOG_APPENDIX if _SKILL_CATALOG_APPENDIX else "")

# ---------------------------------------------------------------------------
# Agent Card - SDK 1.0 shape
# ---------------------------------------------------------------------------
retriever_card = AgentCard(
    name=RETRIEVER_AGENT_NAME,
    description=(
        "Retriever agent that uses a remote MCP server as its main capability. "
        "Connects to the server at MCP_SERVER_URL and exposes its tools for data retrieval."
    ),
    version="1.0.0",
    capabilities=AgentCapabilities(
        streaming=False,
        push_notifications=False,
        extended_agent_card=False,
    ),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    supported_interfaces=[
        AgentInterface(
            protocol_binding="JSONRPC",
            url=f"http://{ADVERTISED_HOST}:{RETRIEVER_PORT}",
            protocol_version="1.0",
        ),
    ],
    skills=retriever_skills,
)


# ---------------------------------------------------------------------------
# MCP helpers
# ---------------------------------------------------------------------------


def _format_call_tool_result(result: CallToolResult) -> str:
    """Convert MCP CallToolResult to a string for ToolMessage content."""
    parts: list[str] = []
    for block in result.content:
        if isinstance(block, TextContent):
            parts.append(block.text)  # noqa: PERF401
    text = "\n".join(parts) if parts else ""
    if result.structuredContent:
        if text:
            text += "\n\n"
        text += json.dumps(result.structuredContent, indent=2)
    if result.isError and not text:
        text = "Tool error (no details returned)"
    return text or "(empty)"


def _extract_httpx_error(exc: BaseException) -> tuple[Any, Any]:
    """Dig through exception(s) to find an httpx HTTPStatusError. Returns (response, request)."""
    seen: set[int] = set()
    to_check = [exc]
    while to_check:
        e = to_check.pop()
        if id(e) in seen:
            continue
        seen.add(id(e))
        if isinstance(e, httpx.HTTPStatusError):
            return (getattr(e, "response", None), getattr(e, "request", None))
        if isinstance(e, ExceptionGroup):
            to_check.extend(e.exceptions)
        if hasattr(e, "__cause__") and e.__cause__:
            to_check.append(e.__cause__)
        if hasattr(e, "__context__") and e.__context__:
            to_check.append(e.__context__)
    return (None, None)


def _headers_str(headers: Any, *, redact_authorization: bool = False) -> str:  # noqa: ANN401
    """Join headers into "k: v; ..." form, redacting Authorization when asked. Never raises."""
    try:
        return "; ".join(
            f"{k}: {'<redacted>' if redact_authorization and k.lower() == 'authorization' else v}"
            for k, v in headers.items()
        )
    except Exception:
        return ""


def _format_mcp_error(exc: BaseException) -> str:
    """Extract request/response and headers from any MCP/HTTP exception. No stack trace."""
    try:
        parts: list[str] = ["MCP error."]
        detail = str(exc).strip()
        if detail:
            parts.append(f"Detail: {detail[:500]}")
        response, request = _extract_httpx_error(exc)
        req_hdr = _headers_str(getattr(request, "headers", None) or {}, redact_authorization=True)
        if req_hdr:
            parts.append(f"Request headers: {req_hdr}")
        if response:
            if hasattr(response, "status_code"):
                parts.append(f"Status: {response.status_code}")
            resp_hdr = _headers_str(getattr(response, "headers", None) or {})
            if resp_hdr:
                parts.append(f"Response headers: {resp_hdr}")
            try:
                body = getattr(response, "text", "")
            except Exception:  # nosec B110 - streaming body not read; status+headers still useful
                body = ""
            if body:
                parts.append(f"Response: {body[:500]}")
        return " ".join(parts)
    except Exception:
        return f"MCP error: {exc!s}"


async def _list_all_mcp_tools(session: ClientSession) -> list[Tool]:
    """List all MCP tools with pagination support."""
    all_tools: list[Tool] = []
    cursor: str | None = None
    while True:
        params = PaginatedRequestParams(cursor=cursor) if cursor else None
        rpc_params = params.model_dump() if params is not None else {}
        _logger.info(
            "MCP RPC request: %s",
            json.dumps({"jsonrpc": "2.0", "method": "tools/list", "params": rpc_params}, default=str),
        )
        result = await session.list_tools(params=params)
        all_tools.extend(result.tools)
        if not result.nextCursor:
            break
        cursor = result.nextCursor
    return all_tools


async def _list_all_mcp_resources(session: ClientSession) -> list[Any]:
    """List all MCP resources with pagination support (resources/list)."""
    all_resources: list[Any] = []
    cursor: str | None = None
    while True:
        params = PaginatedRequestParams(cursor=cursor) if cursor else None
        _logger.info(
            "MCP RPC request: %s",
            json.dumps(
                {"jsonrpc": "2.0", "method": "resources/list", "params": params.model_dump() if params else {}},
                default=str,
            ),
        )
        result = await session.list_resources(params=params)
        all_resources.extend(result.resources)
        if not result.nextCursor:
            break
        cursor = result.nextCursor
    return all_resources


def _format_read_resource_result(result: Any) -> str:  # noqa: ANN401
    """Convert MCP ReadResourceResult to a string."""
    contents = getattr(result, "contents", None) or getattr(result, "content", None) or []
    parts: list[str] = []
    for block in contents:
        if isinstance(block, TextContent):
            parts.append(block.text)
        elif hasattr(block, "text"):
            parts.append(getattr(block, "text", ""))
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts) if parts else "(empty)"


_JSON_TO_PY_TYPE: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _field_definition(name: str, prop: dict, required: set[str]) -> tuple[Any, Any]:
    """Build one (type, Field) pair for create_model from a JSON-schema property."""
    prop_type = prop.get("type")
    # JSON-schema nullable fields use a list, e.g. ["null", "integer"] — pick the
    # concrete type. A bare list is unhashable and would break the dict lookup.
    if isinstance(prop_type, list):
        prop_type = next((t for t in prop_type if t != "null"), None)
    py_type = _JSON_TO_PY_TYPE.get(prop_type, Any)
    if prop_type == "array":
        # Gemini 3.x rejects array declarations without an items type; make the
        # element type explicit instead of relying on the SDK to fill it in.
        item_type = (prop.get("items") or {}).get("type")
        if isinstance(item_type, list):
            item_type = next((t for t in item_type if t != "null"), None)
        py_type = list[_JSON_TO_PY_TYPE.get(item_type, dict)]  # type: ignore[misc]
    desc = prop.get("description") or ""
    if name in required:
        return (py_type, Field(description=desc) if desc else ...)
    opt_type = (py_type | None) if py_type is not Any else Any
    return (opt_type, Field(default=None, description=desc) if desc else None)


def _json_schema_to_args_model(tool_name: str, input_schema: dict) -> type[BaseModel]:
    """Build a Pydantic model from MCP inputSchema so the LLM gets explicit parameter names."""
    properties = input_schema.get("properties") or {}
    required = set(input_schema.get("required") or [])
    if not properties:
        return create_model(f"{tool_name}_args", __config__=ConfigDict(extra="allow"))

    fields: dict[str, Any] = {
        name: _field_definition(name, prop, required) for name, prop in properties.items() if isinstance(prop, dict)
    }
    return create_model(
        f"{tool_name}_args",
        __config__=ConfigDict(extra="allow"),
        **fields,
    )


class _MCPToolArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class _ReadResourceArgs(BaseModel):
    uri: str


class _ActivateSkillArgs(BaseModel):
    name: str


class _EmptyArgs(BaseModel):
    pass


class _MaxPurchaseAmountArgs(BaseModel):
    user_id: str
    ticker: str


def _num_from_value(v: object) -> float | None:
    """Coerce an int/float/numeric-string to float, else None."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _num_from_nodes(nodes: dict) -> float | None:
    """Pick a numeric value out of a CIQ nodes dict (quote price or tier threshold)."""
    quote_price = nodes.get("quote.property.price")
    if isinstance(quote_price, dict):
        n = _num_from_value(quote_price.get("Price"))
        if n is not None:
            return n
    return _num_from_value(nodes.get("tier.property.threshold_amount"))


def _num_from_data_list(data_list: list) -> float | None:
    """Pick a numeric value from a CIQ data[] list entry."""
    if not data_list:
        return None
    first = data_list[0]
    if isinstance(first, dict):
        nodes = first.get("nodes")
        if isinstance(nodes, dict):
            return _num_from_nodes(nodes)
    if isinstance(first, (int, float)):
        return float(first)
    return None


def _num_from_dict(data: dict) -> float | None:
    """Pick a numeric value from a CIQ result dict by scanning known shapes."""
    inner = data.get("data")
    if isinstance(inner, list):
        n = _num_from_data_list(inner)
        if n is not None:
            return n
    for key in ("value", "price", "limit", "amount", "result", "data"):
        val = data.get(key)
        if val is not None:
            n = _num_from_value(val)
            if n is not None:
                return n
    for v in data.values():
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _parse_number_from_ciq_result(result_str: str) -> float | None:
    """Extract a single numeric value from a CIQ result string (JSON or plain)."""
    if not result_str or not result_str.strip():
        return None
    s = result_str.strip()
    try:
        data = json.loads(s)
    except (TypeError, ValueError):
        data = None

    if isinstance(data, (int, float)):
        return float(data)
    if isinstance(data, dict):
        n = _num_from_dict(data)
        if n is not None:
            return n
    if isinstance(data, list) and data and isinstance(data[0], (int, float)):
        return float(data[0])

    try:
        return float(s)
    except ValueError:
        pass
    m = re.search(r"-?\d+\.?\d*", s)
    return float(m.group()) if m else None


# ---------------------------------------------------------------------------
# LangChain tool factories
# ---------------------------------------------------------------------------


def _make_max_purchase_amount_tool(session: ClientSession) -> StructuredTool:
    """Create a LangChain tool: max purchase amount = floor(purchase_limit / stock_price)."""

    async def _invoke(user_id: str, ticker: str) -> str:
        try:
            _logger.info("Calling CIQ_QUERY_STOCK_PRICE: %s", CIQ_QUERY_STOCK_PRICE)
            price_result = await session.call_tool(
                "ciq_execute",
                {"id": CIQ_QUERY_STOCK_PRICE, "input_params": {"ticker": ticker}},
            )
            _logger.info("Stock price result: %s", price_result)
            price_str = _format_call_tool_result(price_result)
            ticker_price = _parse_number_from_ciq_result(price_str)
            if ticker_price is None or ticker_price <= 0:
                return f"Could not get stock price for {ticker!r} or invalid value: {price_str[:200]}"

            _logger.info("Calling CIQ_QUERY_PURCHASE_LIMIT: %s", CIQ_QUERY_PURCHASE_LIMIT)
            limit_result = await session.call_tool(
                "ciq_execute",
                {"id": CIQ_QUERY_PURCHASE_LIMIT, "input_params": {"customer_external_id": user_id}},
            )
            _logger.info("Purchase limit result: %s", limit_result)
            limit_str = _format_call_tool_result(limit_result)
            purchase_limit = _parse_number_from_ciq_result(limit_str)
            if purchase_limit is None or purchase_limit < 0:
                return f"Could not get purchase limit for user {user_id!r} or invalid value: {limit_str[:200]}"

            max_shares = int(purchase_limit // ticker_price)
            _logger.info("Max shares: %d", max_shares)
            return str(max_shares)
        except BaseException as e:
            return _format_mcp_error(e)

    async def _call(**kwargs: Any) -> str:  # noqa: ANN401
        return await _invoke(kwargs["user_id"], kwargs["ticker"])

    return StructuredTool(
        name="max_purchase_amount",
        description=(
            "Maximum number of shares the user can buy: given a user id and a stock ticker symbol, "
            "runs the stock price CIQ and the purchase limit CIQ, then returns floor(purchase_limit / ticker_price)."
        ),
        args_schema=_MaxPurchaseAmountArgs,
        coroutine=_call,
    )


def _make_activate_skill_tool(registry: dict[str, dict[str, Any]]) -> StructuredTool | None:
    """Create activate_skill tool that returns full SKILL.md body for a given skill name (tier 2)."""
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


def _make_list_resources_tool(session: ClientSession) -> StructuredTool:
    """Create a LangChain tool that lists MCP resources (resources/list)."""

    async def _invoke(**_kwargs: Any) -> str:  # noqa: ANN401
        try:
            resources = await _list_all_mcp_resources(session)
            lines = ["Available MCP resources:"]
            for r in resources:
                uri = getattr(r, "uri", "") or (r.get("uri") if isinstance(r, dict) else "?")
                name = getattr(r, "name", "") or (r.get("name") if isinstance(r, dict) else "")
                desc = getattr(r, "description", "") or (r.get("description") if isinstance(r, dict) else "")
                mime = getattr(r, "mimeType", "") or (r.get("mimeType") if isinstance(r, dict) else "")
                row = f"- {uri}"
                if name:
                    row += f"  name: {name}"
                if desc:
                    row += f"  description: {desc}"
                if mime:
                    row += f"  mimeType: {mime}"
                lines.append(row)
            return "\n".join(lines) if len(lines) > 1 else "No resources available."
        except BaseException as e:
            return _format_mcp_error(e)

    return StructuredTool(
        name="list_resources",
        description=(
            "List all available resources from the MCP server (resources/list). "
            "Use this to discover resources (URIs, names, descriptions) before reading them with read_resource."
        ),
        args_schema=_EmptyArgs,
        coroutine=_invoke,
    )


def _make_read_resource_tool(session: ClientSession) -> StructuredTool:
    """Create a LangChain tool that reads an MCP resource by URI (resources/read)."""

    async def _invoke(uri: str) -> str:
        try:
            result = await session.read_resource(uri)
            return _format_read_resource_result(result)
        except BaseException as e:
            return _format_mcp_error(e)

    async def _call(**kwargs: Any) -> str:  # noqa: ANN401
        return await _invoke(kwargs["uri"])

    return StructuredTool(
        name="read_resource",
        description=(
            "Read the content of an MCP resource by its URI (resources/read). "
            "First use list_resources to get available resource URIs."
        ),
        args_schema=_ReadResourceArgs,
        coroutine=_call,
    )


def _make_langchain_tool(session: ClientSession, mcp_tool: Any) -> StructuredTool:  # noqa: ANN401
    """Convert an MCP tool to a LangChain StructuredTool."""
    tool_name = mcp_tool.name
    tool_desc = mcp_tool.description or ""
    input_schema = mcp_tool.inputSchema or {}
    properties = input_schema.get("properties") or {}
    required = input_schema.get("required") or []

    try:
        args_schema = _json_schema_to_args_model(tool_name, input_schema)
    except Exception:
        _logger.warning("Could not build args schema for %s, using generic", tool_name)
        args_schema = _MCPToolArgs

    schema_hint = ""
    if properties:
        hint_parts = [f"Required: {list(required)}" if required else "Parameters:"]
        for k, v in properties.items():
            desc = (v.get("description") or "")[:80]
            hint_parts.append(f"  - {k}: {v.get('type', 'any')}" + (f" ({desc})" if desc else ""))
        schema_hint = "\n\n" + "\n".join(hint_parts)
    full_description = (tool_desc + schema_hint).strip() or "MCP tool (no description)"

    async def _invoke(**kwargs: Any) -> str:  # noqa: ANN401
        args = {k: v for k, v in kwargs.items() if v is not None}
        # Guard: ciq_execute needs a query id. Reject id-less calls with actionable
        # feedback instead of forwarding empty args (which the server rejects and
        # the LLM would otherwise retry in a loop).
        if tool_name == "ciq_execute" and not args.get("id"):
            return (
                'ciq_execute requires a query "id" (e.g. get-self, get-stock-quote, '
                "get-internal-documents, get-decisions) and its input_params. "
                "Retry with a concrete id, or call list_resources to discover valid ids."
            )
        _logger.info(
            "MCP RPC request: %s",
            json.dumps(
                {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": tool_name, "arguments": args}},
                default=str,
            ),
        )
        result = await session.call_tool(tool_name, args)
        return _format_call_tool_result(result)

    return StructuredTool(
        name=tool_name,
        description=full_description,
        args_schema=args_schema,
        coroutine=_invoke,
    )


def _get_access_token_from_context(context: RequestContext | None) -> str:
    """Extract Bearer token from the Authorization header of the incoming request."""
    if not context or not context.call_context:
        return ""
    req_headers = context.call_context.state.get("headers") or {}
    auth = req_headers.get("authorization") or req_headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return auth.strip() if auth else ""


# ---------------------------------------------------------------------------
# MCP session cache
#
# Session setup (initialize + tools/list + resources/list, each message paying
# the gateway's introspection/authz round trips) dominates request latency, so
# the session is cached per user token for MCP_SESSION_TTL seconds and
# validated with a ping before reuse.
#
# anyio's streamable-http transport contexts must be entered and exited in the
# same task, so the connection is held open by a dedicated owner task rather
# than a request-scoped context manager.
# ---------------------------------------------------------------------------

_MCP_PING_TIMEOUT = 3.0
_MCP_MAX_ACQUIRE_ATTEMPTS = 2


def _token_cache_identity(token: str) -> str:
    """Stable per-user identity for the MCP session cache key.

    The gateway mints a fresh delegation JWT for every request (new jti/iat),
    so keying on the raw token would miss the cache on every request. Key on
    the claims that determine downstream authorization instead: subject,
    actor-delegation chain, and client. Falls back to the raw token when the
    payload cannot be decoded (e.g. an opaque token).

    The claims are read WITHOUT signature verification, which is safe only
    because they merely select the cache slot: refresh_auth() re-points the
    cached transport at the incoming token before reuse, so every downstream
    message is still authenticated by the gateway against the caller's own
    credential - a forged token cannot ride a cached session.
    """
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        actors: list[str] = []
        act = claims.get("act")
        while isinstance(act, dict):
            actors.append(str(act.get("sub", "")))
            act = act.get("act")
        ident = {
            "sub": claims.get("sub"),
            "azp": claims.get("azp"),
            "aud": claims.get("aud"),
            "act": actors,
        }
        return json.dumps(ident, sort_keys=True, default=str)
    except Exception:
        return token


class _McpConn:
    """The MCP server connection held open by a dedicated owner task."""

    def __init__(self) -> None:
        """Prepare a connection; nothing happens until start()."""
        self.ready = asyncio.Event()
        self.result: tuple[ClientSession, list, list] | None = None
        self.client: httpx.AsyncClient | None = None
        self.error: BaseException | None = None
        self._close = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self, headers: dict[str, str]) -> None:
        """Spawn the owner task; ready is set once connected (or failed)."""
        self._task = asyncio.get_running_loop().create_task(self._own(headers))

    async def _own(self, headers: dict[str, str]) -> None:
        """Own the transport contexts for the connection's whole lifetime.

        Transport failures (e.g. a gateway 403 during initialize) surface from
        anyio task groups as BaseExceptionGroup; they are stored on self.error
        for the acquiring request to re-raise, since an owner task has no
        caller to propagate to.
        """
        try:
            async with AsyncExitStack() as stack:
                self.client = await stack.enter_async_context(create_mcp_http_client(headers=headers))
                read, write, _ = await stack.enter_async_context(
                    streamable_http_client(
                        MCP_SERVER_URL,
                        http_client=self.client,
                        terminate_on_close=False,  # Indykite returns 403 on DELETE
                    ),
                )
                session = await stack.enter_async_context(ClientSession(read, write))
                async with asyncio.timeout(MCP_SETUP_TIMEOUT):
                    await session.initialize()
                    mcp_tools = await _list_all_mcp_tools(session)
                    mcp_resources = await _list_all_mcp_resources(session)
                self.result = (session, mcp_tools, mcp_resources)
                self.ready.set()
                await self._close.wait()
        except BaseException as e:
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            self.error = e
        finally:
            self.ready.set()

    async def shutdown(self) -> None:
        """Ask the owner task to unwind its contexts and wait for it."""
        self._close.set()
        if self._task is not None:
            with suppress(BaseException):
                async with asyncio.timeout(MCP_SETUP_TIMEOUT):
                    await self._task


class _McpToolsEntry:
    """A live MCP session plus its composed tools for one user token."""

    def __init__(self) -> None:
        """Create an empty, not-yet-built entry."""
        self.conn: _McpConn | None = None
        self.tools: list[StructuredTool] = []
        self.ready = asyncio.Event()
        self.created = time.monotonic()
        self.refs = 0
        self.retired = False
        self.complete = False

    @property
    def expired(self) -> bool:
        """Whether the entry is past its reuse window."""
        return MCP_SESSION_TTL <= 0 or (time.monotonic() - self.created) > MCP_SESSION_TTL

    async def build(self, headers: dict[str, str]) -> None:
        """Connect and compose the LangChain tools."""
        try:
            self.conn = _McpConn()
            self.conn.start(headers)
            await self.conn.ready.wait()
            if self.conn.result is not None:
                session, mcp_tools, mcp_resources = self.conn.result
                lc_tools: list[StructuredTool] = [
                    _make_list_resources_tool(session),
                    _make_read_resource_tool(session),
                    _make_max_purchase_amount_tool(session),
                ] + [_make_langchain_tool(session, t) for t in mcp_tools]
                activate_skill_tool = _make_activate_skill_tool(_SKILL_REGISTRY)
                if activate_skill_tool is not None:
                    lc_tools = [activate_skill_tool, *lc_tools]
                self.tools = lc_tools
                self.complete = True
                _logger.info(
                    "MCP from %s: %d tools, %d resources",
                    MCP_SERVER_URL,
                    len(mcp_tools),
                    len(mcp_resources),
                )
        finally:
            self.ready.set()

    def refresh_auth(self, headers: dict[str, str]) -> None:
        """Point the cached transport at the caller's current credentials.

        The cache key only *selects* the entry - the Authorization actually
        forwarded downstream is always the incoming request's token. A forged
        token whose claims collide with the key therefore fails the gateway's
        introspection on the very next message (the ping) instead of riding
        the credential the session was built with.
        """
        if self.conn is not None and self.conn.client is not None:
            self.conn.client.headers.update(headers)

    async def ping(self) -> bool:
        """Probe the cached session before reuse; False means rebuild."""
        if self.conn is None or self.conn.result is None:
            return False
        try:
            async with asyncio.timeout(_MCP_PING_TIMEOUT):
                await self.conn.result[0].send_ping()
        except BaseException as e:
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            _logger.warning(
                "Cached MCP session failed ping - rebuilding: %s",
                _format_mcp_error(e)[:120],
            )
            return False
        return True

    async def shutdown(self) -> None:
        """Close the connection (in its own owner task)."""
        if self.conn is not None:
            await self.conn.shutdown()


_MCP_TOOLS_CACHE: dict[str, _McpToolsEntry] = {}
_MCP_CACHE_LOCK = asyncio.Lock()
# Fire-and-forget shutdown tasks, referenced so they are not GC'd mid-flight.
_MCP_BG_TASKS: set[asyncio.Task] = set()


def _spawn_bg(coro) -> None:
    """Run *coro* as a background task the loop keeps a reference to."""
    task = asyncio.get_running_loop().create_task(coro)
    _MCP_BG_TASKS.add(task)
    task.add_done_callback(_MCP_BG_TASKS.discard)


async def _retire_entry(key: str, entry: _McpToolsEntry, *, drop_ref: bool = False) -> None:
    """Remove *entry* from the cache; close it once nobody is using it."""
    async with _MCP_CACHE_LOCK:
        entry.retired = True
        if _MCP_TOOLS_CACHE.get(key) is entry:
            _MCP_TOOLS_CACHE.pop(key)
        if drop_ref:
            entry.refs -= 1
        close_now = entry.refs <= 0
    if close_now:
        _spawn_bg(entry.shutdown())


def _sweep_expired_entries() -> None:
    """Drop expired cache entries; call only while holding _MCP_CACHE_LOCK.

    After a token refresh the old token's entry would otherwise linger,
    holding its session open forever.
    """
    for key, entry in list(_MCP_TOOLS_CACHE.items()):
        if entry.expired:
            _MCP_TOOLS_CACHE.pop(key)
            entry.retired = True
            if entry.refs <= 0:
                _spawn_bg(entry.shutdown())


async def _acquire_tools_entry(key: str, headers: dict[str, str], attempts: int = 0) -> _McpToolsEntry:
    """Return a ready tools entry for this token - cached, rebuilt, or fresh.

    Raises the stored connect error when the session cannot be established,
    matching the old per-request behavior (the caller formats it into the
    final response text).
    """
    force_fresh = attempts >= _MCP_MAX_ACQUIRE_ATTEMPTS
    builder = False
    async with _MCP_CACHE_LOCK:
        _sweep_expired_entries()
        entry = None if force_fresh else _MCP_TOOLS_CACHE.get(key)
        if entry is None:
            entry = _McpToolsEntry()
            builder = True
            if MCP_SESSION_TTL > 0 and not force_fresh:
                _MCP_TOOLS_CACHE[key] = entry
            else:
                entry.retired = True
        entry.refs += 1

    if builder:
        try:
            await entry.build(headers)
        except BaseException:
            await _retire_entry(key, entry, drop_ref=True)
            raise
        if not entry.complete:
            error = entry.conn.error if entry.conn is not None else None
            await _retire_entry(key, entry, drop_ref=True)
            if error is not None:
                raise error
            msg = f"MCP session to {MCP_SERVER_URL} could not be established"
            raise RuntimeError(msg)
        return entry

    await entry.ready.wait()
    entry.refresh_auth(headers)
    if entry.complete and not entry.expired and await entry.ping():
        return entry
    await _retire_entry(key, entry, drop_ref=True)
    return await _acquire_tools_entry(key, headers, attempts + 1)


async def _release_tools_entry(entry: _McpToolsEntry) -> None:
    """Drop one reference; close the entry if it is retired and unused."""
    async with _MCP_CACHE_LOCK:
        entry.refs -= 1
        close_now = entry.retired and entry.refs <= 0
    if close_now:
        await entry.shutdown()


@asynccontextmanager
async def _mcp_session(access_token: str = ""):
    """Yield the LangChain tools backed by a per-user cached MCP session.

    The session is cached per user token for MCP_SESSION_TTL seconds (ping-
    validated before reuse), so repeat prompts skip the expensive session
    setup. A connect failure raises, as before.
    """
    if not MCP_SERVER_URL:
        yield []
        return

    # The MCP server resolves the AppAgent identity server-side from the project's
    # MCP server configuration (app_agent_id); the caller sends only the user's
    # Bearer token. X-IK-ClientKey is no longer used.
    headers: dict[str, str] = {}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if INDYKITE_BASE_URL:
        headers["X-IndyKite-Base-URL"] = INDYKITE_BASE_URL

    key_material = _token_cache_identity(access_token) + "|" + INDYKITE_BASE_URL
    key = hashlib.sha256(key_material.encode()).hexdigest()
    entry = await _acquire_tools_entry(key, headers)
    try:
        yield entry.tools
    finally:
        await _release_tools_entry(entry)


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


def _message_prompt(context: RequestContext) -> str:
    """Extract the text prompt from the inbound message (SDK 1.0 Part.text)."""
    raw_text = ""
    if context.message:
        for part in context.message.parts or []:
            t = getattr(part, "text", None)
            if t:
                raw_text += t
    return raw_text


def _block_text(block: Any) -> str:  # noqa: ANN401
    """Extract text from a single content block (dict or object with .text)."""
    if isinstance(block, dict):
        if block.get("type") == "text":
            return str(block.get("text", ""))
        if "text" in block:
            return str(block["text"])
    t = getattr(block, "text", None)
    return str(t) if t else ""


def _response_final_text(response: Any) -> str:  # noqa: ANN401
    """Extract the final text from an LLM response (handles list-of-blocks content)."""
    response_text = getattr(response, "text", "") or ""
    response_content = getattr(response, "content", "") or ""
    if isinstance(response_content, list) and response_content and not response_text:
        parts = [t for t in (_block_text(b) for b in response_content) if t]
        if parts:
            response_text = " ".join(parts)
    if not isinstance(response_content, str):
        response_content = str(response_content) if response_content else ""
    return str(response_text or response_content or "")


async def _invoke_tool(tools: list[StructuredTool], name: str, args: dict) -> str:
    """Find a tool by name and invoke it, returning a string result or an error message."""
    for lc_tool in tools:
        if lc_tool.name != name:
            continue
        if name == "activate_skill":
            _logger.info("Using skill: activate_skill (loading: %s)", args.get("name", "?"))
        else:
            _logger.info("Using tool: %s", name)
        try:
            return str(await lc_tool.ainvoke(args))
        except BaseException as e:
            _logger.warning("MCP error during tool %s", name)
            return _format_mcp_error(e)
    return "Tool not found"


async def _run_tool_calls(tool_calls: list[dict], tools: list[StructuredTool]) -> list[ToolMessage]:
    """Execute each requested tool call and return their ToolMessage results."""
    results: list[ToolMessage] = []
    for tc in tool_calls:
        name = tc.get("name", "")
        args = tc.get("args", {}) or {}
        tid = tc.get("id", "")
        result = await _invoke_tool(tools, name, args)
        results.append(ToolMessage(content=result, tool_call_id=tid))
    return results


async def _run_llm_loop(llm: Any, tools: list[StructuredTool], prompt: str) -> str:  # noqa: ANN401
    """Run the LLM + tool-calling loop. Returns the final response text."""
    messages: list[Any] = [
        HumanMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ]
    for iteration in range(_TOOL_CALL_MAX_ITERATIONS):
        try:
            response = await llm.ainvoke(messages)
        except Exception as e:
            _logger.warning("LLM call failed (%s): %s", type(e).__name__, e)
            return f"LLM error ({type(e).__name__}): {e}"
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            final_text = _response_final_text(response)
            if not final_text:
                _logger.warning(
                    "LLM returned no tool_calls and empty text/content: type=%s",
                    type(response).__name__,
                )
            return final_text
        _logger.info(
            "LLM iteration %d: %d tool_call(s): %s",
            iteration + 1,
            len(tool_calls),
            [tc.get("name", "?") for tc in tool_calls],
        )
        messages.append(response)
        messages.extend(await _run_tool_calls(tool_calls, tools))
    _logger.warning("Exhausted %d iterations (LLM kept making tool calls)", _TOOL_CALL_MAX_ITERATIONS)
    # One last no-more-tools turn so the results gathered so far still become
    # an answer instead of "(No response generated)".
    messages.append(
        HumanMessage(
            content=(
                "Tool budget exhausted. Answer the user's question now in plain "
                "text using only the tool results above; do not request any more tools."
            ),
        ),
    )
    try:
        response = await llm.ainvoke(messages)
    except Exception as e:
        _logger.warning("Final LLM call failed (%s): %s", type(e).__name__, e)
        return f"LLM error ({type(e).__name__}): {e}"
    return _response_final_text(response)


async def _emit_working(context: RequestContext, event_queue: EventQueue) -> None:
    """Emit the initial task + working status events for a new request."""
    task = context.current_task or new_task_from_user_message(context.message)
    await event_queue.enqueue_event(task)
    await event_queue.enqueue_event(
        TaskStatusUpdateEvent(
            task_id=context.task_id,
            context_id=context.context_id,
            status=TaskStatus(
                state=TaskState.TASK_STATE_WORKING,
                message=new_text_message("Retrieving data..."),
            ),
        ),
    )


async def _emit_completed(context: RequestContext, event_queue: EventQueue, final_text: str) -> None:
    """Emit the artifact and completion status for a finished request."""
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


async def _process_retriever_request(
    context: RequestContext,
    event_queue: EventQueue,
    access_token: str,
) -> None:
    """Drive one inbound message through the MCP session and LLM loop."""
    prompt = _message_prompt(context) or "(empty)"
    _logger.info("Received message for %s: %s", RETRIEVER_AGENT_NAME, prompt)
    await _emit_working(context, event_queue)

    try:
        async with _mcp_session(access_token=access_token) as tools:
            llm = _llm.bind_tools(tools) if tools else _llm
            final_text = await _run_llm_loop(llm, tools, prompt)
    except BaseException as e:
        final_text = _format_mcp_error(e)
        _logger.warning("MCP session error: %s", final_text)

    final_text = final_text or "(No response generated)"
    await _emit_completed(context, event_queue, final_text)
    _logger.info("LLM response for %s: %s...", RETRIEVER_AGENT_NAME, final_text[:200])


class RetrieverExecutor(AgentExecutor):
    """AgentExecutor that uses MCP server tools via an LLM with tool-calling."""

    # skipcq: PYL-R0201 - AgentExecutor interface override; must stay an instance method
    async def execute(  # noqa: D102
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        access_token = _get_access_token_from_context(context)
        if not access_token:
            raise HTTPException(status_code=401, detail="Authorization required")
        await _process_retriever_request(context, event_queue, access_token)

    # skipcq: PYL-R0201 - AgentExecutor interface override; must stay an instance method
    async def cancel(  # noqa: D102
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
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
        agent_executor=RetrieverExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=retriever_card,
    )
    app = Starlette(
        routes=[
            *create_agent_card_routes(agent_card=retriever_card),
            *create_jsonrpc_routes(request_handler=handler, rpc_url=DEFAULT_RPC_URL),
        ],
    )

    llm_info = f"Gemini {GEMINI_MODEL}" if (GEMINI_ENABLED and GEMINI_API_KEY) else LLM_MODEL
    _logger.info("Starting %s on port %d (LLM: %s)", RETRIEVER_AGENT_NAME, RETRIEVER_PORT, llm_info)
    if MCP_SERVER_URL:
        _logger.info("MCP server: %s", MCP_SERVER_URL)
    else:
        _logger.warning("MCP_SERVER_URL not set - agent will run without MCP tools")
    # uvicorn must bind to 0.0.0.0 inside Docker; safe because the container
    # network exposes only the intended port via compose.
    uvicorn.run(
        app,
        host="0.0.0.0",  # nosec B104  # noqa: S104
        port=RETRIEVER_PORT,
        log_level="info",
    )
