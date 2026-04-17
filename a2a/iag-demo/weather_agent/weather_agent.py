"""Weather agent - A2A-compliant agent that returns current weather by city."""

import logging
import os
import re
import uuid

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
from starlette.applications import Starlette
from starlette.exceptions import HTTPException

load_dotenv()

WEATHER_PORT = int(os.getenv("WEATHER_PORT", 6004))  # noqa: PLW1508
ADVERTISED_HOST = os.getenv("ADVERTISED_HOST", "weather")
WEATHER_AGENT_NAME = os.getenv("WEATHER_AGENT_NAME", "weather_agent")
DEFAULT_CITY = os.getenv("WEATHER_DEFAULT_CITY", "London").strip()
WEATHER_TIMEOUT = float(os.getenv("WEATHER_TIMEOUT", "15"))
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
            description="Get current weather conditions for a city.",
            tags=["weather", "forecast", "temperature"],
            examples=[
                "What's the weather in London?",
                "Current weather in New York",
                "How warm is it in Oslo right now?",
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

        city = _extract_city(prompt)
        try:
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
    uvicorn.run(
        app,
        host="0.0.0.0",  # nosec B104  # noqa: S104
        port=WEATHER_PORT,
        log_level="info",
    )
