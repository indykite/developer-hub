"""Weather agent - A2A-compliant agent that returns current weather by city.

For requests targeting CanBank's headquarters ("HQ", "headquarters", "office") the
agent calls the canbank `get-hq-weather` knowledge query through the IndyKite MCP
server. The query reads the `hq_weather` Weather node, which carries `latitude` and
`longitude` properties feeding the `weather` and `weather-units` external data
resolvers. For any other city the agent falls back to a direct open-meteo call.
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import time
import uuid
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from typing import Any

import httpx
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
from mcp import ClientSession
from mcp.client.streamable_http import (
    StreamableHTTPTransport,
    streamable_http_client,
)
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import CallToolResult, TextContent
from starlette.applications import Starlette
from starlette.exceptions import HTTPException

load_dotenv()

# ---------------------------------------------------------------------------
# MCP transport workaround for Indykite (same patch retriever_agent uses)
# ---------------------------------------------------------------------------
# Indykite MCP returns 202 Accepted with Mcp-Session-Id in headers. The Python MCP
# SDK returns early on 202 without extracting the session ID, causing the next GET
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

WEATHER_PORT = int(os.getenv("WEATHER_PORT", "6004"))
ADVERTISED_HOST = os.getenv("ADVERTISED_HOST", "weather")
WEATHER_AGENT_NAME = os.getenv("WEATHER_AGENT_NAME", "weather_agent")
DEFAULT_CITY = os.getenv("WEATHER_DEFAULT_CITY", "London").strip()
WEATHER_TIMEOUT = float(os.getenv("WEATHER_TIMEOUT", "15"))
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "").strip()
INDYKITE_BASE_URL = os.getenv("INDYKITE_BASE_URL", "").strip()
# Session-setup deadline (initialize); a hung downstream would otherwise
# leave the owner task waiting forever.
MCP_SETUP_TIMEOUT = float(os.getenv("MCP_SETUP_TIMEOUT", "20"))
# How long (seconds) the per-user MCP session is reused across requests
# before being rebuilt. Keep it below the access-token lifetime; 0 restores
# the old behavior of a fresh session per request.
MCP_SESSION_TTL = float(os.getenv("MCP_SESSION_TTL", "300"))
CIQ_QUERY_HQ_WEATHER = os.getenv("CIQ_QUERY_HQ_WEATHER", "").strip() or "get-hq-weather"
_HQ_KEYWORDS = ("hq", "headquarters", "head office", "head-office", "canbank office", "the office")
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

weather_card = AgentCard(
    name=WEATHER_AGENT_NAME,
    description=("Weather agent that returns current weather for a requested city using a public weather API."),
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
            url=f"http://{ADVERTISED_HOST}:{WEATHER_PORT}",
            protocol_version="1.0",
        ),
    ],
    skills=[
        AgentSkill(
            id="current-weather",
            name="Current Weather",
            description=(
                "Get current weather conditions for a city. CanBank HQ requests "
                "(prompts mentioning HQ, headquarters or office) are resolved through "
                "the IndyKite knowledge graph via the get-hq-weather query."
            ),
            tags=["weather", "forecast", "temperature", "hq", "ciq"],
            examples=[
                "What's the weather in London?",
                "Current weather in New York",
                "How warm is it in Oslo right now?",
                "What's the weather at CanBank HQ?",
                "Current conditions at the office",
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


def _message_text(context: RequestContext) -> str:
    chunks: list[str] = []
    if context.message:
        for part in context.message.parts or []:
            text = getattr(part, "text", None)
            if text:
                chunks.append(text)
    return "".join(chunks).strip()


def _extract_city(prompt: str) -> str:
    if not prompt:
        return DEFAULT_CITY
    patterns = [
        r"\bin\s+([A-Za-z][A-Za-z .'-]{1,60})\??$",
        r"\bfor\s+([A-Za-z][A-Za-z .'-]{1,60})\??$",
        r"\bat\s+([A-Za-z][A-Za-z .'-]{1,60})\??$",
    ]
    for pattern in patterns:
        match = re.search(pattern, prompt.strip(), flags=re.IGNORECASE)
        if match:
            city = match.group(1).strip(" .!?")
            if city:
                return city
    cleaned = prompt.strip(" .!?")
    if 0 < len(cleaned) <= 60 and len(cleaned.split()) <= 4:  # noqa: PLR2004
        return cleaned
    return DEFAULT_CITY


def _is_hq_request(prompt: str) -> bool:
    """Return True if the user is asking about CanBank's headquarters weather."""
    if not prompt:
        return False
    lowered = prompt.lower()
    return any(kw in lowered for kw in _HQ_KEYWORDS)


