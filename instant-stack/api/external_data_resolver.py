# Copyright (c) 2026 IndyKite
import json
import logging
import os
import re
from pathlib import Path

import requests
from api import _dataset
from flask import abort, render_template, request
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


# The external data resolver definitions (weather, weather-units, stock-quote)
# now live in the dataset manifest: data/<DATASET>/manifest.json, loaded via
# api/_dataset.py. They were migrated out of this module during the
# config-as-data scaffold. provision.py still imports _RESOLVER_DEFS from here,
# so the name and shape are preserved (a list of resolver dicts with slot, name,
# display_name, description, url, method, headers{dict}, request_payload,
# request_content_type, response_content_type, response_selector).
_RESOLVER_DEFS = _dataset.RESOLVERS


def _build_default(spec: dict) -> dict:
    # Tolerate datasets whose resolver entries omit the optional fields.
    return {
        "slot": spec["slot"],
        "project_id": os.getenv("PROJECT_ID", ""),
        "name": spec.get("name", ""),
        "display_name": spec.get("display_name", ""),
        "description": spec.get("description", ""),
        "url": spec.get("url", ""),
        "method": spec.get("method", "GET"),
        "headers": json.dumps(spec.get("headers", {})),
        "request_payload": spec.get("request_payload", ""),
        "request_content_type": spec.get("request_content_type", ""),
        "response_content_type": spec.get("response_content_type", "application/json"),
        "response_selector": spec.get("response_selector", "."),
    }


def _default_for_slot(slot: str) -> dict:
    spec = next((r for r in _RESOLVER_DEFS if r["slot"] == slot), None)
    if spec is None:
        msg = f"Unknown external data resolver slot: {slot!r}"
        raise ValueError(msg)
    return _build_default(spec)


@api_external_data_resolver.get("/create", tags=[tag])
def show_create_form():
    """Render the resolver form for ?slot=N (defaults to the dataset's first slot)."""
    slots = [d["slot"] for d in _RESOLVER_DEFS]
    slot = request.args.get("slot") or (slots[0] if slots else "1")
    if slot not in slots:
        abort(404, description=f"Unknown slot {slot!r} for dataset (available: {slots})")
    return render_template(
        "external_data_resolver/create_form.html",
        default_data=_default_for_slot(slot),
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
