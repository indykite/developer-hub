# Copyright (c) 2026 IndyKite
"""Provision-everything add-on: replay every create button in order, from the top.

The whole stack is provisioned from the manifest, so a run needs only an
existing organization to create the project in - nothing else is assumed to
exist except the run inputs (URL_ENDPOINTS, SA_TOKEN, ORGANIZATION_ID, and
optionally DATASET to pick the data/<name>/ bundle). Steps run in dependency order:
Project -> Application -> App Agent (+ credentials) -> Token Introspect ->
MCP Server, then both captures (nodes + relationships), the KBAC policies,
the external data resolvers, and each CIQ policy immediately followed by its
knowledge query. Each payload reads the prior step's saved id from the
environment (lazily, per step), so the create-chain threads through in one
run. Each step POSTs the exact form payload the corresponding create form
would submit, through the Flask test client, so it runs the same route
handlers as a person clicking the buttons - and skip_existing skips any step
whose id is already in .env.

Captures on a freshly created project/agent can hit a transient 401, "failed to
evaluate API access" (the evaluation errored while the IKG was still stabilizing
- the error is cached for ~1 minute, so the retry waits past that TTL). The
captures are idempotent upserts, so the whole upload is safe to retry. A cached
CAN_ACCESS denial ("insufficient API access level") is treated as non-retryable
and surfaced immediately.

AuthZEN evaluations and CIQ executes are deliberately NOT replayed: they are
reads/runs, not creations - use the evaluate/execute forms once provisioned.
"""

import html
import json
import logging
import os
import re
import time
from pathlib import Path

import requests
from api import _dataset
from api.capture import _load_default_nodes
from api.ciq_knowledge_query import _QUERY_DEFS
from api.ciq_knowledge_query import _default_for_slot as _query_default_for_slot
from api.ciq_policy import _POLICY_DEFS
from api.ciq_policy import _default_for_slot as _policy_default_for_slot
from api.external_data_resolver import _RESOLVER_DEFS
from api.external_data_resolver import _default_for_slot as _resolver_default_for_slot
from api.project import update_env_variable
from api.relationships import _load_default_relationships
from dotenv import dotenv_values, load_dotenv
from flask import Response, current_app, render_template, request, stream_with_context
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_provision", description="Provision Everything")
security = [{"BearerToken": []}]

logger = logging.getLogger(__name__)

ENV_FILE = Path(__file__).parent.parent / ".env"

HTTP_OK = 200
HTTP_MULTIPLE_CHOICES = 300
HTTP_BAD_REQUEST = 400
HTTP_SERVER_ERROR = 500

# Captures retry the whole (idempotent) upload on a transient evaluation error -
# see the module docstring.
CAPTURE_TRIES = 4
EVAL_ERROR_RETRY_DELAY_SECONDS = 70
_FAILED_TO_EVALUATE = "failed to evaluate API access"

# A fresh project's IKG database takes minutes to provision. The App Agent must
# be created only AFTER the IKG is ACTIVE: the platform projects the agent's API
# permissions into the IKG when the agent is created, and an event processed
# while the IKG is still provisioning is dropped without retry - leaving the
# agent permanently unauthorized (the capture then 401s "Invalid AppAgent JWT").
IKG_READY_DEADLINE_SECONDS = 600
IKG_POLL_DELAY_SECONDS = 5
# The agent's permissions are written in the same transaction as the create, so
# a short fixed pause before the first capture is enough.
AGENT_SETTLE_SECONDS = 2

# Environment values the run cannot create itself, in checklist order.
PREREQUISITES = [
    ("URL_ENDPOINTS", "Platform API base URL (e.g. https://eu.api.indykite.com)"),
    ("SA_TOKEN", "Service-account token"),
    ("ORGANIZATION_ID", "Organization ID the project is created under"),
]
OPTIONAL_KEYS = [
    ("DATASET", "Which data/<name>/ bundle to provision (default: canbank)"),
]


class Unauthorized(BaseModel):
    code: int = Field(-1, description="Status Code")
    message: str = Field("Unauthorized!", description="Exception Information")


api_provision = APIBlueprint(
    "api_provision",
    __name__,
    url_prefix="/api_provision",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)


# --------------------------------------------------------------------------
# Form payload builders - each mirrors what the corresponding create form
# pre-fills. They are called lazily, step by step, so each one sees the env
# vars its predecessors just saved (a knowledge query needs the
# CIQ_POLICY_ID_<slot> its policy step just wrote).
# --------------------------------------------------------------------------


