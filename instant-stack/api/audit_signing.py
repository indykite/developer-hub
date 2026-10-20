# Copyright (c) 2026 IndyKite
"""Audit Signing configuration (``/configs/v1/audit-signings``).

The configuration switches the project's signed, hash-chained audit trail on.
With ``PLATFORM_MANAGED`` the platform's key signs every record, and nothing
else has to be supplied. See the public Audit Signing guide.
"""

import json
import logging
import os

import requests
from api import _dataset
from api.project import update_env_variable
from flask import render_template, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_audit_signing", description="Audit Signing")
security = [{"BearerToken": []}]

logger = logging.getLogger(__name__)

HTTP_OK = 200
HTTP_MULTIPLE_CHOICES = 300


class Unauthorized(BaseModel):
    code: int = Field(-1, description="Status Code")
    message: str = Field("Unauthorized!", description="Exception Information")


api_audit_signing = APIBlueprint(
    "api_audit_signing",
    __name__,
    url_prefix="/api_audit_signing",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)


def form_default():
    """Create-form defaults from the dataset manifest; project_id from env."""
    a = _dataset.AUDIT_SIGNING
    return {
        "name": a.get("name", ""),
        "display_name": a.get("display_name", ""),
        "description": a.get("description", ""),
        "provider": a.get("provider", "PLATFORM_MANAGED"),
        "project_id": os.getenv("PROJECT_ID", ""),
    }


@api_audit_signing.get("/create", tags=[tag])
def show_create_form():
    """Display the audit-signing creation form with default values."""
    return render_template("audit_signing/create_form.html", default_data=form_default())


@api_audit_signing.post("/create", tags=[tag])
def create_audit_signing():
    """Create the project's audit-signing configuration."""
    json_data = {
        "name": request.form.get("name", ""),
        "project_id": request.form.get("project_id", ""),
        "provider": request.form.get("provider", "PLATFORM_MANAGED"),
    }
    # display_name and description are optional, and an empty string is rejected.
    for field in ("display_name", "description"):
        value = request.form.get(field, "").strip()
        if value:
            json_data[field] = value

    api_url = f"{os.getenv('URL_ENDPOINTS')}/configs/v1/audit-signings"
    logger.info("Creating audit signing at: %s", api_url)
    logger.debug("Request payload: %s", json.dumps(json_data, indent=2))

    response = requests.post(
        api_url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.getenv('SA_TOKEN')}",
        },
        json=json_data,
        timeout=30,
    )
    logger.info("Response status: %s", response.status_code)
    logger.debug("Response text: %s", response.text)

    try:
        response_json = response.json()
    except ValueError:
        response_json = {
            "message": "Invalid JSON response",
            "status": response.status_code,
            "response_text": response.text[:500] if response.text else "No response body",
        }

    audit_signing_id = None
    if HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES and isinstance(response_json, dict):
        audit_signing_id = response_json.get("id")
        if audit_signing_id:
            update_env_variable("AUDIT_SIGNING_ID", audit_signing_id)

    return render_template(
        "audit_signing/result.html",
        response_json=response_json,
        status_code=response.status_code,
        audit_signing_id=audit_signing_id,
    )
