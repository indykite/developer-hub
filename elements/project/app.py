# Copyright (c) 2026 IndyKite
"""Mini app: create one project.

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
    "title": "Project",
    "description": "Creates a project in your organization. Everything else lives inside a project.",
    "method": "POST",
    "path": "/configs/v1/projects",
    "auth_hint": "Authenticated with the service-account token (SA_TOKEN).",
    "button": "Create project",
    "id_label": "Project id",
    # (environment variable, label shown on the page)
    "env_fields": [("ORGANIZATION_ID", "Organization id")],
    # Shown above the form. Source: the guides on developer.indykite.com.
    "explanation": {
        "intro": (
            "A project (also called an application space) is an isolated working environment with its own "
            "Identity Knowledge Graph, the Neo4j graph that holds your nodes and relationships. Everything "
            "else on the platform lives inside one: applications, KBAC and ContX IQ policies, knowledge "
            "queries, external data resolvers, token introspect configurations, trust score profiles and "
            "outbound events. Every authorization decision and every query runs against this project's graph."
        ),
        "points": [
            ("name", "URL-friendly identifier, unique within the organization. Immutable."),
            ("display_name, description", "Human-readable labels; both can be changed later."),
            ("region", "Where the managed graph is hosted, which is what decides data residency."),
            (
                "ikg_size",
                (
                    "Size of the managed graph database. To bring your own Neo4j instead, drop region and "
                    "ikg_size and send db_connection with the URL, username, password and database name."
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
    payload["organization_id"] = _env("ORGANIZATION_ID")
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
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5101")), debug=False)
