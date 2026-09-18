# Copyright (c) 2026 IndyKite
"""Mini app: create one set of credentials for an application agent.

Run `python app.py`, open the page, press the button. Nothing is stored; the
platform's answer is shown as-is.
"""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

ELEMENT = {
    "title": "App Agent Credentials",
    "description": (
        "Creates credentials for an existing app agent and shows the resulting token, the "
        "X-IK-ClientKey the data-plane APIs expect."
    ),
    "method": "POST",
    "path": "/configs/v1/application-agent-credentials",
    "auth_hint": "Authenticated with the service-account token (SA_TOKEN).",
    "button": "Create credentials",
    "id_label": "Agent token (X-IK-ClientKey)",
    # (environment variable, label shown on the page)
    "env_fields": [("APP_AGENT_ID", "App agent id")],
    # Shown above the form. Source: the guides on developer.indykite.com.
    "explanation": {
        "intro": (
            "Credentials turn an app agent into a usable token. Send that token as the X-IK-ClientKey header on "
            "every call to the data-plane APIs: Capture, ContX IQ execute and whoami, the AuthZEN evaluation and "
            "search endpoints, the policy listing, the data schema and entity matching. The Config API the other "
            "mini apps use keeps its own service-account token. The answer below carries the token itself, so "
            "copy it out now: it is what the authzen app's APP_TOKEN expects."
        ),
        "points": [
            ("app_agent_id", "The agent these credentials belong to. Filled in by this app from APP_AGENT_ID."),
            ("display_name", "Human-readable label for the credential."),
            (
                "expire_days",
                "How long the token stays valid. After that it stops working and you create new credentials.",
            ),
        ],
        "guide": ("Credentials guide", "https://developer.indykite.com/guides/guide-credentials"),
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
    payload["application_agent_id"] = _env("APP_AGENT_ID")
    # The API wants an absolute expiry (RFC3339); the example keeps a duration so it never goes stale.
    raw_days = payload.pop("expire_days", 180)
    try:
        days = int(raw_days)
    except (TypeError, ValueError):
        msg = f"expire_days must be a whole number of days, got {json.dumps(raw_days)}"
        raise ValueError(msg) from None
    payload["expire_time"] = (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return payload


def _created_id(body):
    if not isinstance(body, dict):
        return None
    config = body.get("application_agent_config")
    if isinstance(config, dict):
        return config.get("token")
    return config if isinstance(config, str) else None


@app.route("/")
def index():
    """Render the page: the target, the environment values and the editable example."""
    env_fields = [{"key": key, "label": label, "value": _env(key)} for key, label in ELEMENT["env_fields"]]
    view = {**ELEMENT, "url": _target_url(), "env_fields": env_fields}
    return render_template("index.html", element=view, example_json=json.dumps(_manifest(), indent=2))


@app.route("/create", methods=["POST"])
def create():
    """Send the edited example to the platform and hand its answer back to the page."""
    try:
        payload = _prepare(request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"ok": False, "status": 400, "id": None, "response": {"error": str(exc)}}), 400
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
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5104")), debug=False)
