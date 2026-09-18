# Copyright (c) 2026 IndyKite
"""Mini app: create one token introspect configuration.

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
    "title": "Token Introspect",
    "description": (
        "Creates a token introspect configuration: how the platform validates your users' tokens "
        "and maps them to graph nodes."
    ),
    "method": "POST",
    "path": "/configs/v1/token-introspects",
    "auth_hint": "Authenticated with the service-account token (SA_TOKEN).",
    "button": "Create token introspect",
    "id_label": "Token introspect id",
    # (environment variable, label shown on the page)
    "env_fields": [("PROJECT_ID", "Project id")],
    # Shown above the form. Source: the guides on developer.indykite.com.
    "explanation": {
        "intro": (
            "A token introspect configuration tells the platform how to validate the access tokens your users "
            "already carry, and which node in the graph each token stands for. Without one a user's Bearer token "
            "means nothing here. Once the token is validated, its subject claim is matched against external_id on "
            "a node of the configured type; external_id is unique per node type rather than across the whole "
            "graph, which is why the type has to be named. To see what a token resolves to, call "
            "GET /contx-iq/v1/whoami with the agent credential and the user token."
        ),
        "points": [
            ("project_id", "The project this configuration belongs to. Filled in by this app from PROJECT_ID."),
            (
                "jwt_matcher.issuer, jwt_matcher.audience",
                (
                    "Exact matches against the token's iss and aud claims. "
                    "Opaque tokens use opaque_matcher with a hint instead."
                ),
            ),
            (
                "offline_validation",
                (
                    "Validate the JWT signature against the issuer's public keys, generated from "
                    ".well-known/jwks.json when left empty. Opaque tokens can only be validated "
                    "online, against a userinfo or introspection endpoint."
                ),
            ),
            ("ikg_node_type", "The node type the token's subject claim is matched against."),
            (
                "claims_mapping",
                (
                    "Which token claims become properties on that node. Never map external_id: that is "
                    "what the matching itself uses."
                ),
            ),
            (
                "perform_upsert",
                (
                    "Just-in-time provisioning. On first use the identity node is created, or updated, "
                    "from the token's claims."
                ),
            ),
        ],
        "guide": ("Token Introspect guide", "https://developer.indykite.com/guides/guide-token-introspect"),
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
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5105")), debug=False)
