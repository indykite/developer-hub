import json
import logging
import os
import re
from pathlib import Path

import requests
from flask import render_template, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_application", description="Application")
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


api_application = APIBlueprint(
    "api_application",
    __name__,
    url_prefix="/api_application",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)


@api_application.get("/create", tags=[tag])
def show_create_form():
    """Display the application creation form with default values."""
    # Get PROJECT_ID from environment to pre-fill the form
    project_id = os.getenv("PROJECT_ID", "")

    default_data = {"description": "", "name": "", "display_name": "", "project_id": project_id}
    return render_template("application/create_form.html", default_data=default_data)


@api_application.post("/create", tags=[tag])
def create_application():
    """Create a new application with the provided form data."""
    # Get form data
    json_data = {
        "description": request.form.get("description", ""),
        "name": request.form.get("name", ""),
        "display_name": request.form.get("display_name", ""),
        "project_id": request.form.get("project_id", ""),
    }

    url_endpoints = os.getenv("URL_ENDPOINTS")
    sa_token = os.getenv("SA_TOKEN")

    api_url = f"{url_endpoints}/configs/v1/applications"

    logger.info("Creating application at: %s", api_url)
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

    # Extract and save application ID if the request was successful
    application_id_saved = False
    application_id = None

    if HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES and isinstance(response_json, dict):
        # Common field names for application ID
        application_id = response_json.get("id")

        if application_id:
            try:
                update_env_variable("APPLICATION_ID", application_id)
                application_id_saved = True
                logger.info("Saved APPLICATION_ID: %s", application_id)
            except Exception:
                logger.exception("Failed to save APPLICATION_ID")

    return render_template(
        "application/result.html",
        response_json=response_json,
        status_code=response.status_code,
        application_id=application_id,
        application_id_saved=application_id_saved,
    )
