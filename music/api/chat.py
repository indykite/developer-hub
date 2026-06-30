"""Chat API — story-driven walkthrough of every music CIQ execute.

The story script lives in data/scenario.json. Each step references a CIQ
execute slot; the knowledge-query id is resolved from CIQ_QUERY_ID_<slot>
in .env, and the auth headers are derived from the slot's policy subject
(_Application → app token only, Person → app token + user bearer token).
"""

import json
import logging
import os
import time
from pathlib import Path

import requests
from api._music_data import CIQ_POLICIES, ciq_query_for_slot
from dotenv import load_dotenv
from flask import render_template, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_chat", description="Story-Driven Music Demo")
security = [{"ApiKeyAuth": []}]

HTTP_BAD_REQUEST = 400
HTTP_UNAUTHORIZED = 401
# Person-subject executes introspect the user's Bearer token against the project's
# Token Introspect config, which caches the issuer's JWKS.
# Right after an Auth0 signing-key rotation an instance may serve a stale keyset and
# reject an otherwise-valid token with 401; a retry usually lands on a refreshed cache.
# So we retry 401 a couple of times, but only for person-subject (Bearer) executes.
USER_TOKEN_RETRY_ATTEMPTS = 2
USER_TOKEN_RETRY_BACKOFF_SECONDS = 1.5
# `data[].nodes` keys look like "node.property.name"; keep at least node + prop.
MIN_KEY_PARTS = 2

logger = logging.getLogger(__name__)


class Unauthorized(BaseModel):
    code: int = Field(-1, description="Status Code")
    message: str = Field("Unauthorized!", description="Exception Information")


api_chat = APIBlueprint(
    "api_chat",
    __name__,
    url_prefix="/chat",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)

_ENV_FILE = Path(__file__).parent.parent / ".env"
_SCENARIO_PATH = Path(__file__).parent.parent / "data" / "scenario.json"

_APP_SUBJECT_POLICY_SLOTS = {
    pol["slot"] for pol in CIQ_POLICIES if pol.get("policy", {}).get("subject", {}).get("type") == "_Application"
}


def _load_scenario() -> dict:
    with _SCENARIO_PATH.open() as f:
        return json.load(f)


def _find_step(scenario: dict, scene_id: str, step_id: str) -> tuple[dict | None, dict | None, int]:
    scene = next((s for s in scenario["scenes"] if s["id"] == scene_id), None)
    if scene is None:
        return None, None, 0
    for i, step in enumerate(scene["steps"]):
        if step["id"] == step_id:
            return scene, step, i
    return scene, None, 0


def _needs_user_token(slot: str) -> bool:
    policy_slot = ciq_query_for_slot(slot)["policy_slot"]
    return policy_slot not in _APP_SUBJECT_POLICY_SLOTS


def execute_ciq_slot(slot: str, input_params: dict) -> dict:
    """Execute the knowledge query bound to a CIQ execute slot."""
    load_dotenv(_ENV_FILE, override=True)

    url_endpoints = os.getenv("URL_ENDPOINTS")
    app_token = os.getenv("APP_TOKEN")
    knowledge_query_id = os.getenv(f"CIQ_QUERY_ID_{slot}", "")

    if not app_token:
        return {
            "error": True,
            "message": "APP_TOKEN not configured. Please create an application agent first.",
        }

    if not knowledge_query_id:
        return {
            "error": True,
            "message": f"CIQ_QUERY_ID_{slot} not configured. "
            f"Please create the CIQ policy and knowledge query for slot {slot} first.",
        }

    needs_user = _needs_user_token(slot)
    headers = {
        "Content-Type": "application/json",
        "X-IK-ClientKey": app_token,
    }
    if needs_user:
        user_token = os.getenv("USER_TOKEN", "")
        if not user_token:
            return {
                "error": True,
                "message": "USER_TOKEN not configured. Person-subject queries need a signed-in user "
                "(introspect a token first).",
            }
        headers["Authorization"] = f"Bearer {user_token}"

    api_url = f"{url_endpoints}/contx-iq/v1/execute"
    json_data = {
        "id": knowledge_query_id,
        "input_params": input_params,
    }

    logger.info("Executing story step: slot=%s query=%s", slot, knowledge_query_id)
    logger.debug("Input params: %s", json.dumps(input_params, indent=2))

    # Retry transient 401s on person-subject executes (stale-JWKS-cache window after an
    # IdP signing-key rotation); app-subject 401s aren't transient, so don't retry them.
    max_attempts = USER_TOKEN_RETRY_ATTEMPTS + 1 if needs_user else 1
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(api_url, headers=headers, json=json_data, timeout=120)
        except requests.RequestException as e:
            logger.exception("Request failed")
            return {
                "error": True,
                "message": f"Request failed: {e!s}",
            }

        if response.status_code == HTTP_UNAUTHORIZED and attempt < max_attempts:
            logger.warning(
                "Slot %s execute got 401 on attempt %s/%s (likely a stale token-introspect JWKS cache); retrying",
                slot,
                attempt,
                max_attempts,
            )
            time.sleep(USER_TOKEN_RETRY_BACKOFF_SECONDS * attempt)
            continue
        break

    logger.info("Response status: %s", response.status_code)

    try:
        response_json = response.json()
    except ValueError:
        return {
            "error": True,
            "message": "Invalid JSON response from server",
            "status": response.status_code,
        }

    if response.status_code >= HTTP_BAD_REQUEST:
        response_json["error"] = True
        response_json.setdefault("message", f"Request failed with status {response.status_code}")

    return response_json


