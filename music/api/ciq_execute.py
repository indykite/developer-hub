import json
import logging
import os
import time

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

HTTP_UNAUTHORIZED = 401
# Person-subject executes introspect the user's Bearer token against the project's
# Token Introspect config, which caches the issuer's JWKS.
# Right after an IdP signing-key rotation an instance may serve a stale keyset and
# reject an otherwise-valid token with 401; a retry usually lands on a refreshed cache.
# So we retry 401 a couple of times, but only for person-subject (Bearer) executes.
USER_TOKEN_RETRY_ATTEMPTS = 2
USER_TOKEN_RETRY_BACKOFF_SECONDS = 1.5

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
        data = _execute_default(slot)
        # Prefill the inputs with the params just submitted (passed by "Execute Another"),
        # falling back to the slot's defaults when absent.
        prefilled = request.args.get("input_params")
        if prefilled:
            data = {**data, "input_params": prefilled}
        return render_template("ciq_execute/ciq_execute_form.html", default_data=data)

    view.__name__ = "show_execute_form" if slot == "1" else f"show_execute_form_{slot}"
    view.__doc__ = f"Music CIQ Execute slot {slot}."
    return view


for _slot in CIQ_EXECUTE_SLOTS:
    api_ciq_execute.get(f"/execute{slot_to_path_suffix(_slot)}", tags=[tag])(_make_show_view(_slot))


@api_ciq_execute.post("/execute", tags=[tag])
def execution():
    """Execute contX IQ with the provided form data."""
    slot = request.form.get("slot", "1")
    input_params_str = request.form.get("input_params", "{}")
    try:
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
            slot=slot,
            input_params=input_params_str,
        )

    url_endpoints = os.getenv("URL_ENDPOINTS")
    app_token = os.getenv("APP_TOKEN")

    # Derive the policy slot from the manifest mapping (variant slots like "1b"/"2b"
    # have their own policy, so digit-stripping would be wrong). Mirrors chat.py.
    try:
        policy_slot = ciq_query_for_slot(slot)["policy_slot"]
    except ValueError:
        policy_slot = slot

    api_url = f"{url_endpoints}/contx-iq/v1/execute"
    logger.info("Executing ContX IQ at: %s (slot=%s, policy_slot=%s)", api_url, slot, policy_slot)
    logger.debug("Request payload: %s", json.dumps(json_data, indent=2))

    needs_user = policy_slot not in _APP_SUBJECT_POLICY_SLOTS
    headers = {
        "Content-Type": "application/json",
        "X-IK-ClientKey": app_token,
    }
    if needs_user:
        user_token = os.getenv("USER_TOKEN", "")
        if not user_token:
            # Fail fast: a person-subject slot needs a signed-in user. Sending an empty
            # Bearer would just 401 (and trigger the retries below), hiding the real cause.
            logger.error("USER_TOKEN not configured for person-subject slot %s", slot)
            return render_template(
                "ciq_execute/result.html",
                response_json={
                    "message": "USER_TOKEN not configured. Person-subject queries need a "
                    "signed-in user (introspect a token first).",
                },
                status_code=400,
                slot=slot,
                input_params=input_params_str,
            )
        headers["Authorization"] = f"Bearer {user_token}"
        # Never log any part/length of the token (it's a credential); only that one is set.
        logger.info("USER_TOKEN attached to Authorization header for person-subject slot %s", slot)

    # Retry transient 401s on person-subject executes (stale-JWKS-cache window after an
    # IdP signing-key rotation); app-subject 401s aren't transient, so don't retry them.
    max_attempts = USER_TOKEN_RETRY_ATTEMPTS + 1 if needs_user else 1
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(
                api_url,
                headers=headers,
                json=json_data,
                timeout=30,
            )
        except requests.RequestException as e:
            logger.exception("CIQ execute request failed")
            return render_template(
                "ciq_execute/result.html",
                response_json={"message": f"Request failed: {e!s}"},
                status_code=502,
                slot=slot,
                input_params=input_params_str,
            )
        if response.status_code == HTTP_UNAUTHORIZED and attempt < max_attempts:
            logger.warning(
                "CIQ execute slot %s got 401 on attempt %s/%s (likely a stale token-introspect JWKS cache); retrying",
                slot,
                attempt,
                max_attempts,
            )
            time.sleep(USER_TOKEN_RETRY_BACKOFF_SECONDS * attempt)
            continue
        break

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

    return render_template(
        "ciq_execute/result.html",
        response_json=response_json,
        status_code=response.status_code,
        slot=slot,
        input_params=input_params_str,
    )