def _capture_nodes_payload():
    return {"nodes": json.dumps(_load_default_nodes())}


def _capture_relationships_payload():
    return {"relationships": json.dumps(_load_default_relationships())}


def _kbac_payload(index=0):
    # Shares the KBAC form defaults with api.authorization_policy via the dataset
    # manifest (api/_dataset.py) instead of duplicating them here.
    return _dataset.kbac_form_default(os.getenv("PROJECT_ID", ""), index)


def _resolver_payload(slot):
    return _resolver_default_for_slot(slot)


def _ciq_policy_payload(slot):
    payload = _policy_default_for_slot(slot)
    # The create form submits tags as one comma-separated field; a list here
    # would become a repeated form field of which the handler reads only the
    # first item.
    payload["tags"] = ",".join(payload.get("tags") or [])
    return payload


def _ciq_query_payload(slot):
    return _query_default_for_slot(slot)


def _project_payload():
    # /api_project/create form fields. organization_id is a run input (env),
    # everything else from the manifest project section.
    p = _dataset.PROJECT
    db = p.get("db_connection", {}) or {}
    return {
        "name": p.get("name", ""),
        "display_name": p.get("display_name", ""),
        "description": p.get("description", ""),
        "organization_id": os.getenv("ORGANIZATION_ID", ""),
        "region": p.get("region", "europe-west1"),
        "ikg_size": p.get("ikg_size", "2GB"),
        "db_name": db.get("name", ""),
        "db_url": db.get("url", ""),
        "db_username": db.get("username", ""),
        "db_password": db.get("password", ""),
    }


def _application_payload():
    a = _dataset.APPLICATION
    return {
        "name": a.get("name", ""),
        "display_name": a.get("display_name", ""),
        "description": a.get("description", ""),
        "project_id": os.getenv("PROJECT_ID", ""),
    }


def _app_agent_payload():
    # The create route splits api_permissions on newlines/commas; the app-agent
    # create also registers credentials, saving APP_TOKEN in the same step.
    a = _dataset.APP_AGENT
    return {
        "name": a.get("name", ""),
        "display_name": a.get("display_name", ""),
        "description": a.get("description", ""),
        "application_id": os.getenv("APPLICATION_ID", ""),
        "api_permissions": "\n".join(_dataset.DEFAULT_API_PERMISSIONS),
    }


def _mcp_server_payload():
    # References the app agent + token introspect created earlier in the run.
    s = _dataset.MCP_SERVER
    return {
        "name": s.get("name", ""),
        "display_name": s.get("display_name", ""),
        "description": s.get("description", ""),
        "enabled": "true" if s.get("enabled", True) else "false",
        "project_id": os.getenv("PROJECT_ID", ""),
        "app_agent_id": os.getenv("APP_AGENT_ID", ""),
        "token_introspect_id": os.getenv("TOKEN_INTROSPECT_ID", ""),
        "scopes_supported": ",".join(s.get("scopes_supported", [])),
    }


def _token_introspect_payload():
    # Mirrors the /api_token_introspect/create form: JSON-valued fields are
    # posted as JSON strings, perform_upsert as a "true"/"false" string. Values
    # come from the dataset manifest (token_introspect section).
    ti = _dataset.TOKEN_INTROSPECT
    return {
        "name": ti.get("name", ""),
        "display_name": ti.get("display_name", ""),
        "description": ti.get("description", ""),
        "ikg_node_type": ti.get("ikg_node_type", "Person"),
        "jwt_matcher": json.dumps(ti.get("jwt_matcher", {})),
        "claims_mapping": json.dumps(ti.get("claims_mapping", {})),
        "offline_validation": json.dumps(ti.get("offline_validation", {})),
        "perform_upsert": "true" if ti.get("perform_upsert", True) else "false",
        "project_id": os.getenv("PROJECT_ID", ""),
    }


# --------------------------------------------------------------------------
# Step list - the landing-page buttons after the MCP server config, in order.
# --------------------------------------------------------------------------


def _step(label, path, payload, env_keys, kind="create"):
    return {"label": label, "path": path, "payload": payload, "env_keys": env_keys, "kind": kind}


