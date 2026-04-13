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


api_ciq_knowledge_query = APIBlueprint(
    "api_ciq_knowledge_query",
    __name__,
    url_prefix="/api_ciq_knowledge_query",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)


QUERY_1 = {
    "nodes": [
        "caller.external_id",
        "caller.property.name",
        "account.external_id",
        "account.property.acctNb",
        "account.property.account_type",
        "account.property.balance",
    ],
}

QUERY_2 = {
    "nodes": [
        "account.external_id",
        "account.property.acctNb",
        "tx.external_id",
        "tx.property.date",
        "tx.property.amount",
        "tx.property.source",
    ],
}

QUERY_3 = {
    "nodes": [
        "org.property.name",
        "user.external_id",
        "user.property.name",
        "branch.external_id",
        "branch.property.BranchCode",
        "branch.property.name",
        "branch.property.address",
    ],
}


def _build_default(slot: str, name: str, display_name: str, description: str, query: dict) -> dict:
    return {
        "slot": slot,
        "project_id": os.getenv("PROJECT_ID", ""),
        "description": description,
        "display_name": display_name,
        "name": name,
        "policy_id": os.getenv(f"CIQ_POLICY_ID_{slot}", ""),
        "query": json.dumps(query),
        "status": "ACTIVE",
    }


@api_ciq_knowledge_query.get("/create", tags=[tag])
def show_create_form():
    """Banking CIQ Knowledge Query 1 - Account Statement Access."""
    default_data = _build_default(
        "1",
        name="banking-account-statement-query",
        display_name="Banking - Account Statement Query",
        description="Return the caller's FinAccount details.",
        query=QUERY_1,
    )
    return render_template("ciq_knowledge_query/create_form.html", default_data=default_data)


@api_ciq_knowledge_query.get("/create2", tags=[tag])
def show_create_form_2():
    """Banking CIQ Knowledge Query 2 - Recent Transactions Lookup."""
    default_data = _build_default(
        "2",
        name="banking-recent-transactions-query",
        display_name="Banking - Recent Transactions Query",
        description="Return transactions on the given FinAccount.",
        query=QUERY_2,
    )
    return render_template("ciq_knowledge_query/create_form.html", default_data=default_data)


@api_ciq_knowledge_query.get("/create3", tags=[tag])
def show_create_form_3():
    """Banking CIQ Knowledge Query 3 - Branches used by an Organization's members."""
    default_data = _build_default(
        "3",
        name="banking-org-member-branches-query",
        display_name="Banking - Org Member Branches Query",
        description="Return branches where users MEMBER_OF the given Organization work.",
        query=QUERY_3,
    )
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

    slot = request.form.get("slot", "1")
    env_key = f"CIQ_QUERY_ID_{slot}"

    if HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES and isinstance(response_json, dict):
        ciq_knowledge_query_id = response_json.get("id") or response_json.get("ciq_knowledge_query_id")

        if ciq_knowledge_query_id:
            try:
                update_env_variable(env_key, ciq_knowledge_query_id)
                ciq_knowledge_query_id_saved = True
                logger.info("Saved %s: %s", env_key, ciq_knowledge_query_id)
            except Exception:
                logger.exception("Failed to save %s", env_key)

    return render_template(
        "ciq_knowledge_query/result.html",
        response_json=response_json,
        status_code=response.status_code,
        ciq_knowledge_query_id=ciq_knowledge_query_id,
        ciq_knowledge_query_id_saved=ciq_knowledge_query_id_saved,
    )