def _format_call_tool_result(result: CallToolResult) -> str:
    """Convert MCP CallToolResult to a string. Mirrors retriever_agent's helper."""
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


def _extract_node_props(result: CallToolResult) -> dict[str, Any]:
    """Pull the first row of `data[0].nodes` out of a ciq_execute response.

    The response shape from /contx-iq/v1/execute is:
      {"data": [{"nodes": {"<alias>": {...}, "<alias>.property.<name>": <value>, ...}}]}
    Tries structuredContent first, then each text block individually (JSON-parsed).
    """
    candidates: list[Any] = []
    if isinstance(result.structuredContent, dict):
        candidates.append(result.structuredContent)
    for block in result.content or []:
        if isinstance(block, TextContent) and block.text:
            try:
                candidates.append(json.loads(block.text))
            except (json.JSONDecodeError, ValueError):
                continue

    for payload in candidates:
        if not isinstance(payload, dict):
            continue
        rows = payload.get("data") or []
        if not rows or not isinstance(rows[0], dict):
            continue
        nodes = rows[0].get("nodes")
        if isinstance(nodes, dict):
            return nodes
    return {}


def _format_weather_sentence(location: str, current: dict[str, Any], units: dict[str, Any]) -> str:
    """Format the same sentence the httpx path returns, from the CIQ result objects."""
    temp = current.get("temperature_2m")
    feels_like = current.get("apparent_temperature")
    wind = current.get("wind_speed_10m")
    weather_code = current.get("weather_code")
    observed_at = current.get("time")
    return (
        f"Current weather for {location}: "
        f"{temp}{units.get('temperature_2m', 'C')} "
        f"(feels like {feels_like}{units.get('apparent_temperature', 'C')}), "
        f"wind {wind}{units.get('wind_speed_10m', 'km/h')}, "
        f"weather code {weather_code}. "
        f"Observation time: {observed_at}."
    )


# ---------------------------------------------------------------------------
# MCP session cache
#
# Session setup (initialize, each message paying the gateway's introspection
# and authz round trips) dominates the HQ-weather route's latency, so the
# session is cached per user token for MCP_SESSION_TTL seconds and validated
# with a ping before reuse - mirroring retriever_agent's cache.
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
        self.result: ClientSession | None = None
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
                self.result = session
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


class _McpSessionEntry:
    """A live MCP session for one user token."""

    def __init__(self) -> None:
        """Create an empty, not-yet-built entry."""
        self.conn: _McpConn | None = None
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
        """Connect the session."""
        try:
            self.conn = _McpConn()
            self.conn.start(headers)
            await self.conn.ready.wait()
            self.complete = self.conn.result is not None
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
                await self.conn.result.send_ping()
        except BaseException as e:
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            _logger.warning(
                "Cached MCP session failed ping - rebuilding: %s",
                _format_exception_chain(e)[:120],
            )
            return False
        return True

    async def shutdown(self) -> None:
        """Close the connection (in its own owner task)."""
        if self.conn is not None:
            await self.conn.shutdown()


_MCP_SESSION_CACHE: dict[str, _McpSessionEntry] = {}
_MCP_CACHE_LOCK = asyncio.Lock()
# Fire-and-forget shutdown tasks, referenced so they are not GC'd mid-flight.
_MCP_BG_TASKS: set[asyncio.Task] = set()


def _spawn_bg(coro) -> None:
    """Run *coro* as a background task the loop keeps a reference to."""
    task = asyncio.get_running_loop().create_task(coro)
    _MCP_BG_TASKS.add(task)
    task.add_done_callback(_MCP_BG_TASKS.discard)