def build_steps():
    """Return the ordered steps: token introspect, captures, KBAC, resolvers, then CIQ policy+query pairs."""
    steps = []
    # Getting-Started configs, in dependency order, so a run can start from an
    # empty project: project -> application -> app agent (+credentials) ->
    # token introspect -> mcp server. Each payload reads the prior step's saved
    # id from the environment (lazily, per step), so the chain threads through.
    # Each is added only when the manifest declares that section, and is
    # skip_existing-gated on the id it records.

    def _label(cfg, prefix):
        return f"{prefix}: {cfg.get('display_name') or cfg.get('name', '')}"

    if _dataset.PROJECT:
        steps.append(
            _step(_label(_dataset.PROJECT, "Create project"), "/api_project/create", _project_payload, ["PROJECT_ID"]),
        )
        # The IKG wait MUST precede the App Agent: the platform projects the
        # agent's API permissions into the IKG on create, and an event
        # processed while the IKG is still provisioning is dropped without
        # retry - leaving the agent permanently unauthorized (capture 401s).
        steps.append(_step("Wait for project IKG", None, None, ["IKG_READY"], kind="ikg"))
    if _dataset.APPLICATION:
        steps.append(
            _step(
                _label(_dataset.APPLICATION, "Create application"),
                "/api_application/create",
                _application_payload,
                ["APPLICATION_ID"],
            ),
        )
    if _dataset.APP_AGENT:
        steps.append(
            _step(
                _label(_dataset.APP_AGENT, "Create app agent"),
                "/api_app_agent/create",
                _app_agent_payload,
                ["APP_AGENT_ID", "APP_TOKEN"],
            ),
        )
    if _dataset.TOKEN_INTROSPECT:
        steps.append(
            _step(
                _label(_dataset.TOKEN_INTROSPECT, "Create token introspect"),
                "/api_token_introspect/create",
                _token_introspect_payload,
                ["TOKEN_INTROSPECT_ID"],
            ),
        )
    if _dataset.MCP_SERVER:
        steps.append(
            _step(
                _label(_dataset.MCP_SERVER, "Create MCP server"),
                "/api_mcp_server/create",
                _mcp_server_payload,
                ["MCP_SERVER_ID"],
            ),
        )
    if _dataset.APP_AGENT:
        # Let the fresh agent's permissions settle before the first capture.
        steps.append(
            _step(
                f"Wait {AGENT_SETTLE_SECONDS}s for App Agent permissions",
                None,
                None,
                ["AGENT_READY"],
                kind="settle",
            ),
        )
    steps += [
        _step(
            "Capture nodes",
            "/api_capture/create",
            _capture_nodes_payload,
            ["CAPTURED_NODES"],
            kind="capture",
        ),
        _step(
            "Capture relationships",
            "/api_relationships/create",
            _capture_relationships_payload,
            ["CAPTURED_RELATIONSHIPS"],
            kind="capture",
        ),
    ]
    # One step per KBAC policy in the manifest. The env key naming lives in
    # _dataset.kbac_env_key, shared with the create route via the payload's
    # env_key field so each policy's ID is recorded under its own entry.
    steps.extend(
        _step(
            f"Create KBAC policy: {policy.get('display_name', policy.get('name', ''))}",
            "/api_authorization_policy/create",
            lambda i=i: _kbac_payload(i),
            [_dataset.kbac_env_key(i)],
        )
        for i, policy in enumerate(_dataset.KBAC_POLICIES)
    )
    steps.extend(
        _step(
            f"Create external data resolver {spec['slot']}: {spec['display_name']}",
            "/api_external_data_resolver/create",
            lambda s=spec["slot"]: _resolver_payload(s),
            [f"EXTERNAL_DATA_RESOLVER_ID_{spec['slot']}"],
        )
        for spec in _RESOLVER_DEFS
    )
    for pol in _POLICY_DEFS:
        slot = pol["slot"]
        steps.append(
            _step(
                f"Create CIQ policy {slot}: {pol['display_name']}",
                "/api_ciq_policy/create",
                lambda s=slot: _ciq_policy_payload(s),
                [f"CIQ_POLICY_ID_{slot}"],
            ),
        )
        steps.extend(
            _step(
                f"Create knowledge query {query['slot']}: {query['display_name']}",
                "/api_ciq_knowledge_query/create",
                lambda s=query["slot"]: _ciq_query_payload(s),
                [f"CIQ_QUERY_ID_{query['slot']}"],
            )
            for query in _QUERY_DEFS
            if query["slot"] == slot
        )
    return steps


def missing_prerequisites():
    """Return the PREREQUISITES entries whose .env key is absent or empty."""
    saved = {key for key, value in (dotenv_values(ENV_FILE) or {}).items() if value}
    return [entry for entry in PREREQUISITES if entry[0] not in saved]


# --------------------------------------------------------------------------
# Step runners + outcome assessment
# --------------------------------------------------------------------------


