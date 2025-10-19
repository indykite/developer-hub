import json
import logging
import os
import re
from pathlib import Path

import requests
from flask import render_template, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_token_introspect", description="Token Introspect")
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


api_token_introspect = APIBlueprint(
    "api_token_introspect",
    __name__,
    url_prefix="/api_token_introspect",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)


@api_token_introspect.get("/create", tags=[tag])
def show_create_form():
    """Display the token introspect creation form with default values."""
    # Get PROJECT_ID from environment to pre-fill the form
    project_id = os.getenv("PROJECT_ID", "")

    default_data = {
        "claims_mapping": {"email": {"selector": "email"}, "name": {"selector": "full_name"}},
        "description": "Token introspect description",
        "display_name": "Token introspect name",
        "ikg_node_type": "Person",
        "jwt_matcher": {"issuer": "https://example.com", "audience": "audience-id"},
        "name": "rest-token-introspect",
        "online_validation": {"cache_ttl": 600},
        "perform_upsert": True,
        "project_id": project_id,
    }
    return render_template("token_introspect/create_form.html", default_data=default_data)


@api_token_introspect.post("/create", tags=[tag])
def create_token_introspect():
    """Create a new token introspect with the provided form data."""
    # Get form data
    json_data = {
        "claims_mapping": json.loads(request.form.get("claims_mapping", "{}")),
        "description": request.form.get("description", ""),
        "display_name": request.form.get("display_name", ""),
        "ikg_node_type": request.form.get("ikg_node_type", "Person"),
        "jwt_matcher": json.loads(request.form.get("jwt_matcher", "{}")),
        "name": request.form.get("name", ""),
        "online_validation": json.loads(request.form.get("online_validation", "{}")),
        "perform_upsert": request.form.get("perform_upsert") == "true",
        "project_id": request.form.get("project_id", ""),
    }

    url_endpoints = os.getenv("URL_ENDPOINTS")
    sa_token = os.getenv("SA_TOKEN")

    api_url = f"{url_endpoints}/configs/v1/token-introspects"

    logger.info("Creating token introspect at: %s", api_url)
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

    # Extract and save token introspect ID if the request was successful
    token_introspect_id_saved = False
    token_introspect_id = None

    if HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES and isinstance(response_json, dict):
        # Try to extract token introspect ID from different possible locations in the response
        # Common field names for token introspect ID
        token_introspect_id = response_json.get("id") or response_json.get("token_introspect_id")

        if token_introspect_id:
            try:
                update_env_variable("TOKEN_INTROSPECT_ID", token_introspect_id)
                token_introspect_id_saved = True
                logger.info("Saved TOKEN_INTROSPECT_ID: %s", token_introspect_id)
            except Exception:
                logger.exception("Failed to save TOKEN_INTROSPECT_ID")

    return render_template(
        "token_introspect/result.html",
        response_json=response_json,
        status_code=response.status_code,
        token_introspect_id=token_introspect_id,
        token_introspect_id_saved=token_introspect_id_saved,
    )