def format_response_for_chat(response_json: dict, empty_message: str = "") -> dict:
    """Format the CIQ response into a chat-friendly structure.

    `empty_message` turns a no-rows response into a success confirmation —
    used by write steps (upserts/deletes) where empty data still means the
    operation happened.
    """
    if response_json.get("error"):
        return {
            "type": "error",
            "message": response_json.get("message", "An error occurred"),
            "raw_data": response_json,
        }

    data = response_json.get("data", [])

    if not data:
        if empty_message:
            return {
                "type": "success",
                "message": empty_message,
                "items": [],
                "raw_data": response_json,
            }
        return {
            "type": "info",
            "message": "Done — no rows returned for this query.",
            "raw_data": response_json,
        }

    formatted_items = []
    for row in data:
        nodes = row.get("nodes", {})
        item = {}
        for key, value in nodes.items():
            parts = key.split(".")
            if len(parts) >= MIN_KEY_PARTS:
                node_name = parts[0]
                prop_type = parts[1] if parts[1] != "property" else parts[2] if len(parts) > MIN_KEY_PARTS else parts[1]
                display_key = f"{node_name}_{prop_type}".replace(".", "_")
            else:
                display_key = key
            item[display_key] = value
        if item:
            formatted_items.append(item)

    return {
        "type": "success",
        "message": f"Found {len(formatted_items)} result(s)",
        "items": formatted_items,
        "raw_data": response_json,
    }


@api_chat.get("/", tags=[tag])
def chat_home():
    """Display the story-driven chat interface."""
    load_dotenv(_ENV_FILE, override=True)

    scenario = _load_scenario()

    setup_complete = bool(os.getenv("APP_TOKEN") and os.getenv("PROJECT_ID"))
    user_token_present = bool(os.getenv("USER_TOKEN"))

    slots = {step["slot"] for scene in scenario["scenes"] for step in scene["steps"]}
    missing_queries = sorted(
        (f"CIQ_QUERY_ID_{slot}" for slot in slots if not os.getenv(f"CIQ_QUERY_ID_{slot}")),
        key=lambda key: (len(key), key),
    )

    scenes_by_id = {scene["id"]: scene for scene in scenario["scenes"]}

    return render_template(
        "chat/index.html",
        scenario=scenario,
        scenes_by_id=scenes_by_id,
        setup_complete=setup_complete,
        user_token_present=user_token_present,
        missing_queries=missing_queries,
    )


@api_chat.post("/story-step", tags=[tag])
def execute_story_step():
    """Execute a story step and return the response with narrative context."""
    scenario = _load_scenario()

    scene_id = request.form.get("scene_id", "")
    step_id = request.form.get("step_id", "")

    scene, step, step_index = _find_step(scenario, scene_id, step_id)
    if scene is None or step is None:
        return render_template(
            "chat/story_response.html",
            error="Unknown scene or step",
            response=None,
            insight="",
        )

    response_json = execute_ciq_slot(step["slot"], step.get("params", {}))
    formatted_response = format_response_for_chat(response_json, step.get("empty_message", ""))

    query = ciq_query_for_slot(step["slot"])

    is_last_step = step_index >= len(scene["steps"]) - 1
    next_step = None if is_last_step else scene["steps"][step_index + 1]

    return render_template(
        "chat/story_response.html",
        narrative=step.get("narrative", ""),
        question=step["question"],
        action=step["action"],
        response=formatted_response,
        insight=step.get("insight", ""),
        input_params=step.get("params", {}),
        slot=step["slot"],
        query_label=query.get("display_name", ""),
        scene_id=scene_id,
        step_id=step_id,
        is_last_step=is_last_step,
        next_step=next_step,
        conclusion=scene.get("conclusion") if is_last_step else None,
    )
