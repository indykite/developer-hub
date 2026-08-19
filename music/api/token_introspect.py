# Copyright (c) 2026 IndyKite
import json
import logging
import os

import requests
from api._env import update_env_variable
from api._music_data import TOKEN_INTROSPECT_DEFAULTS
from flask import render_template, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_token_introspect", description="Token Introspect")
security = [{"BearerToken": []}]

logger = logging.getLogger(__name__)

# HTTP status code constants
HTTP_OK = 200
HTTP_MULTIPLE_CHOICES = 300


class Unauthorized(BaseModel):
    code: int = Field(-1, description="Status Code")
    message: str = Field("Unauthorized!", description="Exception Information")


api_token_introspect = APIBlueprint(
    "api_token_introspect",
    __name__,
    url_prefix="/api_token_introspect",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)


@api_token_introspect.get("/create", tags=[tag])
def show_create_form():
    """Display the token introspect creation form with default values from the manifest."""
    default_data = {**TOKEN_INTROSPECT_DEFAULTS, "project_id": os.getenv("PROJECT_ID", "")}
    return render_template("token_introspect/create_form.html", default_data=default_data)


@api_token_introspect.post("/create", tags=[tag])
def create_token_introspect():
    """Create a new token introspect with the provided form data."""
    # Get form data
    json_data = {
        "claims_mapping": json.loads(request.form.get("claims_mapping", "{}")),
        "description": request.form.get("description", ""),
        "display_name": request.form.get("display_name", ""),
        "ikg_node_type": request.form.get("ikg_node_type", "Person"),
        "jwt_matcher": json.loads(request.form.get("jwt_matcher", "{}")),
        "name": request.form.get("name", ""),
        "offline_validation": json.loads(request.form.get("offline_validation", "{}")),
        "perform_upsert": request.form.get("perform_upsert") == "true",
        "project_id": request.form.get("project_id", ""),
    }

    url_endpoints = os.getenv("URL_ENDPOINTS")
    sa_token = os.getenv("SA_TOKEN")

    api_url = f"{url_endpoints}/configs/v1/token-introspects"

    logger.info("Creating token introspect at: %s", api_url)
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

    # Extract and save token introspect ID if the request was successful
    token_introspect_id_saved = False
    token_introspect_id = None

    if HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES and isinstance(response_json, dict):
        # Try to extract token introspect ID from different possible locations in the response
        # Common field names for token introspect ID
        token_introspect_id = response_json.get("id") or response_json.get("token_introspect_id")

        if token_introspect_id:
            try:
                update_env_variable("TOKEN_INTROSPECT_ID", token_introspect_id)
                token_introspect_id_saved = True
                logger.info("Saved TOKEN_INTROSPECT_ID: %s", token_introspect_id)
            except Exception:
                logger.exception("Failed to save TOKEN_INTROSPECT_ID")

    return render_template(
        "token_introspect/result.html",
        response_json=response_json,
        status_code=response.status_code,
        token_introspect_id=token_introspect_id,
        token_introspect_id_saved=token_introspect_id_saved,
    )
