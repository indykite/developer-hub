import json
import logging
import os
import re
from pathlib import Path

import requests
from flask import render_template, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_ciq_knowledge_query", description="ContX IQ Knowledge Query")
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


api_ciq_knowledge_query = APIBlueprint(
    "api_ciq_knowledge_query",
    __name__,
    url_prefix="/api_ciq_knowledge_query",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)


@api_ciq_knowledge_query.get("/create", tags=[tag])
def show_create_form():
    """Display the ciq knowledge query creation form with default values."""
    # Get PROJECT_ID from environment to pre-fill the form
    project_id = os.getenv("PROJECT_ID", "")
    policy_id = os.getenv("CIQ_POLICY_ID", "")

    default_knowledge_query = (
        "{"
        ' "nodes": ['
        ' "paymentmethod.external_id"'
        " ],"
        '"filter": {'
        ' "attribute": "ln.property.number",'
        ' "operator": "=",'
        ' "value": "$license"'
        " }"
        "}"
    )
    default_data = {
        "project_id": project_id,
        "description": "description of knowledge query",
        "display_name": "ciq knowledge query name",
        "name": "ciq-knowledge-query-name",
        "policy_id": policy_id,
        "query": default_knowledge_query,
        "status": "ACTIVE",
    }
    return render_template("ciq_knowledge_query/create_form.html", default_data=default_data)


@api_ciq_knowledge_query.post("/create", tags=[tag])
def create_ciq_knowledge_query():
    """Create a new ciq knowledge query with the provided form data."""
    # Get form data
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

    # Extract and save ciq knowledge query ID if the request was successful
    ciq_knowledge_query_id_saved = False
    ciq_knowledge_query_id = None

    if HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES and isinstance(response_json, dict):
        # Try to extract ciq knowledge query ID from different possible locations in the response
        # Common field names for ciq knowledge query ID
        ciq_knowledge_query_id = response_json.get("id") or response_json.get("ciq_knowledge_query_id")

        if ciq_knowledge_query_id:
            try:
                update_env_variable("CIQ_KNOWLEDGE_QUERY_ID", ciq_knowledge_query_id)
                ciq_knowledge_query_id_saved = True
                logger.info("Saved CIQ_KNOWLEDGE_QUERY_ID: %s", ciq_knowledge_query_id)
            except Exception:
                logger.exception("Failed to save CIQ_KNOWLEDGE_QUERY_ID")

    return render_template(
        "ciq_knowledge_query/result.html",
        response_json=response_json,
        status_code=response.status_code,
        ciq_knowledge_query_id=ciq_knowledge_query_id,
        ciq_knowledge_query_id_saved=ciq_knowledge_query_id_saved,
    )
