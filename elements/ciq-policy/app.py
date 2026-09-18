# Copyright (c) 2026 IndyKite
"""Mini app: create one ContX IQ policy.

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
    "title": "ContX IQ Policy",
    "description": (
        "Creates a ContX IQ policy: the graph pattern a knowledge query may read, and which nodes "
        "and values it may return."
    ),
    "method": "POST",
    "path": "/configs/v1/authorization-policies",
    "auth_hint": (
        "Authenticated with the service-account token (SA_TOKEN). Same endpoint as KBAC policies; "
        "the policy version tells them apart."
    ),
    "button": "Create ContX IQ policy",
    "id_label": "Policy id",
    # (environment variable, label shown on the page)
    "env_fields": [("PROJECT_ID", "Project id")],
    # Shown above the form. Source: the guides on developer.indykite.com.
    "explanation": {
        "intro": (
            "ContX IQ is the other half of the platform's authorization: where KBAC returns allow or deny, ContX "
            "IQ returns data. This policy states what is required, the graph pattern a caller may reach, and what "
            "is allowed, the nodes and values a knowledge query may then return. A policy has exactly one subject "
            "type; for a second subject, the _Application identity for instance, create a second policy."
        ),
        "points": [
            ("project_id", "The project this policy belongs to. Filled in by this app from PROJECT_ID."),
            (
                "meta.policy_version",
                "1.0-ciq. KBAC policies share this endpoint and are told apart by their version, 2.0-kbac.",
            ),
            (
                "subject.type",
                "The single entity type that may call it. The subject is pinned to the caller's token identity.",
            ),
            (
                "condition.cypher",
                (
                    "What may be reached. The variable names bound here, subject, department and "
                    "manager in this example, are the contract with everything downstream: a "
                    "knowledge query can only ask for what this cypher binds."
                ),
            ),
            (
                "condition.filter",
                (
                    "Static values hardcoded in the policy, or partial ones supplied at execution "
                    "time as $param, checked without touching the graph."
                ),
            ),
            (
                "allowed_reads",
                (
                    "The allow-list: which nodes, relationships and aggregate_values a query may "
                    "return. subject.* means all of that node's properties."
                ),
            ),
            (
                "$token.<claim>, $_appId",
                (
                    "The caller's token claims, and the reserved value matching the calling "
                    "application's external_id when the subject is _Application."
                ),
            ),
        ],
        "guide": ("ContX IQ guide", "https://developer.indykite.com/guides/guide-contx-iq"),
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
        payload["policy"] = json.dumps(payload["policy"])
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
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5107")), debug=False)