async def _retire_entry(key: str, entry: _McpSessionEntry, *, drop_ref: bool = False) -> None:
    """Remove *entry* from the cache; close it once nobody is using it."""
    async with _MCP_CACHE_LOCK:
        entry.retired = True
        if _MCP_SESSION_CACHE.get(key) is entry:
            _MCP_SESSION_CACHE.pop(key)
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
    for key, entry in list(_MCP_SESSION_CACHE.items()):
        if entry.expired:
            _MCP_SESSION_CACHE.pop(key)
            entry.retired = True
            if entry.refs <= 0:
                _spawn_bg(entry.shutdown())


async def _acquire_session_entry(key: str, headers: dict[str, str], attempts: int = 0) -> _McpSessionEntry:
    """Return a ready session entry for this token - cached, rebuilt, or fresh.

    Raises the stored connect error when the session cannot be established,
    matching the old per-request behavior.
    """
    force_fresh = attempts >= _MCP_MAX_ACQUIRE_ATTEMPTS
    builder = False
    async with _MCP_CACHE_LOCK:
        _sweep_expired_entries()
        entry = None if force_fresh else _MCP_SESSION_CACHE.get(key)
        if entry is None:
            entry = _McpSessionEntry()
            builder = True
            if MCP_SESSION_TTL > 0 and not force_fresh:
                _MCP_SESSION_CACHE[key] = entry
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
    return await _acquire_session_entry(key, headers, attempts + 1)


async def _release_session_entry(entry: _McpSessionEntry) -> None:
    """Drop one reference; close the entry if it is retired and unused."""
    async with _MCP_CACHE_LOCK:
        entry.refs -= 1
        close_now = entry.retired and entry.refs <= 0
    if close_now:
        await entry.shutdown()


@asynccontextmanager
async def _mcp_session(access_token: str):
    """Yield a per-user cached MCP session (ping-validated before reuse).

    Repeat HQ-weather prompts skip the expensive session setup. A connect
    failure raises, as before.
    """
    if not MCP_SERVER_URL:
        msg = "MCP_SERVER_URL not configured"
        raise RuntimeError(msg)

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
    entry = await _acquire_session_entry(key, headers)
    try:
        yield entry.conn.result
    finally:
        await _release_session_entry(entry)


def _unwrap_exception(exc: BaseException) -> list[BaseException]:
    """Walk an ExceptionGroup / chained exception tree and return its leaf exceptions."""
    seen: set[int] = set()
    leaves: list[BaseException] = []
    pending: list[BaseException] = [exc]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        sub = getattr(current, "exceptions", None)
        if sub:
            pending.extend(sub)
            continue
        leaves.append(current)
        pending.extend(c for c in (getattr(current, "__cause__", None), getattr(current, "__context__", None)) if c)
    return leaves


def _format_exception_chain(exc: BaseException) -> str:
    """Render every leaf in an ExceptionGroup as `Type: message`, joined by ' | '."""
    return " | ".join(f"{type(e).__name__}: {e}" for e in _unwrap_exception(exc)) or repr(exc)


async def _fetch_hq_weather_via_ciq(access_token: str) -> str:
    """Run the canbank get-hq-weather query and format the standard weather sentence."""
    async with _mcp_session(access_token) as session:
        result = await session.call_tool(
            "ciq_execute",
            {"id": CIQ_QUERY_HQ_WEATHER, "input_params": {}},
        )

    nodes = _extract_node_props(result)
    if not nodes:
        msg = f"ciq_execute({CIQ_QUERY_HQ_WEATHER}) returned no rows: {_format_call_tool_result(result)[:300]}"
        raise RuntimeError(msg)

    location = nodes.get("weather.property.location") or "CanBank HQ"
    current = nodes.get("weather.property.current")
    units = nodes.get("weather.property.units")
    if not isinstance(current, dict) or not isinstance(units, dict):
        msg = (
            f"ciq_execute({CIQ_QUERY_HQ_WEATHER}) missing weather.property.current/units; got keys={list(nodes.keys())}"
        )
        raise TypeError(msg)

    return _format_weather_sentence(str(location), current, units)


