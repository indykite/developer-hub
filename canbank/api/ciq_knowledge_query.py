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
        "subject.external_id",
        "subject.property.name",
        "subject.property.email",
        "subject.property.title",
        "department.property.name",
        "manager.property.name",
    ],
    "relationships": [],
    "aggregate_values": ["agent"],
}

QUERY_2 = {
    "nodes": [
        "subject.external_id",
        "subject.property.name",
        "subject.property.title",
        "department.property.name",
        "quote.price",
    ],
    "relationships": [],
    "aggregate_values": [],
}

QUERY_3 = {
    "nodes": [
        "subject.external_id",
        "subject.property.name",
        "subject.property.title",
        "customer.property.name",
        "account.property.account_type",
        "tier.property.name",
        "tier.property.threshold_amount",
    ],
    "relationships": [],
    "aggregate_values": [],
}

QUERY_4 = {
    "nodes": [
        "subject.external_id",
        "subject.property.name",
        "subject.property.title",
        "document.property.name",
        "document.property.description",
        "document.property.url",
    ],
    "relationships": [],
    "aggregate_values": [],
}

QUERY_5 = {
    "nodes": [
        "doc.external_id",
        "doc.property.name",
        "ra.property.name",
        "ic.property.name",
    ],
    "relationships": [],
    "aggregate_values": [],
}

QUERY_6 = {
    "nodes": [
        "ra.external_id",
        "ra.property.name",
        "ra.property.description",
    ],
    "relationships": [],
    "aggregate_values": [],
}

QUERY_7 = {
    "nodes": [
        "subject.external_id",
        "subject.property.name",
        "document.property.name",
        "decision.property.name",
        "decision.property.description",
        "ticket.property.name",
        "ticket.property.description",
        "account.property.account_type",
        "customer.property.name",
    ],
    "relationships": [],
    "aggregate_values": [],
}


_QUERY_DEFS = [
    {
        "slot": "1",
        "name": "canbank-get-self-query",
        "display_name": "CanBank - Get Self Query",
        "description": (
            "Retrieve the authenticated user's profile, department and manager. "
            "Call tool 'ciq_execute' with no input_params. "
            'Example: { "id": "canbank-get-self", "input_params": { } }.'
        ),
        "query": QUERY_1,
    },
    {
        "slot": "2",
        "name": "canbank-get-stock-quote-query",
        "display_name": "CanBank - Get Stock Quote Query",
        "description": (
            "Retrieve a stock quote for a ticker when the caller's department is allowed to "
            "access it. Call tool 'ciq_execute' with a ticker. "
            'Example: { "id": "canbank-get-stock-quote", "input_params": { "ticker": "NVDA" } }.'
        ),
        "query": QUERY_2,
    },
    {
        "slot": "3",
        "name": "canbank-get-stock-trade-threshold-query",
        "display_name": "CanBank - Get Stock Trade Threshold Query",
        "description": (
            "Retrieve a customer's stock account tier and threshold. Caller must be in the "
            'trading department. Example: { "id": "canbank-get-stock-trade-threshold", '
            '"input_params": { "customer_external_id": "ted" } }.'
        ),
        "query": QUERY_3,
    },
    {
        "slot": "4",
        "name": "canbank-get-internal-documents-query",
        "display_name": "CanBank - Get Internal Documents Query",
        "description": (
            "Retrieve internal documents visible to the caller filtered by taxonomy. "
            'Example: { "id": "canbank-get-internal-documents", '
            '"input_params": { "taxonomy_external_id": "policy" } }.'
        ),
        "query": QUERY_4,
    },
    {
        "slot": "5",
        "name": "canbank-get-customer-facing-documents-query",
        "display_name": "CanBank - Get Customer Facing Documents Query",
        "description": (
            "Retrieve customer facing documents with their regulatory agreements and investment "
            'categories. Example: { "id": "canbank-get-customer-facing-documents", '
            '"input_params": { } }.'
        ),
        "query": QUERY_5,
    },
    {
        "slot": "6",
        "name": "canbank-get-regulatory-agreements-query",
        "display_name": "CanBank - Get Regulatory Agreements Query",
        "description": (
            "Retrieve the full list of regulatory agreements. "
            'Example: { "id": "canbank-get-regulatory-agreements", "input_params": { } }.'
        ),
        "query": QUERY_6,
    },
    {
        "slot": "7",
        "name": "canbank-get-decisions-query",
        "display_name": "CanBank - Get Decisions Query",
        "description": (
            "Retrieve decisions that referenced a given document, along with the ticket, "
            'customer and account involved. Example: { "id": "canbank-get-decisions", '
            '"input_params": { "document_external_id": "refund_policy" } }.'
        ),
        "query": QUERY_7,
    },
]


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


def _default_for_slot(slot: str) -> dict:
    spec = next((q for q in _QUERY_DEFS if q["slot"] == slot), None)
    if spec is None:
        msg = f"Unknown CIQ knowledge-query slot: {slot!r}"
        raise ValueError(msg)
    return _build_default(
        spec["slot"],
        name=spec["name"],
        display_name=spec["display_name"],
        description=spec["description"],
        query=spec["query"],
    )


@api_ciq_knowledge_query.get("/create", tags=[tag])
def show_create_form():
    """CanBank CIQ Knowledge Query 1 - Get Self."""
    return render_template("ciq_knowledge_query/create_form.html", default_data=_default_for_slot("1"))


@api_ciq_knowledge_query.get("/create2", tags=[tag])
def show_create_form_2():
    """CanBank CIQ Knowledge Query 2 - Get Stock Quote."""
    return render_template("ciq_knowledge_query/create_form.html", default_data=_default_for_slot("2"))


@api_ciq_knowledge_query.get("/create3", tags=[tag])
def show_create_form_3():
    """CanBank CIQ Knowledge Query 3 - Get Stock Trade Threshold."""
    return render_template("ciq_knowledge_query/create_form.html", default_data=_default_for_slot("3"))


@api_ciq_knowledge_query.get("/create4", tags=[tag])
def show_create_form_4():
    """CanBank CIQ Knowledge Query 4 - Get Internal Documents."""
    return render_template("ciq_knowledge_query/create_form.html", default_data=_default_for_slot("4"))


@api_ciq_knowledge_query.get("/create5", tags=[tag])
def show_create_form_5():
    """CanBank CIQ Knowledge Query 5 - Get Customer Facing Documents."""
    return render_template("ciq_knowledge_query/create_form.html", default_data=_default_for_slot("5"))


@api_ciq_knowledge_query.get("/create6", tags=[tag])
def show_create_form_6():
    """CanBank CIQ Knowledge Query 6 - Get Regulatory Agreements."""
    return render_template("ciq_knowledge_query/create_form.html", default_data=_default_for_slot("6"))


@api_ciq_knowledge_query.get("/create7", tags=[tag])
def show_create_form_7():
    """CanBank CIQ Knowledge Query 7 - Get Decisions."""
    return render_template("ciq_knowledge_query/create_form.html", default_data=_default_for_slot("7"))


@api_ciq_knowledge_query.post("/create", tags=[tag])
def create_ciq_knowledge_query():
    """Create a new ciq knowledge query with the provided form data."""
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
