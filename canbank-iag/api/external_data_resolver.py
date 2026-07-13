import json
import logging
import os
import re
from pathlib import Path

import requests
from flask import render_template, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_external_data_resolver", description="External Data Resolver")
security = [{"BearerToken": []}]

logger = logging.getLogger(__name__)

HTTP_OK = 200
HTTP_MULTIPLE_CHOICES = 300


def update_env_variable(key, value):
    """Update or add an environment variable in the .env file."""
    env_file = Path(__file__).parent.parent / ".env"

    if env_file.exists():
        with env_file.open() as f:
            lines = f.readlines()
    else:
        lines = []

    key_found = False
    updated_lines = []

    for line in lines:
        if re.match(f"^{re.escape(key)}=", line):
            updated_lines.append(f"{key}={value}\n")
            key_found = True
        else:
            updated_lines.append(line)

    if not key_found:
        if updated_lines and not updated_lines[-1].endswith("\n"):
            updated_lines[-1] += "\n"
        updated_lines.append(f"{key}={value}\n")

    with env_file.open("w") as f:
        f.writelines(updated_lines)

    os.environ[key] = value

    logger.info("Updated %s in .env file", key)


class Unauthorized(BaseModel):
    code: int = Field(-1, description="Status Code")
    message: str = Field("Unauthorized!", description="Exception Information")


api_external_data_resolver = APIBlueprint(
    "api_external_data_resolver",
    __name__,
    url_prefix="/api_external_data_resolver",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)


# Resolver 1: Weather — fetches the current weather block from open-meteo for the
# latitude/longitude passed via the knowledge query's input_params, defaulting to
# CanBank's London headquarters when no input_params are provided. Returns the whole
# `.current` object so consumers can read temperature_2m, apparent_temperature,
# wind_speed_10m, weather_code and time in one call.
# Same public endpoint as the iag-demo weather_agent.
#
# Substitution syntax {$var || default} is enforced by pkg/extdataref/payload/url.go;
# variables come from input_params only — sibling node properties are not consulted.
RESOLVER_WEATHER = {
    "slot": "1",
    "name": "weather",
    "display_name": "Current Weather",
    "description": (
        "Fetches the current weather block (temperature, apparent temperature, wind "
        "speed and weather code) for the latitude/longitude passed via input_params. "
        "Defaults to CanBank's London HQ (51.5072, -0.1276) when input_params are "
        "absent. Use this as the `external_value` on a Weather node property. Pair "
        "with the `weather-units` resolver to also retrieve the unit labels."
    ),
    "url": (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude={$latitude || 51.5072}&longitude={$longitude || -0.1276}"
        "&current=temperature_2m,apparent_temperature,wind_speed_10m,weather_code"
        "&timezone=auto"
    ),
    "method": "GET",
    "headers": {},
    "request_payload": "",
    "request_content_type": "JSON",
    "response_content_type": "JSON",
    "response_selector": ".current",
}


# Resolver 2: Weather Units — returns the unit labels for the same weather call.
RESOLVER_WEATHER_UNITS = {
    "slot": "2",
    "name": "weather-units",
    "display_name": "Current Weather Units",
    "description": (
        "Fetches the unit labels (e.g. °C, km/h) for the current weather block at the "
        "latitude/longitude passed via input_params (defaults to London HQ)."
    ),
    "url": (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude={$latitude || 51.5072}&longitude={$longitude || -0.1276}"
        "&current=temperature_2m,apparent_temperature,wind_speed_10m,weather_code"
        "&timezone=auto"
    ),
    "method": "GET",
    "headers": {},
    "request_payload": "",
    "request_content_type": "JSON",
    "response_content_type": "JSON",
    "response_selector": ".current_units",
}


# Resolver 3: Stock Quote — fetches the current price for a ticker symbol from Yahoo
# Finance's public chart endpoint. The ticker is supplied via the query's input_params
# (`{"id": "get-stock-quote", "input_params": {"ticker": "NVDA"}}`) and substituted
# into the URL. Bound to the `price` property on the `stock_quote` Quote node, which
# is what the iag-demo retriever_agent reads through CIQ_QUERY_STOCK_PRICE.
RESOLVER_STOCK_QUOTE = {
    "slot": "3",
    "name": "stock-quote",
    "display_name": "Stock Quote",
    "description": (
        "Returns the current market price for the ticker symbol passed via the "
        "knowledge query's `ticker` input parameter. Bound to the price property of "
        "the stock_quote Quote node and consumed by the iag-demo retriever_agent's "
        "max-purchase-amount tool. Backed by Yahoo Finance's public chart endpoint; "
        "no API key required."
    ),
    "url": "https://query1.finance.yahoo.com/v8/finance/chart/{$ticker}?interval=1d",
    "method": "GET",
    "headers": {},
    "request_payload": "",
    "request_content_type": "JSON",
    "response_content_type": "JSON",
    "response_selector": ".chart.result[0].meta.regularMarketPrice",
}


