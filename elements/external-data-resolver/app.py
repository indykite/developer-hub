# Copyright (c) 2026 IndyKite
"""Mini app: create one external data resolver.

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
    "title": "External Data Resolver",
    "description": (
        "Creates an external data resolver: an HTTP call the graph runs at query time to fill a "
        "property from an outside API."
    ),
    "method": "POST",
    "path": "/configs/v1/external-data-resolvers",
    "auth_hint": "Authenticated with the service-account token (SA_TOKEN).",
    "button": "Create resolver",
    "id_label": "Resolver id",
    # (environment variable, label shown on the page)
    "env_fields": [("PROJECT_ID", "Project id")],
    # Shown above the form. Source: the guides on developer.indykite.com.
    "explanation": {
        "intro": (
            "An external data resolver lets a property live outside the graph. A node carries an external_value "
            "data reference instead of a stored value, and when a ContX IQ query asks for that property the "
            "platform calls this HTTP endpoint and puts the answer into the result. Sensitive or fast-moving "
            "data, a VIN, a balance, the weather, stays in its own system: no copy in the graph, no sync to keep "
            "current."
        ),
        "points": [
            ("project_id", "The project this resolver belongs to. Filled in by this app from PROJECT_ID."),
            ("name", "How a node's external_value references this resolver. Unique within the project and immutable."),
            (
                "url",
                (
                    "The endpoint to call. {$param} is substituted at query time and {$param || default} supplies "
                    "a fallback, London's latitude and longitude in this example."
                ),
            ),
            (
                "method, headers, request_payload",
                "The call itself. The payload is a JSON string, used with POST, PUT and PATCH.",
            ),
            ("request_content_type, response_content_type", "JSON, currently the only supported value."),
            (
                "response_selector",
                "A jq-like path picking the part of the response that becomes the value, .current here.",
            ),
        ],
        "guide": ("External Data Resolver guide", "https://developer.indykite.com/guides/guide-external-data-resolver"),
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
    payload["project_id"] = _env("PROJECT_ID")
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
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5109")), debug=False)
