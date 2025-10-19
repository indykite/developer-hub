import json
import logging
import os
import re
from pathlib import Path

import requests
from flask import render_template, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_authorization_policy", description="Authorization Policy")
security = [{"BearerToken": []}]

logger = logging.getLogger(__name__)

# HTTP status code constants
HTTP_OK = 200
HTTP_MULTIPLE_CHOICES = 300


def update_env_variable(key, value):
    """Update or add an environment variable in the .env file."""
    env_file = Path(__file__).parent.parent / ".env"

    # Read existing .env file or create empty content
    if env_file.exists():
        with env_file.open() as f:
            lines = f.readlines()
    else:
        lines = []

    # Check if the key exists and update it, or add it
    key_found = False
    updated_lines = []

    for line in lines:
        # Match lines like KEY=value or KEY="value"
        if re.match(f"^{re.escape(key)}=", line):
            updated_lines.append(f"{key}={value}\n")
            key_found = True
        else:
            updated_lines.append(line)

    # If key wasn't found, add it
    if not key_found:
        updated_lines.append(f"{key}={value}\n")

    # Write back to .env file
    with env_file.open("w") as f:
        f.writelines(updated_lines)

    # Update the environment variable in the current process
    os.environ[key] = value

    logger.info("Updated %s in .env file", key)


class Unauthorized(BaseModel):
    code: int = Field(-1, description="Status Code")
    message: str = Field("Unauthorized!", description="Exception Information")


api_authorization_policy = APIBlueprint(
    "api_authorization_policy",
    __name__,
    url_prefix="/api_authorization_policy",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)


@api_authorization_policy.get("/create", tags=[tag])
def show_create_form():
    """Display the authorization policy creation form with default values."""
    # Get PROJECT_ID from environment to pre-fill the form
    project_id = os.getenv("PROJECT_ID", "")

    default_policy = (
        '{"meta":{"policy_version":"2.0-kbac"},"subject":{"type":"Person"},"actions":["CAN_DRIVE"],'
        '"resource":{"type":"Car"},"condition":{"cypher":"MATCH (subject:Person)-[:DRIVES]->(resource:Car)"}}'
    )
    default_data = {
        "project_id": project_id,
        "description": "description of policy",
        "display_name": "policy name",
        "name": "policy-name",
        "policy": default_policy,
        "status": "ACTIVE",
    }
    return render_template("authorization_policy/create_form.html", default_data=default_data)


@api_authorization_policy.post("/create", tags=[tag])
def create_authorization_policy():
    """Create a new authorization policy with the provided form data."""
    # Get form data
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

    logger.info("Creating authorization policy at: %s", api_url)
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

    # Extract and save authorization policy ID if the request was successful
    authorization_policy_id_saved = False
    authorization_policy_id = None

    if HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES and isinstance(response_json, dict):
        # Try to extract authorization policy ID from different possible locations in the response
        # Common field names for authorization policy ID
        authorization_policy_id = response_json.get("id") or response_json.get("authorization_policy_id")

        if authorization_policy_id:
            try:
                update_env_variable("AUTHORIZATION_POLICY_ID", authorization_policy_id)
                authorization_policy_id_saved = True
                logger.info("Saved AUTHORIZATION_POLICY_ID: %s", authorization_policy_id)
            except Exception:
                logger.exception("Failed to save AUTHORIZATION_POLICY_ID")

    return render_template(
        "authorization_policy/result.html",
        response_json=response_json,
        status_code=response.status_code,
        authorization_policy_id=authorization_policy_id,
        authorization_policy_id_saved=authorization_policy_id_saved,
    )
