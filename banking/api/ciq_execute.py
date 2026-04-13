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


def _execute_default(slot: str, title: str, input_params: dict) -> dict:
    return {
        "knowledge_query_id": os.getenv(f"CIQ_QUERY_ID_{slot}", ""),
        "title": title,
        "input_params": json.dumps(input_params, indent=2),
    }


@api_ciq_execute.get("/execute", tags=[tag])
def show_execute_form():
    """Banking CIQ Execute 1 - Account Statement Access (caller_id=50)."""
    return render_template(
        "ciq_execute/ciq_execute_form.html",
        default_data=_execute_default(
            "1",
            title="Account Statement Access",
            input_params={"caller_id": "50"},
        ),
    )


@api_ciq_execute.get("/execute2", tags=[tag])
def show_execute_form_2():
    """Banking CIQ Execute 2 - Recent Transactions Lookup."""
    return render_template(
        "ciq_execute/ciq_execute_form.html",
        default_data=_execute_default(
            "2",
            title="Recent Transactions Lookup",
            input_params={"caller_id": "50", "account_external_id": "100050001"},
        ),
    )


@api_ciq_execute.get("/execute3", tags=[tag])
def show_execute_form_3():
    """Banking CIQ Execute 3 - Branches used by an Organization's members."""
    return render_template(
        "ciq_execute/ciq_execute_form.html",
        default_data=_execute_default(
            "3",
            title="Branches used by an Organization's members",
            input_params={"org_id": "CORP001"},
        ),
    )


@api_ciq_execute.post("/execute", tags=[tag])
def execution():
    """Execute contX IQ with the provided form data."""
    # Get form data
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

    api_url = f"{url_endpoints}/contx-iq/v1/execute"
    logger.info("Executing ContX IQ at: %s", api_url)
    logger.debug("Request payload: %s", json.dumps(json_data, indent=2))

    response = requests.post(
        api_url,
        headers={
            "Content-Type": "application/json",
            "X-IK-ClientKey": app_token,
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

    return render_template("ciq_execute/result.html", response_json=response_json, status_code=response.status_code)
