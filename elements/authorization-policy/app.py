# Copyright (c) 2026 IndyKite
"""Mini app: create one KBAC authorization policy.

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
    "title": "Authorization Policy",
    "description": (
        "Creates a knowledge-based access control policy: who may do which action on which "
        "resource, as a graph pattern."
    ),
    "method": "POST",
    "path": "/configs/v1/authorization-policies",
    "auth_hint": "Authenticated with the service-account token (SA_TOKEN).",
    "button": "Create policy",
    "id_label": "Policy id",
    # (environment variable, label shown on the page)
    "env_fields": [("PROJECT_ID", "Project id")],
    # Shown above the form. Source: the guides on developer.indykite.com.
    "explanation": {
        "intro": (
            "A KBAC policy answers one question: can this subject perform this action on this resource? The rule "
            "is a graph pattern rather than a role list, evaluated against the graph as it stands at the moment of "
            "the request, and the answer is true or false. This is the policy the authzen mini app then queries."
        ),
        "points": [
            ("project_id", "The project this policy belongs to. Filled in by this app from PROJECT_ID."),
            (
                "meta.policy_version",
                (
                    "2.0-kbac. The platform rewrites the condition into its evaluation and search "
                    "variants. The subject node must have been ingested as an identity "
                    "(is_identity: true); a plain entity never matches, and the decision is false "
                    "with no error."
                ),
            ),
            (
                "subject.type, resource.type",
                "Node types. The ids an AuthZEN request sends are matched against external_id on nodes of these types.",
            ),
            ("actions", "The action names a request may ask for, CAN_TRIGGER here."),
            (
                "condition.cypher",
                (
                    "The pattern that has to match, written with the variables subject and resource. "
                    "No CALL and no RETURN on this policy version."
                ),
            ),
            (
                "condition.filter",
                (
                    "Optional. A boolean pre-check over the request's input params and the user "
                    "token's claims, evaluated without touching the graph. The decision is true only "
                    "if both the cypher and the filter hold."
                ),
            ),
            (
                "status, tags",
                "ACTIVE makes the policy count. Tags let an AuthZEN request narrow which policies are consulted.",
            ),
        ],
        "guide": ("AuthZEN guide", "https://developer.indykite.com/guides/guide-authzen"),
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
    # The API takes the policy as a JSON string, not as an object.
    if isinstance(payload.get("policy"), dict):
        payload["policy"] = json.dumps(payload["policy"], separators=(",", ":"))
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
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5106")), debug=False)
