import json
import logging
import os
import re

import requests
from api import _dataset
from flask import abort, render_template, request
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


# Slots whose underlying policy uses subject: _Application and therefore do NOT
# need a user bearer token - the app's X-IK-ClientKey is sufficient. Derived
# from the dataset manifest (the paired CIQ policy's subject type).
_APP_SUBJECT_SLOTS = {
    p["slot"] for p in _dataset.CIQ_POLICIES if p.get("policy", {}).get("subject", {}).get("type") == "_Application"
}


def _example_input_params(description: str) -> dict:
    """Extract the input_params of the `Example: {...}` JSON a query description carries.

    The dataset manifests document each knowledge query with an example call
    (e.g. `Example: { "id": "get-stock-quote", "input_params": { "ticker": "NVDA" } }`),
    so the execute form can be pre-filled for any dataset without hardcoding
    per-slot examples. Falls back to {} when no example is found.
    """
    m = re.search(r'"input_params"\s*:\s*(\{[^{}]*\})', description)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


# One execute definition per manifest knowledge query; examples come from the
# query descriptions so new datasets work without touching this module.
_EXECUTE_DEFS = [
    {
        "slot": q["slot"],
        "title": q.get("display_name", q.get("name", "")),
        "input_params": _example_input_params(q.get("description", "")),
    }
    for q in _dataset.CIQ_QUERIES
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
    """Render the execute form for ?slot=N (defaults to the dataset's first slot)."""
    slots = [d["slot"] for d in _EXECUTE_DEFS]
    slot = request.args.get("slot") or (slots[0] if slots else "1")
    if slot not in slots:
        abort(404, description=f"Unknown slot {slot!r} for dataset (available: {slots})")
    return render_template(
        "ciq_execute/ciq_execute_form.html",
        default_data=_execute_default(slot),
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
        user_token = os.getenv("USER_TOKEN", "")
        headers["Authorization"] = f"Bearer {user_token}"
        # Log a fingerprint (length + first/last 4 chars) so we can confirm the
        # process is shipping the token currently in .env - without leaking it.
        fingerprint = (
            f"len={len(user_token)} head={user_token[:4]!r} tail={user_token[-4:]!r}" if user_token else "<empty>"
        )
        logger.info("Authorization header USER_TOKEN: %s", fingerprint)

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
