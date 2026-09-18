# Copyright (c) 2026 IndyKite
"""Mini app: create one MCP server configuration.

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
    "title": "MCP Server",
    "description": (
        "Creates the MCP server configuration that binds an app agent and a token introspect to "
        "the project's MCP endpoint."
    ),
    "method": "POST",
    "path": "/configs/v1/mcp-servers",
    "auth_hint": "Authenticated with the service-account token (SA_TOKEN).",
    "button": "Create MCP server",
    "id_label": "MCP server id",
    # (environment variable, label shown on the page)
    "env_fields": [
        ("PROJECT_ID", "Project id"),
        ("APP_AGENT_ID", "App agent id"),
        ("TOKEN_INTROSPECT_ID", "Token introspect id"),
    ],
    # Shown above the form. Source: the guides on developer.indykite.com.
    "explanation": {
        "intro": (
            "This configuration is what switches the project's MCP endpoint on: until it exists the endpoint "
            "refuses every request for the project. It binds two things to that endpoint, the app agent the "
            "server calls the platform's own APIs with, and the token introspect configuration that validates "
            "the Bearer tokens arriving from MCP clients, and it declares the OAuth scopes the server "
            "advertises. The tools the endpoint then exposes are the AuthZEN ones and ciq_execute, driven by the "
            "policies and knowledge queries the other mini apps create."
        ),
        "points": [
            ("project_id", "The project whose MCP endpoint this enables. Filled in by this app from PROJECT_ID."),
            (
                "app_agent_id",
                "The agent the server uses at runtime. It needs the Authorization and ContXIQ API permissions.",
            ),
            (
                "token_introspect_id",
                "Validates inbound Bearer tokens, so a caller's token resolves to a subject in the graph.",
            ),
            ("name", "URL-friendly identifier, unique within the project. Immutable."),
            ("enabled", "Whether the endpoint accepts requests."),
            (
                "scopes_supported",
                "The OAuth scopes advertised in .well-known/oauth-protected-resource. At least one entry.",
            ),
        ],
        "guide": ("MCP guide", "https://developer.indykite.com/guides/guide-mcp"),
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
    payload["app_agent_id"] = _env("APP_AGENT_ID")
    payload["token_introspect_id"] = _env("TOKEN_INTROSPECT_ID")
    # The API rejects empty strings and lists; leave those fields out instead.
    return {key: value for key, value in payload.items() if value not in ("", [], None)}


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
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5110")), debug=False)
