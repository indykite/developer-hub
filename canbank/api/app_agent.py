import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests
from flask import render_template, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_app_agent", description="Application Agent")
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

    # If key wasn't found, add it (ensuring previous last line ends with a newline)
    if not key_found:
        if updated_lines and not updated_lines[-1].endswith("\n"):
            updated_lines[-1] += "\n"
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


api_app_agent = APIBlueprint(
    "api_app_agent",
    __name__,
    url_prefix="/api_app_agent",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)


@api_app_agent.get("/create", tags=[tag])
def show_create_form():
    """Display the application agent creation form with default values."""
    # Get APPLICATION_ID from environment to pre-fill the form
    application_id = os.getenv("APPLICATION_ID", "")

    default_data = {
        "api_permissions": ["Authorization", "Capture", "ContXIQ", "EntityMatching", "IKGRead"],
        "application_id": application_id,
        "description": "App agent for the banking demo",
        "display_name": "Banking Agent",
        "name": "banking-agent",
    }
    return render_template("app_agent/create_form.html", default_data=default_data)


def _save_app_agent_id(app_agent_id: str) -> bool:
    """Persist APP_AGENT_ID to .env. Return True on success."""
    try:
        update_env_variable("APP_AGENT_ID", app_agent_id)
    except Exception:
        logger.exception("Failed to save APP_AGENT_ID")
        return False
    logger.info("Saved APP_AGENT_ID: %s", app_agent_id)
    return True


def _extract_app_token(credentials_response: dict) -> str | None:
    """Pull the app token out of a credentials response's application_agent_config field."""
    agent_config = credentials_response.get("application_agent_config")
    if not agent_config:
        logger.warning("application_agent_config not found in response")
        return None
    if isinstance(agent_config, dict):
        return agent_config.get("token")
    if isinstance(agent_config, str):
        return agent_config
    return None


def _create_agent_credentials(
    url_endpoints: str,
    sa_token: str,
    app_agent_id: str,
    agent_name: str,
) -> tuple[dict | None, bool]:
    """Create credentials for a newly created app agent and save APP_TOKEN. Return (response, created)."""
    logger.info("Creating credentials for the application agent...")
    expire_time = (datetime.now(UTC) + timedelta(days=180)).isoformat().replace("+00:00", "Z")
    credentials_data = {
        "application_agent_id": app_agent_id,
        "display_name": f"Credentials for {agent_name}",
        "expire_time": expire_time,
    }
    credentials_url = f"{url_endpoints}/configs/v1/application-agent-credentials"
    logger.info("Creating credentials at: %s", credentials_url)
    logger.debug("Credentials payload: %s", json.dumps(credentials_data, indent=2))

    creds_response = requests.post(
        credentials_url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {sa_token}",
        },
        json=credentials_data,
        timeout=30,
    )

    logger.info("Credentials response status: %s", creds_response.status_code)
    logger.debug("Credentials response: %s", creds_response.text)

    try:
        credentials_response = creds_response.json()
    except ValueError:
        return {
            "message": "Invalid JSON response from credentials endpoint",
            "status": creds_response.status_code,
            "response_text": creds_response.text[:500] if creds_response.text else "No response body",
        }, False

    status = creds_response.status_code
    if status < HTTP_OK or status >= HTTP_MULTIPLE_CHOICES:
        return credentials_response, False

    logger.info("Credentials created successfully")
    if not isinstance(credentials_response, dict):
        return credentials_response, True

    app_token = _extract_app_token(credentials_response)
    if app_token:
        try:
            update_env_variable("APP_TOKEN", app_token)
            logger.info("Saved APP_TOKEN to .env file (length: %s)", len(app_token))
        except Exception:
            logger.exception("Failed to save APP_TOKEN")
    else:
        logger.warning("No token found in credentials response")
        logger.debug("Full response: %s", json.dumps(credentials_response, indent=2))

    return credentials_response, True


@api_app_agent.post("/create", tags=[tag])
def create_app_agent():
    """Create a new application agent with the provided form data."""
    # Get form data
    # Handle api_permissions as a list (split by newlines or commas)
    api_permissions_raw = request.form.get("api_permissions", "")
    api_permissions = [p.strip() for p in api_permissions_raw.split("\n") if p.strip()]

    json_data = {
        "api_permissions": api_permissions,
        "application_id": request.form.get("application_id", ""),
        "description": request.form.get("description", ""),
        "display_name": request.form.get("display_name", ""),
        "name": request.form.get("name", ""),
    }

    url_endpoints = os.getenv("URL_ENDPOINTS")
    sa_token = os.getenv("SA_TOKEN")

    api_url = f"{url_endpoints}/configs/v1/application-agents"

    logger.info("Creating application agent at: %s", api_url)
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

    # Extract and save app agent ID if the request was successful
    app_agent_id_saved = False
    app_agent_id = None
    credentials_response = None
    credentials_created = False

    if HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES and isinstance(response_json, dict):
        app_agent_id = (
            response_json.get("id") or response_json.get("app_agent_id") or response_json.get("application_agent_id")
        )

        if app_agent_id:
            app_agent_id_saved = _save_app_agent_id(app_agent_id)
            credentials_response, credentials_created = _create_agent_credentials(
                url_endpoints,
                sa_token,
                app_agent_id,
                request.form.get("name", "agent"),
            )

    return render_template(
        "app_agent/result.html",
        response_json=response_json,
        status_code=response.status_code,
        app_agent_id=app_agent_id,
        app_agent_id_saved=app_agent_id_saved,
        credentials_response=credentials_response,
        credentials_created=credentials_created,
    )
