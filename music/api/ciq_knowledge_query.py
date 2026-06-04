import json
import logging
import os
import re
from pathlib import Path

import requests
from api._music_data import CIQ_QUERY_SLOTS, ciq_query_for_slot, slot_to_path_suffix
from flask import render_template, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_ciq_knowledge_query", description="ContX IQ Knowledge Query")
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


api_ciq_knowledge_query = APIBlueprint(
    "api_ciq_knowledge_query",
    __name__,
    url_prefix="/api_ciq_knowledge_query",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)


def _default_for_slot(slot: str) -> dict:
    spec = ciq_query_for_slot(slot)
    return {
        "slot": slot,
        "project_id": os.getenv("PROJECT_ID", ""),
        "description": spec.get("description", ""),
        "display_name": spec.get("display_name", ""),
        "name": spec.get("name", ""),
        "policy_id": os.getenv(f"CIQ_POLICY_ID_{spec['policy_slot']}", ""),
        "query": json.dumps(spec["query"]),
        "status": spec.get("status", "ACTIVE"),
    }


def _make_show_view(slot: str):
    def view():
        return render_template("ciq_knowledge_query/create_form.html", default_data=_default_for_slot(slot))

    view.__name__ = "show_create_form" if slot == "1" else f"show_create_form_{slot}"
    view.__doc__ = f"Music CIQ Knowledge Query slot {slot} - {ciq_query_for_slot(slot).get('display_name', '')}."
    return view


for _slot in CIQ_QUERY_SLOTS:
    api_ciq_knowledge_query.get(f"/create{slot_to_path_suffix(_slot)}", tags=[tag])(_make_show_view(_slot))


@api_ciq_knowledge_query.post("/create", tags=[tag])
def create_ciq_knowledge_query():
    """Create a new ciq knowledge query with the provided form data."""
    json_data = {
        "project_id": request.form.get("project_id", ""),
        "description": request.form.get("description", ""),
        "display_name": request.form.get("display_name", ""),
        "name": request.form.get("name", ""),
        "policy_id": request.form.get("policy_id", ""),
        "query": request.form.get("query", ""),
        "status": request.form.get("status", "ACTIVE"),
    }

    url_endpoints = os.getenv("URL_ENDPOINTS")
    sa_token = os.getenv("SA_TOKEN")

    api_url = f"{url_endpoints}/configs/v1/knowledge-queries"

    logger.info("Creating ContX IQ knowledge query at: %s", api_url)
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

    ciq_knowledge_query_id_saved = False
    ciq_knowledge_query_id = None

    slot = request.form.get("slot", "1")
    env_key = f"CIQ_QUERY_ID_{slot}"

    if HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES and isinstance(response_json, dict):
        ciq_knowledge_query_id = response_json.get("id") or response_json.get("ciq_knowledge_query_id")

        if ciq_knowledge_query_id:
            try:
                update_env_variable(env_key, ciq_knowledge_query_id)
                ciq_knowledge_query_id_saved = True
                logger.info("Saved %s: %s", env_key, ciq_knowledge_query_id)
            except Exception:
                logger.exception("Failed to save %s", env_key)

    return render_template(
        "ciq_knowledge_query/result.html",
        response_json=response_json,
        status_code=response.status_code,
        ciq_knowledge_query_id=ciq_knowledge_query_id,
        ciq_knowledge_query_id_saved=ciq_knowledge_query_id_saved,
        slot=slot,
        execute_path=f"/api_ciq_execute/execute{slot_to_path_suffix(slot)}",
    )