_RESOLVER_DEFS = [RESOLVER_WEATHER, RESOLVER_WEATHER_UNITS, RESOLVER_STOCK_QUOTE]


def _build_default(spec: dict) -> dict:
    return {
        "slot": spec["slot"],
        "project_id": os.getenv("PROJECT_ID", ""),
        "name": spec["name"],
        "display_name": spec["display_name"],
        "description": spec["description"],
        "url": spec["url"],
        "method": spec["method"],
        "headers": json.dumps(spec["headers"]),
        "request_payload": spec["request_payload"],
        "request_content_type": spec["request_content_type"],
        "response_content_type": spec["response_content_type"],
        "response_selector": spec["response_selector"],
    }


def _default_for_slot(slot: str) -> dict:
    spec = next((r for r in _RESOLVER_DEFS if r["slot"] == slot), None)
    if spec is None:
        msg = f"Unknown external data resolver slot: {slot!r}"
        raise ValueError(msg)
    return _build_default(spec)


@api_external_data_resolver.get("/create", tags=[tag])
def show_create_form():
    """CanBank External Data Resolver 1 - Weather."""
    return render_template(
        "external_data_resolver/create_form.html",
        default_data=_default_for_slot("1"),
    )


@api_external_data_resolver.get("/create2", tags=[tag])
def show_create_form_2():
    """CanBank External Data Resolver 2 - Weather Units."""
    return render_template(
        "external_data_resolver/create_form.html",
        default_data=_default_for_slot("2"),
    )


@api_external_data_resolver.get("/create3", tags=[tag])
def show_create_form_3():
    """CanBank External Data Resolver 3 - Stock Quote."""
    return render_template(
        "external_data_resolver/create_form.html",
        default_data=_default_for_slot("3"),
    )


@api_external_data_resolver.post("/create", tags=[tag])
def create_external_data_resolver():
    """Create a new external data resolver with the provided form data."""
    headers_raw = request.form.get("headers", "{}").strip() or "{}"
    try:
        headers_value = json.loads(headers_raw)
    except json.JSONDecodeError:
        headers_value = {}

    json_data = {
        "project_id": request.form.get("project_id", ""),
        "name": request.form.get("name", ""),
        "display_name": request.form.get("display_name", ""),
        "description": request.form.get("description", ""),
        "url": request.form.get("url", ""),
        "method": request.form.get("method", "GET"),
        "headers": headers_value,
        "request_payload": request.form.get("request_payload", ""),
        "request_content_type": request.form.get("request_content_type", "JSON"),
        "response_content_type": request.form.get("response_content_type", "JSON"),
        "response_selector": request.form.get("response_selector", ""),
    }

    url_endpoints = os.getenv("URL_ENDPOINTS")
    sa_token = os.getenv("SA_TOKEN")

    api_url = f"{url_endpoints}/configs/v1/external-data-resolvers"

    logger.info("Creating external data resolver at: %s", api_url)
    logger.debug("Request payload: %s", json.dumps(json_data, indent=2))

    response = requests.post(
        api_url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {sa_token}",
        },
        json=json_data,
        timeout=30,
    )

    logger.info("Response status: %s", response.status_code)
    logger.debug("Response headers: %s", response.headers)
    logger.debug("Response text: %s", response.text)

    try:
        response_json = response.json()
    except ValueError:
        response_json = {
            "message": "Invalid JSON response",
            "status": response.status_code,
            "response_text": response.text[:500] if response.text else "No response body",
        }

    resolver_id_saved = False
    resolver_id = None

    slot = request.form.get("slot", "1")
    env_key = f"EXTERNAL_DATA_RESOLVER_ID_{slot}"

    if HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES and isinstance(response_json, dict):
        resolver_id = response_json.get("id") or response_json.get("external_data_resolver_id")

        if resolver_id:
            try:
                update_env_variable(env_key, resolver_id)
                resolver_id_saved = True
                logger.info("Saved %s: %s", env_key, resolver_id)
            except Exception:
                logger.exception("Failed to save %s", env_key)

    return render_template(
        "external_data_resolver/result.html",
        response_json=response_json,
        status_code=response.status_code,
        resolver_id=resolver_id,
        resolver_id_saved=resolver_id_saved,
        resolver_name=json_data["name"],
    )
