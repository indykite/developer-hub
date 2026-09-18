# Copyright (c) 2026 IndyKite
"""Mini app: ask the platform one AuthZEN question.

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
    "title": "AuthZEN Evaluation",
    "description": (
        "Creates nothing. Asks the platform one question, can this subject do this action on this "
        "resource, and shows the decision."
    ),
    "method": "POST",
    "path": "/access/v1/evaluation",
    "auth_hint": (
        "Authenticated with an app agent token (APP_TOKEN, sent as X-IK-ClientKey), not the service-account token."
    ),
    "button": "Evaluate",
    "id_label": "Decision",
    # (environment variable, label shown on the page)
    "env_fields": [],
    # Shown above the form. Source: the guides on developer.indykite.com.
    "explanation": {
        "intro": (
            "This is the question the authorization-policy element exists to answer, in the shape AuthZEN "
            "standardizes: a subject, an action and a resource, plus an optional context. The platform maps the "
            "three onto nodes and actions in the graph, evaluates the project's active KBAC policies against the "
            "data as it stands right now, and returns a decision, true or false. Note the different credential "
            "above: this is a data-plane call, so it uses the app agent token, not the service-account one."
        ),
        "points": [
            ("subject.type, subject.id", "The node type of whoever is asking, and their external_id in the graph."),
            ("resource.type, resource.id", "The node type of what they want to reach, and its external_id."),
            (
                "action.name",
                "The operation being requested. It has to be one of the policy's actions, CAN_TRIGGER here.",
            ),
            (
                "context.input_params",
                (
                    "Optional. Extra values the policy's cypher or filter can read, a channel or "
                    "an IP address for instance."
                ),
            ),
            (
                "The example",
                (
                    "It matches the policy the authorization-policy element creates: the answer is true "
                    "once User joe can reach Workflow wf1 through WORKS_IN or CAN_TRIGGER in the graph, "
                    "and false otherwise, with no error either way."
                ),
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
        "X-IK-ClientKey": _env("APP_TOKEN"),
    }


def _prepare(payload):
    """Turn the example into the request body: add what comes from the environment."""
    return payload


def _created_id(body):
    if not isinstance(body, dict):
        return None
    return str(body.get("decision")) if "decision" in body else None


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
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5111")), debug=False)
