# Copyright (c) 2026 IndyKite
"""Mini app: create one application agent.

Run `python app.py`, open the page, press the button. Nothing is stored; the
platform's answer is shown as-is.
"""

import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

ELEMENT = {
    "title": "App Agent",
    "description": ("Creates an application agent with its API permissions. Credentials are a separate element."),
    "method": "POST",
    "path": "/configs/v1/application-agents",
    "auth_hint": "Authenticated with the service-account token (SA_TOKEN).",
    "button": "Create app agent",
    "id_label": "App agent id",
    # (environment variable, label shown on the page)
    "env_fields": [("APPLICATION_ID", "Application id")],
    # Shown above the form. Source: the guides on developer.indykite.com.
    "explanation": {
        "intro": (
            "An application agent is the identity that authenticates your API calls; its credentials, the next "
            "element, are the X-IK-ClientKey the data-plane APIs expect. api_permissions decides which of those "
            "APIs the agent may call, and a call to one it does not hold fails with 401 and the message "
            '"insufficient API access level for appAgent". Permissions are per agent and are not shared '
            "between agents of the same application, so grant only what this agent needs. The example grants "
            "all six."
        ),
        "points": [
            ("application_id", "The application this agent belongs to. Filled in by this app from APPLICATION_ID."),
            (
                "Authorization",
                (
                    "AuthZEN evaluations, single and batch, plus the action, resource and subject "
                    "search endpoints. This is what a Policy Enforcement Point needs."
                ),
            ),
            (
                "Capture",
                (
                    "Write to the graph directly: upsert and delete nodes, relationships and properties. "
                    "Not policy-mediated, meant for ingestion pipelines and sync jobs."
                ),
            ),
            (
                "ContXIQ",
                (
                    "Run knowledge queries on behalf of a user or of the _Application subject "
                    "(POST /contx-iq/v1/execute), and resolve a user token to its graph subject "
                    "(GET /contx-iq/v1/whoami)."
                ),
            ),
            (
                "EntityMatching",
                (
                    "Run entity matching pipelines to find and merge records describing the same "
                    "real-world identity across sources."
                ),
            ),
            (
                "ReadAuthZConfigs",
                (
                    "Read the project's active KBAC policies as stored (GET /access/v1/policies). "
                    "Read-only and independent of Authorization: an agent that only makes "
                    "decisions does not need it."
                ),
            ),
            (
                "ReadDataSchema",
                (
                    "Read the observed schema of the graph (GET /data-schema/v1/): node types with "
                    "their properties and the relationship combinations between them, with counts "
                    "but no data."
                ),
            ),
        ],
        "guide": ("Environment guide", "https://developer.indykite.com/guides/guide-environment"),
    },
}

app = Flask(__name__, template_folder=str(HERE / "templates"))


def _manifest():
    return json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))


def _env(key):
    return (os.getenv(key) or "").strip()


def _target_url():
    return _env("URL_ENDPOINTS").rstrip("/") + ELEMENT["path"]


def _headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_env('SA_TOKEN')}",
    }


def _prepare(payload):
    """Turn the example into the request body: add what comes from the environment."""
    payload["application_id"] = _env("APPLICATION_ID")
    return payload


def _created_id(body):
    if not isinstance(body, dict):
        return None
    return body.get("id")


@app.route("/")
def index():
    """Render the page: the target, the environment values and the editable example."""
    env_fields = [{"key": key, "label": label, "value": _env(key)} for key, label in ELEMENT["env_fields"]]
    view = {**ELEMENT, "url": _target_url(), "env_fields": env_fields}
    return render_template("index.html", element=view, example_json=json.dumps(_manifest(), indent=2))


@app.route("/create", methods=["POST"])
def create():
    """Send the edited example to the platform and hand its answer back to the page."""
    payload = _prepare(request.get_json(silent=True) or {})
    try:
        resp = requests.post(_target_url(), headers=_headers(), json=payload, timeout=30)
    except requests.RequestException as exc:
        return jsonify({"ok": False, "status": 0, "id": None, "response": {"error": str(exc)}}), 502
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:2000]}
    return jsonify(
        {"ok": resp.ok, "status": resp.status_code, "id": _created_id(body) if resp.ok else None, "response": body},
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5103")), debug=False)
