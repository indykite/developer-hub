import json
import logging
import os
import re
from pathlib import Path

import requests
from api._music_data import CIQ_POLICY_SLOTS, ciq_policy_for_slot, slot_to_path_suffix
from flask import render_template, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_ciq_policy", description="ContX IQ Policy")
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


api_ciq_policy = APIBlueprint(
    "api_ciq_policy",
    __name__,
    url_prefix="/api_ciq_policy",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)


def _default_for_slot(slot: str) -> dict:
    spec = ciq_policy_for_slot(slot)
    return {
        "slot": slot,
        "project_id": os.getenv("PROJECT_ID", ""),
        "description": spec.get("description", ""),
        "display_name": spec.get("display_name", ""),
        "name": spec.get("name", ""),
        "policy": json.dumps(spec["policy"]),
        "status": spec.get("status", "ACTIVE"),
        "tags": spec.get("tags") or [],
    }


def _make_show_view(slot: str):
    def view():
        return render_template("ciq_policy/create_form.html", default_data=_default_for_slot(slot))

    view.__name__ = "show_create_form" if slot == "1" else f"show_create_form_{slot}"
    view.__doc__ = f"Music CIQ Policy slot {slot} - {ciq_policy_for_slot(slot).get('display_name', '')}."
    return view


for _slot in CIQ_POLICY_SLOTS:
    api_ciq_policy.get(f"/create{slot_to_path_suffix(_slot)}", tags=[tag])(_make_show_view(_slot))


@api_ciq_policy.post("/create", tags=[tag])
def create_ciq_policy():
    """Create a new ciq policy with the provided form data."""
    json_data = {
        "project_id": request.form.get("project_id", ""),
        "description": request.form.get("description", ""),
        "display_name": request.form.get("display_name", ""),
        "name": request.form.get("name", ""),
        "policy": request.form.get("policy", ""),
        "status": request.form.get("status", "ACTIVE"),
        "tags": request.form.get("tags", "").split(",") if request.form.get("tags", "").strip() else [],
    }

    url_endpoints = os.getenv("URL_ENDPOINTS")
    sa_token = os.getenv("SA_TOKEN")

    api_url = f"{url_endpoints}/configs/v1/authorization-policies"

    logger.info("Creating ContX IQ policy at: %s", api_url)
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

    ciq_policy_id_saved = False
    ciq_policy_id = None

    slot = request.form.get("slot", "1")
    env_key = f"CIQ_POLICY_ID_{slot}"

    if HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES and isinstance(response_json, dict):
        ciq_policy_id = response_json.get("id") or response_json.get("ciq_policy_id")

        if ciq_policy_id:
            try:
                update_env_variable(env_key, ciq_policy_id)
                ciq_policy_id_saved = True
                logger.info("Saved %s: %s", env_key, ciq_policy_id)
            except Exception:
                logger.exception("Failed to save %s", env_key)

    return render_template(
        "ciq_policy/result.html",
        response_json=response_json,
        status_code=response.status_code,
        ciq_policy_id=ciq_policy_id,
        ciq_policy_id_saved=ciq_policy_id_saved,
    )
