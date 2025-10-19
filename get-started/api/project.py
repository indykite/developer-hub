import json
import logging
import os
import re
from pathlib import Path

import requests
from flask import render_template, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_project", description="Project")
security = [{"BearerToken": []}]

logger = logging.getLogger(__name__)

# HTTP status code constants
HTTP_OK = 200
HTTP_MULTIPLE_CHOICES = 300
HTTP_BAD_REQUEST = 400


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


def clean_env_file():
    """Remove all environment variables except SA_TOKEN and URL_ENDPOINTS from .env file."""
    env_file = Path(__file__).parent.parent / ".env"

    # Read existing .env file
    if not env_file.exists():
        logger.warning(".env file does not exist")
        return

    with env_file.open() as f:
        lines = f.readlines()

    # Keep only SA_TOKEN and URL_ENDPOINTS
    keep_vars = ["SA_TOKEN", "URL_ENDPOINTS"]
    updated_lines = []

    for line in lines:
        # Check if line starts with one of the variables we want to keep
        should_keep = False
        for var in keep_vars:
            if re.match(f"^{re.escape(var)}=", line):
                should_keep = True
                break

        if should_keep:
            updated_lines.append(line)

    # Write back to .env file
    with env_file.open("w") as f:
        f.writelines(updated_lines)

    # Clear the environment variables from the current process
    vars_to_clear = [
        "PROJECT_ID",
        "APPLICATION_ID",
        "APP_AGENT_ID",
        "APP_TOKEN",
        "TOKEN_INTROSPECT_ID",
        "AUTHORIZATION_POLICY_ID",
    ]
    for var in vars_to_clear:
        if var in os.environ:
            del os.environ[var]

    logger.info("Cleaned .env file, keeping only SA_TOKEN and URL_ENDPOINTS")


class Unauthorized(BaseModel):
    code: int = Field(-1, description="Status Code")
    message: str = Field("Unauthorized!", description="Exception Information")


api_project = APIBlueprint(
    "api_project",
    __name__,
    url_prefix="/api_project",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)


@api_project.get("/create", tags=[tag])
def show_create_form():
    """Display the project creation form with default values."""
    default_data = {
        "db_connection": {"name": "", "password": "", "url": "", "username": ""},
        "description": "",
        "display_name": "",
        "name": "",
        "organization_id": "",
        "region": "europe-west1",
    }
    return render_template("project/create_form.html", default_data=default_data)


@api_project.post("/create", tags=[tag])
def create_project():
    """Create a new project with the provided form data."""
    # Get form data
    json_data = {
        "name": request.form.get("name", ""),
        "display_name": request.form.get("display_name", ""),
        "description": request.form.get("description", ""),
        "organization_id": request.form.get("organization_id", ""),
        "region": request.form.get("region", "europe-west1"),
        "ikg_size": request.form.get("ikg_size", "2GB"),
    }

    # Only add db_connection if at least one field is provided
    db_name = request.form.get("db_name", "")
    db_url = request.form.get("db_url", "")
    db_username = request.form.get("db_username", "")
    db_password = request.form.get("db_password", "")

    if db_name or db_url or db_username or db_password:
        json_data["db_connection"] = {"name": db_name, "url": db_url, "username": db_username, "password": db_password}

    url_endpoints = os.getenv("URL_ENDPOINTS")
    sa_token = os.getenv("SA_TOKEN")

    api_url = f"{url_endpoints}/configs/v1/projects"

    logger.info("Creating project at: %s", api_url)
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

    # Extract and save project ID if the request was successful
    project_id_saved = False
    project_id = None

    if HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES and isinstance(response_json, dict):
        # Common field names for project ID
        project_id = (
            response_json.get("id")
            or response_json.get("project_id")
            or response_json.get("projectId")
            or response_json.get("gid")
        )

        if project_id:
            try:
                update_env_variable("PROJECT_ID", project_id)
                project_id_saved = True
                logger.info("Saved PROJECT_ID: %s", project_id)
            except Exception:
                logger.exception("Failed to save PROJECT_ID")

    return render_template(
        "project/result.html",
        response_json=response_json,
        status_code=response.status_code,
        project_id=project_id,
        project_id_saved=project_id_saved,
    )


@api_project.get("/delete", tags=[tag])
def show_delete_form():
    """Display the project deletion form."""
    # Get PROJECT_ID from environment to pre-fill the form
    project_id = os.getenv("PROJECT_ID", "")

    return render_template("project/delete_form.html", project_id=project_id)


@api_project.post("/delete", tags=[tag])
def delete_project():
    """Delete a project with the provided ID."""
    project_id = request.form.get("project_id", "").strip()

    if not project_id:
        return render_template(
            "project/delete_result.html",
            response_json={"message": "Project ID is required"},
            status_code=HTTP_BAD_REQUEST,
        )

    url_endpoints = os.getenv("URL_ENDPOINTS")
    sa_token = os.getenv("SA_TOKEN")

    api_url = f"{url_endpoints}/configs/v1/projects/{project_id}"

    logger.info("Deleting project at: %s", api_url)

    response = requests.delete(
        api_url,
        headers={
            "Authorization": f"Bearer {sa_token}",
        },
        timeout=30,
    )

    logger.info("Response status: %s", response.status_code)
    logger.debug("Response headers: %s", response.headers)
    logger.debug("Response text: %s", response.text)

    try:
        response_json = response.json()
    except ValueError:
        response_json = {
            "message": "Project deleted successfully" if response.status_code == HTTP_OK else "Invalid JSON response",
            "status": response.status_code,
            "response_text": response.text[:500] if response.text else "No response body",
        }

    # Clean .env file if deletion was successful
    env_cleaned = False
    if HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES:
        try:
            clean_env_file()
            env_cleaned = True
            logger.info("Environment variables cleaned from .env file")
        except Exception:
            logger.exception("Failed to clean .env file")

    return render_template(
        "project/delete_result.html",
        response_json=response_json,
        status_code=response.status_code,
        project_id=project_id,
        env_cleaned=env_cleaned,
    )