def _extract_message(page_text):
    text = html.unescape(page_text)
    match = re.search(r'"message":\s*"([^"]{1,300})"', text)
    return match.group(1) if match else ""


def _assess_create(step, page_text):
    """Judge a create step: it succeeded iff its handler saved the expected IDs to .env."""
    if all(os.getenv(key, "") for key in step["env_keys"]):
        return True, ", ".join(step["env_keys"]) + " saved"
    message = _extract_message(page_text)
    return False, message or "no ID returned - check the flask log or run this form manually"


def _assess_capture_page(page_text):
    """Judge a capture result page: (ok, failure_kind, detail).

    The capture routes render an HTML result whose alert carries the WORST
    chunk status, and the response JSON (all chunk bodies) is inlined in a
    <pre> block - so the page text is enough to both judge the run and pick
    the retry strategy: "transient" (evaluation errors / 5xx - wait out the
    server-side error cache) or None (not retryable, e.g. an access denial).
    """
    text = html.unescape(page_text)
    match = re.search(r"Status Code:\s*(\d+)", text)
    if not match:
        return False, "transient", "no status code on the capture result page"
    status = int(match.group(1))
    if HTTP_OK <= status < HTTP_MULTIPLE_CHOICES:
        return True, None, f"all chunks accepted (status {status})"
    detail = f"worst chunk status {status}: {_extract_message(page_text) or 'see the flask log'}"
    if _FAILED_TO_EVALUATE in text or status >= HTTP_SERVER_ERROR:
        return False, "transient", detail
    return False, None, detail


def _capture_iter(client, step):
    """Run a capture with retries, yielding ("progress", detail) and one final ("result", (ok, detail)).

    Chunks are idempotent upserts, so re-running the whole capture is safe.
    Evaluation errors / 5xx (IKG still stabilizing after project creation) are
    retried only after the platform's ~1-minute error cache has expired. Access
    denials are non-retryable and surface immediately.
    """
    detail = "no attempt made"
    attempt = 0
    for attempt in range(1, CAPTURE_TRIES + 1):
        response = client.post(step["path"], data=step["payload"]())
        ok, kind, detail = _assess_capture_page(response.get_data(as_text=True))
        if ok:
            update_env_variable(step["env_keys"][0], "true")
            suffix = "" if attempt == 1 else f" (try {attempt}/{CAPTURE_TRIES})"
            yield "result", (True, detail + suffix)
            return
        logger.warning("Capture %s try %s/%s failed: %s", step["path"], attempt, CAPTURE_TRIES, detail)
        if attempt >= CAPTURE_TRIES or kind is None:
            break
        yield (
            "progress",
            f"try {attempt} hit a transient platform error - waiting {EVAL_ERROR_RETRY_DELAY_SECONDS}s",
        )
        time.sleep(EVAL_ERROR_RETRY_DELAY_SECONDS)
    yield "result", (False, f"{detail} (after {attempt} tr{'y' if attempt == 1 else 'ies'})")


def _read_ikg_status(url_endpoints, sa_token, project_id):
    """Read the project's ikg_status (PENDING/ACTIVE/FAILED/...). Returns (status, error_message)."""
    try:
        response = requests.get(
            f"{url_endpoints}/configs/v1/projects/{project_id}",
            headers={"Authorization": f"Bearer {sa_token}"},
            timeout=30,
        )
    except requests.RequestException as e:
        return "", str(e)[:100]
    if response.status_code >= HTTP_BAD_REQUEST:
        return "", f"project read failed with status {response.status_code}"
    try:
        return response.json().get("ikg_status", ""), ""
    except ValueError:
        return "", "invalid JSON from project read"


def _ikg_iter():
    """Wait-for-IKG step: poll the project's ikg_status until ACTIVE.

    Yields ("progress", msg) while waiting, then one ("result", (ok, detail)).
    """
    url_endpoints = os.getenv("URL_ENDPOINTS")
    sa_token = os.getenv("SA_TOKEN")
    project_id = os.getenv("PROJECT_ID")
    if not (url_endpoints and sa_token and project_id):
        yield "result", (False, "URL_ENDPOINTS / SA_TOKEN / PROJECT_ID missing from env")
        return
    start = time.monotonic()
    while time.monotonic() - start < IKG_READY_DEADLINE_SECONDS:
        status, err = _read_ikg_status(url_endpoints, sa_token, project_id)
        elapsed = int(time.monotonic() - start)
        if status == "ACTIVE":
            update_env_variable("IKG_READY", "true")
            yield "result", (True, f"project IKG is ACTIVE after {elapsed}s")
            return
        if status == "FAILED":
            yield "result", (False, "project IKG provisioning FAILED - delete the project and provision again")
            return
        yield "progress", f"waiting for the project IKG to provision: {status or err} ({elapsed}s)"
        time.sleep(IKG_POLL_DELAY_SECONDS)
    minutes = IKG_READY_DEADLINE_SECONDS // 60
    yield "result", (False, f"project IKG still not ACTIVE after {minutes} minutes - check the project in the Hub")


