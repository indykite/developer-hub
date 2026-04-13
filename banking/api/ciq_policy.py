import json
import logging
import os
import re
from pathlib import Path

import requests
from flask import render_template, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_ciq_policy", description="ContX IQ Policy")
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


api_ciq_policy = APIBlueprint(
    "api_ciq_policy",
    __name__,
    url_prefix="/api_ciq_policy",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)


def _build_default(  # noqa: PLR0913
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


# Policy 1: Account Statement Access — caller can view their own FinAccount.
POLICY_1 = {
    "meta": {"policy_version": "1.0-ciq"},
    "subject": {"type": "_Application"},
    "condition": {
        "cypher": (
            "MATCH (subject:_Application {external_id: $_appId}), "
            "(caller:User {external_id: $caller_id}) "
            "MATCH (caller)-[r_owns:OWNS]->(account:FinAccount)"
        ),
    },
    "allowed_reads": {
        "nodes": [
            "caller.external_id",
            "caller.property.name",
            "caller.property.email",
            "account.external_id",
            "account.property.acctNb",
            "account.property.account_type",
            "account.property.balance",
            "account.property.since",
        ],
        "relationships": ["r_owns"],
    },
}

# Policy 2: Recent Transactions Lookup — caller can read transactions on an account they own.
POLICY_2 = {
    "meta": {"policy_version": "1.0-ciq"},
    "subject": {"type": "_Application"},
    "condition": {
        "cypher": (
            "MATCH (subject:_Application {external_id: $_appId}), "
            "(caller:User {external_id: $caller_id}) "
            "MATCH (caller)-[r_owns:OWNS]->(account:FinAccount {external_id: $account_external_id}) "
            "MATCH (account)-[r_tx:HAS]->(tx:Transaction)"
        ),
    },
    "allowed_reads": {
        "nodes": [
            "caller.external_id",
            "account.external_id",
            "account.property.acctNb",
            "tx.external_id",
            "tx.property.date",
            "tx.property.amount",
            "tx.property.source",
        ],
        "relationships": ["r_owns", "r_tx"],
    },
}

# Policy 3: Branches used by an Organization's members — list branches where members of a given
# Organization work. Uses (:User)-[:MEMBER_OF]->(:Organization) and (:User)-[:WORKS_FOR]->(:Branch).
POLICY_3 = {
    "meta": {"policy_version": "1.0-ciq"},
    "subject": {"type": "_Application"},
    "condition": {
        "cypher": (
            "MATCH (subject:_Application {external_id: $_appId}), "
            "(org:Organization {external_id: $org_id}) "
            "MATCH (user:User)-[r_member:MEMBER_OF]->(org) "
            "MATCH (user)-[r_works:WORKS_FOR]->(branch:Branch)"
        ),
    },
    "allowed_reads": {
        "nodes": [
            "org.external_id",
            "org.property.name",
            "user.external_id",
            "user.property.name",
            "branch.external_id",
            "branch.property.BranchCode",
            "branch.property.name",
            "branch.property.address",
        ],
        "relationships": ["r_member", "r_works"],
    },
}


@api_ciq_policy.get("/create", tags=[tag])
def show_create_form():
    """Banking CIQ Policy 1 - Account Statement Access."""
    project_id = os.getenv("PROJECT_ID", "")
    default_data = _build_default(
        "1",
        project_id,
        name="banking-account-statement-access",
        display_name="Banking - Account Statement Access",
        description="Allow a banking caller to read their own FinAccount details.",
        policy=POLICY_1,
        tags=["banking", "account", "owner"],
    )
    return render_template("ciq_policy/create_form.html", default_data=default_data)


@api_ciq_policy.get("/create2", tags=[tag])
def show_create_form_2():
    """Banking CIQ Policy 2 - Recent Transactions Lookup."""
    project_id = os.getenv("PROJECT_ID", "")
    default_data = _build_default(
        "2",
        project_id,
        name="banking-recent-transactions-lookup",
        display_name="Banking - Recent Transactions Lookup",
        description="Allow a banking caller to read transactions on a FinAccount they own.",
        policy=POLICY_2,
        tags=["banking", "transactions", "owner"],
    )
    return render_template("ciq_policy/create_form.html", default_data=default_data)


@api_ciq_policy.get("/create3", tags=[tag])
def show_create_form_3():
    """Banking CIQ Policy 3 - Branches used by an Organization's members."""
    project_id = os.getenv("PROJECT_ID", "")
    default_data = _build_default(
        "3",
        project_id,
        name="banking-org-member-branches",
        display_name="Banking - Org Member Branches",
        description="List branches where users that are MEMBER_OF a given Organization work.",
        policy=POLICY_3,
        tags=["banking", "branch", "organization", "member"],
    )
    return render_template("ciq_policy/create_form.html", default_data=default_data)


@api_ciq_policy.post("/create", tags=[tag])
def create_ciq_policy():
    """Create a new ciq policy with the provided form data."""
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

    # Extract and save ciq policy ID if the request was successful
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
