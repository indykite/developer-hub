# Copyright (c) 2026 IndyKite
import json
import logging
import os
import re
from pathlib import Path

import requests
from api import _dataset
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


def _build_default(  # noqa: PLR0913, PLR0917
    slot: str,
    project_id: str,
    name: str,
    display_name: str,
    description: str,
    policy: dict,
    tags: list[str],
) -> dict:
    return {
        "slot": slot,
        "project_id": project_id,
        "description": description,
        "display_name": display_name,
        "name": name,
        "policy": json.dumps(policy),
        "status": "ACTIVE",
        "tags": tags,
    }


# The CIQ policy definitions (slot -> name / display_name / description / policy
# body / tags) now live in the dataset manifest: data/<DATASET>/manifest.json,
# loaded via api/_dataset.py. They were migrated out of this module during the
# config-as-data scaffold. provision.py still imports _POLICY_DEFS from here, so
# the name and shape are preserved (a list of
# {slot, name, display_name, description, policy{dict}, tags[]} entries).
_POLICY_DEFS = _dataset.CIQ_POLICIES


def _default_for_slot(slot: str) -> dict:
    project_id = os.getenv("PROJECT_ID", "")
    spec = next((p for p in _POLICY_DEFS if p["slot"] == slot), None)
    if spec is None:
        msg = f"Unknown CIQ policy slot: {slot!r}"
        raise ValueError(msg)
    return _build_default(
        spec["slot"],
        project_id,
        name=spec["name"],
        display_name=spec["display_name"],
        description=spec["description"],
        policy=spec["policy"],
        tags=spec["tags"],
    )


@api_ciq_policy.get("/create", tags=[tag])
def show_create_form():
    """CanBank CIQ Policy 1 - Get Self."""
    return render_template("ciq_policy/create_form.html", default_data=_default_for_slot("1"))


@api_ciq_policy.get("/create2", tags=[tag])
def show_create_form_2():
    """CanBank CIQ Policy 2 - Get Stock Quote."""
    return render_template("ciq_policy/create_form.html", default_data=_default_for_slot("2"))


@api_ciq_policy.get("/create3", tags=[tag])
def show_create_form_3():
    """CanBank CIQ Policy 3 - Get Stock Trade Threshold."""
    return render_template("ciq_policy/create_form.html", default_data=_default_for_slot("3"))


@api_ciq_policy.get("/create4", tags=[tag])
def show_create_form_4():
    """CanBank CIQ Policy 4 - Get Internal Documents."""
    return render_template("ciq_policy/create_form.html", default_data=_default_for_slot("4"))


@api_ciq_policy.get("/create5", tags=[tag])
def show_create_form_5():
    """CanBank CIQ Policy 5 - Get Customer Facing Documents."""
    return render_template("ciq_policy/create_form.html", default_data=_default_for_slot("5"))


@api_ciq_policy.get("/create6", tags=[tag])
def show_create_form_6():
    """CanBank CIQ Policy 6 - Get Regulatory Agreements."""
    return render_template("ciq_policy/create_form.html", default_data=_default_for_slot("6"))


@api_ciq_policy.get("/create7", tags=[tag])
def show_create_form_7():
    """CanBank CIQ Policy 7 - Get Decisions."""
    return render_template("ciq_policy/create_form.html", default_data=_default_for_slot("7"))


@api_ciq_policy.get("/create8", tags=[tag])
def show_create_form_8():
    """CanBank CIQ Policy 8 - Get Workflows."""
    return render_template("ciq_policy/create_form.html", default_data=_default_for_slot("8"))


@api_ciq_policy.get("/create9", tags=[tag])
def show_create_form_9():
    """CanBank CIQ Policy 9 - Get HQ Weather."""
    return render_template("ciq_policy/create_form.html", default_data=_default_for_slot("9"))


@api_ciq_policy.get("/create10", tags=[tag])
def show_create_form_10():
    """CanBank CIQ Policy 10 - Store Decision."""
    return render_template("ciq_policy/create_form.html", default_data=_default_for_slot("10"))


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
