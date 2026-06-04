import json
import logging
import os

import requests
from api._music_data import (
    CIQ_EXECUTE_SLOTS,
    CIQ_POLICIES,
    ciq_execute_for_slot,
    ciq_query_for_slot,
    slot_to_path_suffix,
)
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


def _app_subject_policy_slots() -> set[str]:
    """Slots whose underlying policy uses subject: _Application."""
    out = set()
    for pol in CIQ_POLICIES:
        subject = pol.get("policy", {}).get("subject", {})
        if subject.get("type") == "_Application":
            out.add(pol["slot"])
    return out


_APP_SUBJECT_POLICY_SLOTS = _app_subject_policy_slots()


def _execute_default(slot: str) -> dict:
    spec = ciq_execute_for_slot(slot)
    query = ciq_query_for_slot(slot)
    return {
        "slot": slot,
        "knowledge_query_id": os.getenv(f"CIQ_QUERY_ID_{slot}", ""),
        "title": query.get("display_name", ""),
        "name": query.get("name", ""),
        "input_params": json.dumps(spec.get("input_params", {}), indent=2),
    }


def _make_show_view(slot: str):
    def view():
        return render_template("ciq_execute/ciq_execute_form.html", default_data=_execute_default(slot))

    view.__name__ = "show_execute_form" if slot == "1" else f"show_execute_form_{slot}"
    view.__doc__ = f"Music CIQ Execute slot {slot}."
    return view


for _slot in CIQ_EXECUTE_SLOTS:
    api_ciq_execute.get(f"/execute{slot_to_path_suffix(_slot)}", tags=[tag])(_make_show_view(_slot))


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

    # Find the policy slot for this query slot (strip variant letter).
    policy_slot = "".join(ch for ch in slot if ch.isdigit())

    api_url = f"{url_endpoints}/contx-iq/v1/execute"
    logger.info("Executing ContX IQ at: %s (slot=%s, policy_slot=%s)", api_url, slot, policy_slot)
    logger.debug("Request payload: %s", json.dumps(json_data, indent=2))

    headers = {
        "Content-Type": "application/json",
        "X-IK-ClientKey": app_token,
    }
    if policy_slot not in _APP_SUBJECT_POLICY_SLOTS:
        user_token = os.getenv("USER_TOKEN", "")
        headers["Authorization"] = f"Bearer {user_token}"
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
