import json
import logging
import os

import requests
from flask import render_template, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_ciq_execute", description="ContX IQ Execution")
security = [{"ApiKeyAuth": []}]

logger = logging.getLogger(__name__)


class Unauthorized(BaseModel):
    code: int = Field(-1, description="Status Code")
    message: str = Field("Unauthorized!", description="Exception Information")


api_ciq_execute = APIBlueprint(
    "api_ciq_execute",
    __name__,
    url_prefix="/api_ciq_execute",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)


# Slots whose underlying policy uses subject: _Application and therefore does NOT need
# a user bearer token — the app's X-IK-ClientKey is sufficient.
_APP_SUBJECT_SLOTS = {"5", "6"}


_EXECUTE_DEFS = [
    {
        "slot": "1",
        "title": "Get Self",
        "input_params": {},
    },
    {
        "slot": "2",
        "title": "Get Stock Quote",
        "input_params": {"ticker": "NVDA"},
    },
    {
        "slot": "3",
        "title": "Get Stock Trade Threshold",
        "input_params": {"customer_external_id": "ted"},
    },
    {
        "slot": "4",
        "title": "Get Internal Documents",
        "input_params": {"taxonomy_external_id": "policy"},
    },
    {
        "slot": "5",
        "title": "Get Customer Facing Documents",
        "input_params": {},
    },
    {
        "slot": "6",
        "title": "Get Regulatory Agreements",
        "input_params": {},
    },
    {
        "slot": "7",
        "title": "Get Decisions",
        "input_params": {"document_external_id": "refund_policy"},
    },
]


def _execute_default(slot: str) -> dict:
    spec = next((e for e in _EXECUTE_DEFS if e["slot"] == slot), None)
    if spec is None:
        msg = f"Unknown CIQ execute slot: {slot!r}"
        raise ValueError(msg)
    return {
        "slot": slot,
        "knowledge_query_id": os.getenv(f"CIQ_QUERY_ID_{slot}", ""),
        "title": spec["title"],
        "input_params": json.dumps(spec["input_params"], indent=2),
    }


@api_ciq_execute.get("/execute", tags=[tag])
def show_execute_form():
    """CanBank CIQ Execute 1 - Get Self."""
    return render_template(
        "ciq_execute/ciq_execute_form.html",
        default_data=_execute_default("1"),
    )


@api_ciq_execute.get("/execute2", tags=[tag])
def show_execute_form_2():
    """CanBank CIQ Execute 2 - Get Stock Quote."""
    return render_template(
        "ciq_execute/ciq_execute_form.html",
        default_data=_execute_default("2"),
    )


@api_ciq_execute.get("/execute3", tags=[tag])
def show_execute_form_3():
    """CanBank CIQ Execute 3 - Get Stock Trade Threshold."""
    return render_template(
        "ciq_execute/ciq_execute_form.html",
        default_data=_execute_default("3"),
    )


@api_ciq_execute.get("/execute4", tags=[tag])
def show_execute_form_4():
    """CanBank CIQ Execute 4 - Get Internal Documents."""
    return render_template(
        "ciq_execute/ciq_execute_form.html",
        default_data=_execute_default("4"),
    )


@api_ciq_execute.get("/execute5", tags=[tag])
def show_execute_form_5():
    """CanBank CIQ Execute 5 - Get Customer Facing Documents."""
    return render_template(
        "ciq_execute/ciq_execute_form.html",
        default_data=_execute_default("5"),
    )


@api_ciq_execute.get("/execute6", tags=[tag])
def show_execute_form_6():
    """CanBank CIQ Execute 6 - Get Regulatory Agreements."""
    return render_template(
        "ciq_execute/ciq_execute_form.html",
        default_data=_execute_default("6"),
    )


@api_ciq_execute.get("/execute7", tags=[tag])
def show_execute_form_7():
    """CanBank CIQ Execute 7 - Get Decisions."""
    return render_template(
        "ciq_execute/ciq_execute_form.html",
        default_data=_execute_default("7"),
    )


@api_ciq_execute.post("/execute", tags=[tag])
def execution():
    """Execute contX IQ with the provided form data."""
    try:
        input_params_str = request.form.get("input_params", "{}")
        input_params = json.loads(input_params_str)
        json_data = {
            "id": request.form.get("knowledge_query_id", ""),
            "input_params": input_params,
        }
    except json.JSONDecodeError as e:
        logger.exception("Failed to parse input_params JSON")
        return render_template(
            "ciq_execute/result.html",
            response_json={"message": f"Invalid JSON in input_params: {e!s}"},
            status_code=400,
        )

    url_endpoints = os.getenv("URL_ENDPOINTS")
    app_token = os.getenv("APP_TOKEN")
    slot = request.form.get("slot", "1")

    api_url = f"{url_endpoints}/contx-iq/v1/execute"
    logger.info("Executing ContX IQ at: %s (slot=%s)", api_url, slot)
    logger.debug("Request payload: %s", json.dumps(json_data, indent=2))

    headers = {
        "Content-Type": "application/json",
        "X-IK-ClientKey": app_token,
    }
    if slot not in _APP_SUBJECT_SLOTS:
        headers["Authorization"] = f"Bearer {os.getenv('USER_TOKEN', '')}"

    response = requests.post(
        api_url,
        headers=headers,
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

    return render_template("ciq_execute/result.html", response_json=response_json, status_code=response.status_code)
