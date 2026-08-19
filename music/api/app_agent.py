# Copyright (c) 2026 IndyKite
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import requests
from api._env import remove_env_variables, update_env_variable
from api._music_data import APP_AGENT_DEFAULTS
from flask import render_template, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_app_agent", description="Application Agent")
security = [{"BearerToken": []}]

logger = logging.getLogger(__name__)

# HTTP status code constants
HTTP_OK = 200
HTTP_MULTIPLE_CHOICES = 300
HTTP_CONFLICT = 409


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
    """Display the application agent creation form with default values from the manifest."""
    default_data = {**APP_AGENT_DEFAULTS, "application_id": os.getenv("APPLICATION_ID", "")}
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


def _lookup_agent_by_name(url_endpoints: str, sa_token: str, name: str) -> str | None:
    """Resolve an existing agent's id by its (unique) name.

    Used to recover from a duplicate-name create: a previous run may have
    created the agent but failed on the credentials step, and re-creating the
    same fixed name would otherwise dead-end on the conflict forever.
    """
    try:
        response = requests.get(
            f"{url_endpoints}/configs/v1/application-agents/{quote(name, safe='')}",
            params={"location": os.getenv("PROJECT_ID", "")},
            headers={"Authorization": f"Bearer {sa_token}"},
            timeout=30,
        )
    except requests.RequestException:
        logger.exception("Agent lookup by name failed")
        return None
    if not HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES:
        logger.warning("Agent lookup by name returned status %s", response.status_code)
        return None
    try:
        return response.json().get("id")
    except ValueError:
        return None


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
    elif response.status_code == HTTP_CONFLICT:
        # The fixed name already exists (e.g. a previous run created the agent
        # but its credentials step failed): reuse the existing agent and mint
        # fresh credentials instead of dead-ending on the duplicate name.
        app_agent_id = _lookup_agent_by_name(url_endpoints, sa_token, json_data["name"])
        if app_agent_id:
            logger.info("Agent name already exists; reusing %s and creating fresh credentials", app_agent_id)

    if app_agent_id:
        app_agent_id_saved = _save_app_agent_id(app_agent_id)
        credentials_response, credentials_created = _create_agent_credentials(
            url_endpoints,
            sa_token,
            app_agent_id,
            request.form.get("name", "agent"),
        )
        if not credentials_created:
            # Never leave a token that does not match the agent: a stale
            # APP_TOKEN would mask this failure as success on a re-run.
            remove_env_variables(["APP_TOKEN"])

    return render_template(
        "app_agent/result.html",
        response_json=response_json,
        status_code=response.status_code,
        app_agent_id=app_agent_id,
        app_agent_id_saved=app_agent_id_saved,
        credentials_response=credentials_response,
        credentials_created=credentials_created,
    )
