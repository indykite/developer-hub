import json
import logging
import os
import re
from pathlib import Path

import requests
from flask import render_template, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_mcp_server", description="MCP Server")
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


api_mcp_server = APIBlueprint(
    "api_mcp_server",
    __name__,
    url_prefix="/api_mcp_server",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)


@api_mcp_server.get("/create", tags=[tag])
def show_create_form():
    """Display the MCP Server creation form with default values."""
    default_data = {
        "project_id": os.getenv("PROJECT_ID", ""),
        "name": "canbank-mcp-server",
        "display_name": "CanBank MCP Server",
        "description": (
            "MCP Server configuration for CanBank — binds the App Agent and Token "
            "Introspect used to authenticate inbound MCP traffic."
        ),
        "enabled": True,
        "app_agent_id": os.getenv("APP_AGENT_ID", ""),
        "token_introspect_id": os.getenv("TOKEN_INTROSPECT_ID", ""),
        "scopes_supported": ["name", "email"],
    }
    return render_template("mcp_server/create_form.html", default_data=default_data)


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()] if value else []


@api_mcp_server.post("/create", tags=[tag])
def create_mcp_server():
    """Create a new MCP Server configuration with the provided form data."""
    json_data = {
        "project_id": request.form.get("project_id", ""),
        "name": request.form.get("name", ""),
        "display_name": request.form.get("display_name", ""),
        "description": request.form.get("description", ""),
        "enabled": request.form.get("enabled") == "true",
        "app_agent_id": request.form.get("app_agent_id", ""),
        "token_introspect_id": request.form.get("token_introspect_id", ""),
        "scopes_supported": _split_csv(request.form.get("scopes_supported", "")),
    }

    # Drop empty optional string fields so the API doesn't reject empty values it dislikes.
    payload = {k: v for k, v in json_data.items() if v not in ("", [], None)}

    url_endpoints = os.getenv("URL_ENDPOINTS")
    sa_token = os.getenv("SA_TOKEN")

    api_url = f"{url_endpoints}/configs/v1/mcp-servers"

    logger.info("Creating MCP Server at: %s", api_url)
    logger.debug("Request payload: %s", json.dumps(payload, indent=2))

    response = requests.post(
        api_url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {sa_token}",
        },
        json=payload,
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

    mcp_server_id_saved = False
    mcp_server_id = None

    if HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES and isinstance(response_json, dict):
        mcp_server_id = response_json.get("id") or response_json.get("mcp_server_id")

        if mcp_server_id:
            try:
                update_env_variable("MCP_SERVER_ID", mcp_server_id)
                mcp_server_id_saved = True
                logger.info("Saved MCP_SERVER_ID: %s", mcp_server_id)
            except Exception:
                logger.exception("Failed to save MCP_SERVER_ID")

    return render_template(
        "mcp_server/result.html",
        response_json=response_json,
        status_code=response.status_code,
        mcp_server_id=mcp_server_id,
        mcp_server_id_saved=mcp_server_id_saved,
    )