async def _fetch_current_weather(city: str) -> str:
    timeout = httpx.Timeout(WEATHER_TIMEOUT)
    async with httpx.AsyncClient(timeout=timeout) as client:
        geo_resp = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "en", "format": "json"},
        )
        geo_resp.raise_for_status()
        geo_data = geo_resp.json()
        results = geo_data.get("results") or []
        if not results:
            return f"I couldn't find a location match for '{city}'. Please provide a clearer city name."

        loc = results[0]
        latitude = loc.get("latitude")
        longitude = loc.get("longitude")
        resolved_city = loc.get("name") or city
        country = loc.get("country") or ""

        weather_resp = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,apparent_temperature,wind_speed_10m,weather_code",
                "timezone": "auto",
            },
        )
        weather_resp.raise_for_status()
        weather_data = weather_resp.json()
        current = weather_data.get("current") or {}
        units = weather_data.get("current_units") or {}

        temp = current.get("temperature_2m")
        feels_like = current.get("apparent_temperature")
        wind = current.get("wind_speed_10m")
        weather_code = current.get("weather_code")
        observed_at = current.get("time")

        location = resolved_city if not country else f"{resolved_city}, {country}"
        return (
            f"Current weather for {location}: "
            f"{temp}{units.get('temperature_2m', 'C')} "
            f"(feels like {feels_like}{units.get('apparent_temperature', 'C')}), "
            f"wind {wind}{units.get('wind_speed_10m', 'km/h')}, "
            f"weather code {weather_code}. "
            f"Observation time: {observed_at}."
        )


class WeatherExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:  # noqa: D102
        access_token = _get_access_token_from_context(context)
        if not access_token:
            raise HTTPException(status_code=401, detail="Authorization required")

        prompt = _message_text(context)
        _logger.info("Received message for %s: %s", WEATHER_AGENT_NAME, prompt)

        task = context.current_task or new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(
                    state=TaskState.TASK_STATE_WORKING,
                    message=new_text_message("Fetching weather..."),
                ),
            ),
        )

        is_hq = _is_hq_request(prompt)
        # When the user asked about HQ, _extract_city would yield e.g. "CanBank HQ"
        # which the geocoder can't resolve. Use DEFAULT_CITY for the HQ fallback so
        # the user still gets weather data when the CIQ path is unavailable.
        city = DEFAULT_CITY if is_hq else _extract_city(prompt)
        use_ciq = is_hq and bool(MCP_SERVER_URL)
        try:
            if use_ciq:
                _logger.info("HQ weather request — calling ciq_execute(%s)", CIQ_QUERY_HQ_WEATHER)
                try:
                    result_text = await _fetch_hq_weather_via_ciq(access_token)
                except Exception as ciq_exc:
                    _logger.warning(
                        "CIQ HQ weather failed, falling back to direct fetch for %s: %s",
                        city,
                        _format_exception_chain(ciq_exc),
                    )
                    # Full traceback only at DEBUG to keep WARNING rows scannable in production logs.
                    _logger.debug("CIQ HQ weather traceback", exc_info=ciq_exc)
                    result_text = await _fetch_current_weather(city)
            else:
                result_text = await _fetch_current_weather(city)
        except Exception as exc:
            _logger.warning("Weather lookup failed for %s: %s", city, exc)
            result_text = f"I couldn't fetch weather for '{city}' right now. Please try again in a moment."

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
        _logger.info("Weather response: %s", result_text[:200])

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
        agent_executor=WeatherExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=weather_card,
    )
    app = Starlette(
        routes=[
            *create_agent_card_routes(agent_card=weather_card),
            *create_jsonrpc_routes(request_handler=handler, rpc_url=DEFAULT_RPC_URL),
        ],
    )
    _logger.info("Starting %s on port %d", WEATHER_AGENT_NAME, WEATHER_PORT)
    if MCP_SERVER_URL:
        _logger.info("HQ weather route enabled: ciq_execute(%s) via %s", CIQ_QUERY_HQ_WEATHER, MCP_SERVER_URL)
    else:
        _logger.info("MCP_SERVER_URL not set — HQ weather will fall back to the direct open-meteo path")
    uvicorn.run(
        app,
        host="0.0.0.0",  # nosec B104  # noqa: S104
        port=WEATHER_PORT,
        log_level="info",
    )
