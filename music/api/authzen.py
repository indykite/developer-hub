import json
import logging
import os

import requests
from api._music_data import EVALUATION_SLOTS, EVALUATIONS, evaluation_for_slot, slot_to_path_suffix
from flask import render_template, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_authzen", description="AuthZen Evaluation")
security = [{"ApiKeyAuth": []}]

logger = logging.getLogger(__name__)


class Unauthorized(BaseModel):
    code: int = Field(-1, description="Status Code")
    message: str = Field("Unauthorized!", description="Exception Information")


api_authzen = APIBlueprint(
    "api_authzen",
    __name__,
    url_prefix="/api_authzen",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)


# Evaluation 2 in the postman is the bulk evaluations call (different endpoint).
_BULK_EVAL_NAMES = {"Evaluations"}


def _default_for_slot(slot: str) -> dict:
    spec = evaluation_for_slot(slot)
    return {
        "slot": slot,
        "name": spec.get("name", "Evaluation"),
        "authzen_json": json.dumps(spec["body"], indent=2),
    }


def _make_show_view(slot: str):
    def view():
        return render_template(
            "authzen/evaluate_form.html",
            default_data=_default_for_slot(slot),
        )

    view.__name__ = "show_evaluate_form" if slot == "1" else f"show_evaluate_form_{slot}"
    view.__doc__ = f"Music AuthZen evaluation slot {slot} - {evaluation_for_slot(slot).get('name', '')}."
    return view


for _slot in EVALUATION_SLOTS:
    api_authzen.get(f"/evaluate{slot_to_path_suffix(_slot)}", tags=[tag])(_make_show_view(_slot))


@api_authzen.post("/evaluate", tags=[tag])
def evaluate_authzen():
    """Evaluate authorization with the provided form data."""
    try:
        authzen_json = request.form.get("authzen_data", "{}")
        json_data = json.loads(authzen_json)
    except json.JSONDecodeError as e:
        logger.exception("Failed to parse authzen JSON")
        return render_template(
            "authzen/result.html",
            response_json={"message": f"Invalid JSON: {e!s}"},
            status_code=400,
        )

    url_endpoints = os.getenv("URL_ENDPOINTS")
    app_token = os.getenv("APP_TOKEN")

    # The bulk endpoint is /access/v1/evaluations (note the trailing s).
    name = request.form.get("name", "")
    path = "evaluations" if name in _BULK_EVAL_NAMES or "evaluations" in json_data else "evaluation"
    api_url = f"{url_endpoints}/access/v1/{path}"

    logger.info("Evaluating authorization at: %s", api_url)
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

    return render_template("authzen/result.html", response_json=response_json, status_code=response.status_code)


# Expose the evaluation list for the index template.
EVALUATION_DEFAULTS = EVALUATIONS
