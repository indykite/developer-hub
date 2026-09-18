# Copyright (c) 2026 IndyKite
"""Mini app: create one ContX IQ knowledge query.

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
    "title": "ContX IQ Knowledge Query",
    "description": (
        "Creates a knowledge query bound to a ContX IQ policy: the projection of nodes, "
        "relationships and values an agent gets back."
    ),
    "method": "POST",
    "path": "/configs/v1/knowledge-queries",
    "auth_hint": "Authenticated with the service-account token (SA_TOKEN).",
    "button": "Create knowledge query",
    "id_label": "Knowledge query id",
    # (environment variable, label shown on the page)
    "env_fields": [("PROJECT_ID", "Project id"), ("CIQ_POLICY_ID", "ContX IQ policy id")],
    # Shown above the form. Source: the guides on developer.indykite.com.
    "explanation": {
        "intro": (
            "A knowledge query says what to do with the data its policy allows. The policy is what may be "
            "reached; the query is the projection actually returned, and for writes the nodes and relationships "
            "to upsert or delete. The two are compiled into one Cypher statement when the query runs. A value "
            "only comes back if its variable is bound in the policy's cypher, allow-listed in the policy's "
            "allowed_reads, and asked for here."
        ),
        "points": [
            (
                "project_id, policy_id",
                (
                    "The project and the ContX IQ policy this query is bound to. Filled in by "
                    "this app from PROJECT_ID and CIQ_POLICY_ID."
                ),
            ),
            (
                "name",
                "The id callers use at execution time, through POST /contx-iq/v1/execute or the ciq_execute MCP tool.",
            ),
            (
                "description",
                (
                    "Read by AI agents when they choose a query, so it is written as a usable "
                    "instruction, with the call and an example, not just a label."
                ),
            ),
            ("query.nodes", "The values to return, written as <variable>.property.<attr> or <variable>.external_id."),
            ("query.relationships", "The relationship variables to return; empty here, the example only reads nodes."),
            (
                "query.aggregate_values",
                "Values collected with WITH in the policy's cypher, the calling agent in this example.",
            ),
            (
                "upsert_nodes, delete_nodes",
                "The variables to write or remove, on a query that mutates. This one only reads, so they are absent.",
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
    payload["policy_id"] = _env("CIQ_POLICY_ID")
    # The API takes the query as a JSON string, not as an object.
    if isinstance(payload.get("query"), dict):
        payload["query"] = json.dumps(payload["query"])
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
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5108")), debug=False)