def _settle_iter():
    """Give the fresh App Agent a moment before the first capture.

    The permissions are written with the agent create, so a short fixed pause
    is enough - no data-plane readiness probe needed.
    """
    if not os.getenv("APP_TOKEN"):
        yield "result", (False, "APP_TOKEN missing from env")
        return
    time.sleep(AGENT_SETTLE_SECONDS)
    update_env_variable("AGENT_READY", "true")
    yield "result", (True, f"waited {AGENT_SETTLE_SECONDS}s - agent permissions are assigned with the create")


def _execute_step(client, step):
    """Run one step, yielding ("substep", detail) progress and exactly one final ("result", (ok, detail))."""
    try:
        if step["kind"] == "capture":
            for kind, payload in _capture_iter(client, step):
                yield ("substep", payload) if kind == "progress" else ("result", payload)
            return
        if step["kind"] == "ikg":
            for kind, payload in _ikg_iter():
                yield ("substep", payload) if kind == "progress" else ("result", payload)
            return
        if step["kind"] == "settle":
            for kind, payload in _settle_iter():
                yield ("substep", payload) if kind == "progress" else ("result", payload)
            return
        response = client.post(step["path"], data=step["payload"]())
        yield "result", _assess_create(step, response.get_data(as_text=True))
    except Exception as exc:
        logger.exception("Provisioning step failed: %s", step["label"])
        yield "result", (False, str(exc))


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@api_provision.get("/run", tags=[tag])
def show_run_form():
    """Display the provision page with the prerequisite checklist and step list."""
    load_dotenv(ENV_FILE, override=True)
    missing = missing_prerequisites()
    return render_template(
        "provision/run_form.html",
        steps=build_steps(),
        prerequisites=PREREQUISITES,
        optional_keys=OPTIONAL_KEYS,
        missing_keys={entry[0] for entry in missing},
        ready=not missing,
    )


def _format_event(payload):
    """Serialize one NDJSON progress event."""
    return json.dumps(payload) + "\n"


@api_provision.post("/run", tags=[tag])
def run_provisioning():
    """Capture the data and create every policy, resolver and query, streaming NDJSON progress."""
    skip_existing = request.form.get("skip_existing") == "true"
    client = current_app.test_client()

    def event_stream():
        load_dotenv(ENV_FILE, override=True)
        missing = missing_prerequisites()
        if missing:
            labels = ", ".join(label for _key, label in missing)
            yield _format_event({"type": "blocked", "detail": f"Missing from .env: {labels}"})
            yield _format_event({"type": "done", "aborted": True, "ok": 0, "failed": 0, "skipped": 0})
            return
        # Skip decisions come from the .env FILE, not os.environ: stale ids can
        # survive in the process environment after a project delete.
        saved_ids = {key for key, value in (dotenv_values(ENV_FILE) or {}).items() if value}
        steps = build_steps()
        yield _format_event({"type": "start", "total": len(steps)})
        counts = {"ok": 0, "failed": 0, "skipped": 0}
        for index, step in enumerate(steps, 1):
            event = {"type": "step", "index": index, "total": len(steps), "label": step["label"], "path": step["path"]}
            if skip_existing and all(key in saved_ids for key in step["env_keys"]):
                counts["skipped"] += 1
                keys = ", ".join(step["env_keys"])
                yield _format_event({**event, "status": "skipped", "detail": f"{keys} already set"})
                continue
            logger.info("Provisioning step %s/%s: %s", index, len(steps), step["label"])
            ok, detail = False, "step yielded no result"
            for kind, payload in _execute_step(client, step):
                if kind == "substep":
                    yield _format_event({**event, "type": "substep", "detail": payload})
                else:
                    ok, detail = payload
            counts["ok" if ok else "failed"] += 1
            yield _format_event({**event, "status": "ok" if ok else "failed", "detail": detail})
        yield _format_event({"type": "done", "aborted": False, **counts})

    return Response(
        stream_with_context(event_stream()),
        mimetype="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
