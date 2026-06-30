"""Weather agent - A2A-compliant agent that returns current weather by city.

For requests targeting CanBank's headquarters ("HQ", "headquarters", "office") the
agent calls the canbank `get-hq-weather` knowledge query through the IndyKite MCP
server. The query reads the `hq_weather` Weather node, which carries `latitude` and
`longitude` properties feeding the `weather` and `weather-units` external data
resolvers. For any other city the agent falls back to a direct open-meteo call.
"""

import json
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
import uvicorn
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
from a2a.utils import new_agent_text_message, new_task, new_text_artifact
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


StreamableHTTPTransport._handle_post_request = _patched_handle_post_request  # noqa: SLF001

WEATHER_PORT = int(os.getenv("WEATHER_PORT", "6004"))
ADVERTISED_HOST = os.getenv("ADVERTISED_HOST", "weather")
WEATHER_AGENT_NAME = os.getenv("WEATHER_AGENT_NAME", "weather_agent")
DEFAULT_CITY = os.getenv("WEATHER_DEFAULT_CITY", "London").strip()
WEATHER_TIMEOUT = float(os.getenv("WEATHER_TIMEOUT", "15"))
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "").strip()
INDYKITE_BASE_URL = os.getenv("INDYKITE_BASE_URL", "").strip()
CIQ_QUERY_HQ_WEATHER = os.getenv("CIQ_QUERY_HQ_WEATHER", "").strip() or "get-hq-weather"
_HQ_KEYWORDS = ("hq", "headquarters", "head office", "head-office", "canbank office", "the office")
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
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


@asynccontextmanager
async def _mcp_session(access_token: str):
    """Open an MCP session against IndyKite, mirroring retriever_agent's wiring."""
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

    async with (  # noqa: SIM117
        create_mcp_http_client(headers=headers) as client,
        streamable_http_client(
            MCP_SERVER_URL,
            http_client=client,
            terminate_on_close=False,  # Indykite returns 403 on DELETE
        ) as (read, write, _),
    ):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


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

        task = context.current_task or new_task(context.message)
        await event_queue.enqueue_event(task)

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(
                    state=TaskState.TASK_STATE_WORKING,
                    message=new_agent_text_message("Fetching weather..."),
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
