# Copyright (c) 2026 IndyKite
"""Event sink configuration (``/configs/v1/event-sinks``) - Outbound Events.

A project holds at most one sink. Only events the sink's destination accepted
are added to the signed audit chain, so the sink must exist (and be healthy)
before the events the chain should show. The provider secret (the Pub/Sub
service-account key) is attached server-side from PUBSUB_CREDENTIALS_FILE and is
never rendered in the form. See the public Outbound Events guide.
"""

import json
import logging
import os

import requests
from api import _dataset
from api.project import update_env_variable
from flask import jsonify, render_template, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_event_sink", description="Event Sink (Outbound Events)")
security = [{"BearerToken": []}]

logger = logging.getLogger(__name__)

HTTP_OK = 200
HTTP_MULTIPLE_CHOICES = 300
HTTP_BAD_REQUEST = 400


class Unauthorized(BaseModel):
    code: int = Field(-1, description="Status Code")
    message: str = Field("Unauthorized!", description="Exception Information")


api_event_sink = APIBlueprint(
    "api_event_sink",
    __name__,
    url_prefix="/api_event_sink",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)


_SECRET_FIELDS = {"credentials_json", "password", "access_key", "connection_string"}


def _redact_secrets(value):
    """Blank provider secrets in a response before it is rendered."""
    if isinstance(value, dict):
        return {k: ("***" if k in _SECRET_FIELDS and v else _redact_secrets(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_secrets(v) for v in value]
    return value


def _check_sink_shape(providers, routes):
    """Raise TypeError unless the edited JSON has the shape secrets are attached to.

    providers must be an object of provider objects (a ``pubsub`` setting, when
    present, an object too) and routes an array.
    """
    if not isinstance(providers, dict) or not isinstance(routes, list):
        msg = "providers must be a JSON object and routes a JSON array"
        raise TypeError(msg)
    for provider_id, provider in providers.items():
        if not isinstance(provider, dict) or ("pubsub" in provider and not isinstance(provider["pubsub"], dict)):
            msg = f"provider {provider_id!r} and its pubsub settings must be JSON objects"
            raise TypeError(msg)


def read_sink_health():
    """Read the saved sink and report per-provider delivery errors.

    Returns ``{"exists", "id", "errors", "message"}``. The platform checks each
    provider after the sink is saved and records a failure as the provider's
    read-only ``last_error``; no ``last_error`` means none was reported (yet).
    """
    sink_id = os.getenv("EVENT_SINK_ID", "")
    if not sink_id:
        return {"exists": False, "id": "", "errors": [], "message": "EVENT_SINK_ID is not set"}
    try:
        response = requests.get(
            f"{os.getenv('URL_ENDPOINTS')}/configs/v1/event-sinks/{sink_id}",
            headers={"Authorization": f"Bearer {os.getenv('SA_TOKEN')}"},
            timeout=30,
        )
    except requests.RequestException as e:
        return {"exists": False, "id": sink_id, "errors": [], "message": str(e)[:200]}
    if response.status_code >= HTTP_BAD_REQUEST:
        return {
            "exists": False,
            "id": sink_id,
            "errors": [],
            "message": f"event sink read failed with status {response.status_code}",
        }
    try:
        sink = response.json()
    except ValueError:
        return {"exists": False, "id": sink_id, "errors": [], "message": "invalid JSON from event sink read"}
    errors = []
    for provider_id, provider in (sink.get("providers") or {}).items():
        for kind, settings in provider.items():
            if isinstance(settings, dict) and settings.get("last_error"):
                errors.append({"provider": provider_id, "kind": kind, "last_error": settings["last_error"]})
    return {"exists": True, "id": sink_id, "errors": errors, "message": ""}


@api_event_sink.get("/create", tags=[tag])
def show_create_form():
    """Display the event-sink creation form with default values."""
    return render_template(
        "event_sink/create_form.html",
        default_data=_dataset.event_sink_form_default(os.getenv("PROJECT_ID", "")),
        key_file=os.getenv("PUBSUB_CREDENTIALS_FILE", ""),
    )


@api_event_sink.post("/create", tags=[tag])
def create_event_sink():
    """Create the project's event sink; the provider secret is attached here."""
    try:
        providers = json.loads(request.form.get("providers", "{}"))
        routes = json.loads(request.form.get("routes", "[]"))
        _check_sink_shape(providers, routes)
    except (json.JSONDecodeError, TypeError) as e:
        return render_template(
            "event_sink/result.html",
            response_json={"message": f"Invalid JSON: {e!s}"},
            status_code=400,
            event_sink_id=None,
        )

    missing = _dataset.attach_event_sink_secrets(providers)
    if missing:
        return render_template(
            "event_sink/result.html",
            response_json={"message": "Provider secret missing", "details": missing},
            status_code=400,
            event_sink_id=None,
        )

    json_data = {
        "project_id": request.form.get("project_id", ""),
        "name": request.form.get("name", ""),
        "include_cdc_events": request.form.get("include_cdc_events") == "true",
        "providers": providers,
        "routes": routes,
    }
    for field in ("display_name", "description"):
        value = request.form.get(field, "").strip()
        if value:
            json_data[field] = value

    api_url = f"{os.getenv('URL_ENDPOINTS')}/configs/v1/event-sinks"
    logger.info("Creating event sink at: %s", api_url)

    response = requests.post(
        api_url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.getenv('SA_TOKEN')}",
        },
        json=json_data,
        timeout=30,
    )
    logger.info("Response status: %s", response.status_code)
    logger.debug("Response text: %s", response.text)

    try:
        response_json = response.json()
    except ValueError:
        response_json = {
            "message": "Invalid JSON response",
            "status": response.status_code,
            "response_text": response.text[:500] if response.text else "No response body",
        }

    event_sink_id = None
    if HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES and isinstance(response_json, dict):
        event_sink_id = response_json.get("id")
        if event_sink_id:
            update_env_variable("EVENT_SINK_ID", event_sink_id)

    return render_template(
        "event_sink/result.html",
        response_json=_redact_secrets(response_json),
        status_code=response.status_code,
        event_sink_id=event_sink_id,
    )


@api_event_sink.get("/status", tags=[tag])
def sink_status():
    """Report whether the saved sink exists and any provider delivery errors."""
    return jsonify(read_sink_health())
