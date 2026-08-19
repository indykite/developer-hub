# Copyright (c) 2026 IndyKite
"""Provision-everything add-on: replay every create button in order, from the top.

Runs the FULL setup end to end: Project, wait for the project's IKG to become
ACTIVE, Application, App Agent (+ credentials), Token Introspect, MCP Server, a
short agent settle, both captures (nodes + relationships), the ten KBAC
policies, then each CIQ policy immediately followed by its knowledge queries.
Each step POSTs the exact form payload the corresponding create form would
submit, through the Flask test client, so it runs the same route handlers as a
person clicking the buttons one by one.

Two waits gate the run (ported from demos/generic, where both were observed
necessary live):
 - after the Project create, the run polls the project read's ikg_status until
   ACTIVE - a fresh IKG takes minutes to provision, and events processed while
   it is still PENDING can be dropped without retry (hermes projects the App
   Agent's API permissions into the IKG at agent-create time, so creating the
   agent early can leave it permanently unauthorized);
 - after the config steps, a short settle before the first capture, plus
   capture retries: a cached CAN_ACCESS denial ("insufficient API access
   level") is healed by re-saving the agent's permissions and retrying, while
   a transient 401 "failed to evaluate API access" (or 5xx) is retried only
   after the platform's ~1-minute error cache has expired. The captures are
   idempotent upserts, so the whole upload is safe to retry.

The run only needs URL_ENDPOINTS, SA_TOKEN, and ORGANIZATION_ID in .env; every
other ID is created and saved as it goes.

AuthZEN evaluations and CIQ executes are deliberately NOT replayed: they are
reads/runs, not creations: use the evaluate/execute forms once provisioned.

The /backfill routes serve a different entry point: a music sandbox that was
created OUTSIDE this app. Given only the prerequisites (URL, tokens,
PROJECT_ID), they recover every derived ID into .env so the app's forms work
against that sandbox. Two naming schemes are recognized: the fixed names from
data/music_manifest.json (scripts using this repo's data), and the Hub
console's sandbox flow, which deploys the same music templates under
random-suffixed names ("<base>-<6 digits>-<index>", "app-<6 digits>", ...) and
creates no MCP server. Each lookup is a read-only GET: nothing is created or
modified on the platform. The one-by-one buttons and the provisioning run
never need the backfill; they populate .env themselves as they create things.
"""

import html
import json
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests
from api._env import remove_env_variables, update_env_variable
from api._music_data import (
    APP_AGENT_DEFAULTS,
    APPLICATION_DEFAULTS,
    CIQ_POLICIES,
    CIQ_QUERIES,
    KBAC_SLOTS,
    MCP_SERVER_DEFAULTS,
    PROJECT_DEFAULTS,
    TOKEN_INTROSPECT_DEFAULTS,
    kbac_for_slot,
)
from api.authorization_policy import _default_for_slot as _kbac_default_for_slot
from api.ciq_knowledge_query import _default_for_slot as _query_default_for_slot
from api.ciq_policy import _default_for_slot as _policy_default_for_slot
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

# A fresh project's IKG database takes minutes to provision, and any data-plane
# call before it is ACTIVE just errors (and pollutes the platform's per-pod
# error caches). The project read exposes ikg_status (PENDING/ACTIVE/FAILED),
# so wait on that - cheap config-plane polling with no side effects.
IKG_READY_DEADLINE_SECONDS = 600
IKG_POLL_DELAY_SECONDS = 5
# The platform assigns a fresh App Agent's API permissions in the same
# transaction as the agent create, so a short settle after the config steps is
# enough before the first capture; the capture retries below cover stragglers.
AGENT_SETTLE_SECONDS = 2
# Captures retry the whole (idempotent) upload with a strategy matched to WHY
# chunks failed: see the module docstring.
CAPTURE_TRIES = 4
CAPTURE_RETRY_DELAY_SECONDS = 10
EVAL_ERROR_RETRY_DELAY_SECONDS = 70
_FAILED_TO_EVALUATE = "failed to evaluate API access"
_INSUFFICIENT_ACCESS = "insufficient API access level"

# Environment values the run cannot create itself, in checklist order.
PREREQUISITES = [
    ("URL_ENDPOINTS", "Platform API base URL (e.g. https://eu.api.indykite.com)"),
    ("SA_TOKEN", "Service-account token"),
    ("ORGANIZATION_ID", "Organization ID"),
]
OPTIONAL_KEYS = []

# The backfill only reads configs, so the App Agent token is not needed.
BACKFILL_PREREQUISITES = [
    ("URL_ENDPOINTS", "Platform API base URL (e.g. https://eu.api.indykite.com)"),
    ("SA_TOKEN", "Service-account token"),
    ("PROJECT_ID", "Project ID (Getting Started step 1)"),
]
REQUEST_TIMEOUT = 30  # seconds per lookup
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409

# Keys NOT owned by a project's lifecycle. Everything else in .env (IDs,
# APP_TOKEN, readiness/capture flags) describes one specific project and is
# purged when the run creates a fresh one, so values from a project deleted
# outside this app can never skip the IKG wait or the captures.
_BASE_ENV_KEYS = {"SA_TOKEN", "URL_ENDPOINTS", "ORGANIZATION_ID", "USER_TOKEN", "PROJECT_ID"}


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
# Form payload builders: each mirrors what the corresponding create form
# pre-fills. They are called lazily, step by step, so each one sees the env
# vars its predecessors just saved (a knowledge query needs the
# CIQ_POLICY_ID_<slot> its policy step just wrote).
# --------------------------------------------------------------------------


def _project_payload():
    return {
        "name": PROJECT_DEFAULTS.get("name", ""),
        "display_name": PROJECT_DEFAULTS.get("display_name", ""),
        "description": PROJECT_DEFAULTS.get("description", ""),
        "organization_id": os.getenv("ORGANIZATION_ID", ""),
        "region": PROJECT_DEFAULTS.get("region", "europe-west1"),
        "ikg_size": PROJECT_DEFAULTS.get("ikg_size", "2GB"),
        "db_name": "",
        "db_url": "",
        "db_username": "",
        "db_password": "",  # nosec B105 - intentionally blank form field, not a credential
    }


def _application_payload():
    return {
        "name": APPLICATION_DEFAULTS.get("name", ""),
        "display_name": APPLICATION_DEFAULTS.get("display_name", ""),
        "description": APPLICATION_DEFAULTS.get("description", ""),
        "project_id": os.getenv("PROJECT_ID", ""),
    }


def _app_agent_payload():
    # Newline-joined, exactly like the create form's textarea; the handler
    # accepts commas too, but a single string field is what request.form.get
    # expects - never send the permissions as a repeated form field.
    return {
        "name": APP_AGENT_DEFAULTS.get("name", ""),
        "display_name": APP_AGENT_DEFAULTS.get("display_name", ""),
        "description": APP_AGENT_DEFAULTS.get("description", ""),
        "application_id": os.getenv("APPLICATION_ID", ""),
        "api_permissions": "\n".join(APP_AGENT_DEFAULTS.get("api_permissions", [])),
    }


def _token_introspect_payload():
    return {
        "name": TOKEN_INTROSPECT_DEFAULTS.get("name", ""),
        "display_name": TOKEN_INTROSPECT_DEFAULTS.get("display_name", ""),
        "description": TOKEN_INTROSPECT_DEFAULTS.get("description", ""),
        "project_id": os.getenv("PROJECT_ID", ""),
        "ikg_node_type": TOKEN_INTROSPECT_DEFAULTS.get("ikg_node_type", "Person"),
        "perform_upsert": "true" if TOKEN_INTROSPECT_DEFAULTS.get("perform_upsert") else "false",
        "claims_mapping": json.dumps(TOKEN_INTROSPECT_DEFAULTS.get("claims_mapping", {})),
        "jwt_matcher": json.dumps(TOKEN_INTROSPECT_DEFAULTS.get("jwt_matcher", {})),
        "offline_validation": json.dumps(TOKEN_INTROSPECT_DEFAULTS.get("offline_validation", {})),
    }


def _mcp_server_payload():
    return {
        "name": MCP_SERVER_DEFAULTS.get("name", ""),
        "display_name": MCP_SERVER_DEFAULTS.get("display_name", ""),
        "description": MCP_SERVER_DEFAULTS.get("description", ""),
        "project_id": os.getenv("PROJECT_ID", ""),
        "app_agent_id": os.getenv("APP_AGENT_ID", ""),
        "token_introspect_id": os.getenv("TOKEN_INTROSPECT_ID", ""),
        "enabled": "true" if MCP_SERVER_DEFAULTS.get("enabled", True) else "false",
        "scopes_supported": ",".join(MCP_SERVER_DEFAULTS.get("scopes_supported", [])),
    }


def _capture_payload():
    # Both capture forms default to streaming the bundled dataset file.
    return {"use_defaults": "true"}


def _kbac_payload(slot):
    payload = _kbac_default_for_slot(slot)
    # The create form submits tags as one comma-separated field; a list here
    # would become a repeated form field of which the handler reads only the
    # first item. The slot defaults always provide tags as a list.
    payload["tags"] = ",".join(payload["tags"])
    return payload


def _ciq_policy_payload(slot):
    payload = _policy_default_for_slot(slot)
    payload["tags"] = ",".join(payload["tags"])
    return payload


def _ciq_query_payload(slot):
    return _query_default_for_slot(slot)


# --------------------------------------------------------------------------
# Step list: the full landing page from the top, in click order.
# --------------------------------------------------------------------------


def _step(label, path, payload, env_keys, kind="create"):
    # "required" (the rest of the run cannot proceed without this step) is
    # stamped onto the base-setup steps in build_steps.
    return {"label": label, "path": path, "payload": payload, "env_keys": env_keys, "kind": kind, "required": False}


def build_steps():
    """Return the ordered steps: base configs (with waits), captures, KBACs, then CIQ groups."""
    project_step = _step("Create Project", "/api_project/create", _project_payload, ["PROJECT_ID"])
    # A freshly created project invalidates every derived value from a previous
    # one (see _BASE_ENV_KEYS); the run loop purges them when this step runs.
    project_step["resets_derived"] = True
    base_setup = [
        project_step,
        # The IKG wait MUST precede the App Agent: hermes projects the agent's
        # API permissions into the IKG when the agent is created, and an event
        # processed while the IKG is still provisioning is dropped without
        # retry - leaving the agent permanently unauthorized (observed live).
        _step("Wait for project IKG to become ACTIVE", None, None, ["IKG_READY"], kind="ikg"),
        _step("Create Application", "/api_application/create", _application_payload, ["APPLICATION_ID"]),
        _step(
            "Create App Agent + credentials",
            "/api_app_agent/create",
            _app_agent_payload,
            ["APP_AGENT_ID", "APP_TOKEN"],
        ),
        _step(
            "Create Token Introspect",
            "/api_token_introspect/create",
            _token_introspect_payload,
            ["TOKEN_INTROSPECT_ID"],
        ),
        _step("Create MCP Server", "/api_mcp_server/create", _mcp_server_payload, ["MCP_SERVER_ID"]),
        _step(
            "Check the App Agent credentials",
            None,
            None,
            ["AGENT_READY"],
            kind="settle",
        ),
    ]
    for base in base_setup:
        base["required"] = True
    steps = [
        *base_setup,
        _step(
            "Capture nodes",
            "/api_capture/create",
            _capture_payload,
            ["CAPTURED_NODES"],
            kind="capture",
        ),
        _step(
            "Capture relationships",
            "/api_relationships/create",
            _capture_payload,
            ["CAPTURED_RELATIONSHIPS"],
            kind="capture",
        ),
    ]
    for slot in KBAC_SLOTS:
        spec = kbac_for_slot(slot)
        steps.append(
            _step(
                f"Create KBAC policy {slot}: {spec.get('display_name') or spec.get('name', '')}",
                "/api_authorization_policy/create",
                lambda s=slot: _kbac_payload(s),
                [f"KBAC_POLICY_ID_{slot}"],
            ),
        )
    for pol in CIQ_POLICIES:
        slot = pol["slot"]
        steps.append(
            _step(
                f"Create CIQ policy {slot}: {pol.get('display_name') or pol.get('name', '')}",
                "/api_ciq_policy/create",
                lambda s=slot: _ciq_policy_payload(s),
                [f"CIQ_POLICY_ID_{slot}"],
            ),
        )
        steps.extend(
            _step(
                f"Create knowledge query {query['slot']}: {query.get('display_name') or query.get('name', '')}",
                "/api_ciq_knowledge_query/create",
                lambda s=query["slot"]: _ciq_query_payload(s),
                [f"CIQ_QUERY_ID_{query['slot']}"],
            )
            for query in CIQ_QUERIES
            if query["policy_slot"] == slot
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


def _assess_create(step, page_text, before):
    """Judge a create step: success iff its handler saved a NEW value for every expected key.

    Presence in .env alone is not enough: with skip-existing unchecked, a
    failed re-create (duplicate-name 409, expired SA_TOKEN) saves nothing and
    the previous run's values would otherwise report as success and mask the
    failure from the required-step abort.
    """
    saved = dotenv_values(ENV_FILE) or {}
    present = all(saved.get(key) for key in step["env_keys"])
    # "changed" includes newly-set: the App Agent recovery path legitimately
    # re-saves the same agent id while minting a fresh APP_TOKEN, and the
    # handler removes a stale APP_TOKEN when credentials fail — so all-present
    # plus at-least-one-new proves the handler saved something THIS run.
    wrote_new = any(saved.get(key) and saved.get(key) != before.get(key) for key in step["env_keys"])
    if present and wrote_new:
        return True, ", ".join(step["env_keys"]) + " saved"
    message = _extract_message(page_text)
    if present:
        return False, message or "no new value saved — .env still holds a previous run's value (check the flask log)"
    return False, message or "no ID returned: check the flask log or run this form manually"


def _purge_derived_env():
    """Drop every project-scoped .env key after a NEW project was created."""
    saved = dotenv_values(ENV_FILE) or {}
    remove_env_variables([key for key in saved if key not in _BASE_ENV_KEYS])


def _read_ikg_status(url_endpoints, sa_token, project_id):
    """Read the project's ikg_status (PENDING/ACTIVE/FAILED/...). Returns (status, error_message)."""
    try:
        response = requests.get(
            f"{url_endpoints}/configs/v1/projects/{project_id}",
            headers={"Authorization": f"Bearer {sa_token}"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        return "", str(e)[:100]
    if response.status_code >= HTTP_BAD_REQUEST:
        return "", f"project read failed with status {response.status_code}"
    try:
        body = response.json()
    except ValueError:
        return "", "invalid JSON from project read"
    # Verified snake_case live on rc (2026-08-14); tolerate camelCase for safety.
    return body.get("ikg_status") or body.get("ikgStatus") or "", ""


def _ikg_step_iter():
    """'Wait for project IKG' step: poll ikg_status until ACTIVE.

    Yields ("progress", ...) while waiting and one final ("result", (ok, detail)).
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
    """Give the fresh App Agent a moment before the first capture, and test the credentials exist.

    The platform writes the agent's API permissions into the project IKG in the
    same transaction as the agent create, so a fixed short pause is enough; the
    capture retries handle any straggling permission propagation.
    """
    if not os.getenv("APP_TOKEN"):
        yield "result", (False, "APP_TOKEN missing from env - the App Agent credentials step did not save it")
        return
    time.sleep(AGENT_SETTLE_SECONDS)
    update_env_variable("AGENT_READY", "true")
    yield "result", (True, "APP_TOKEN present - agent credentials are ready")


def _resave_agent_permissions(url_endpoints, sa_token, app_agent_id):
    """Re-save the agent's CURRENT api_permissions; return True iff the update succeeded.

    The update publishes a ConfigChangedEvent that purges the platform's cached
    CAN_ACCESS denial for the agent, so the next call re-evaluates against the
    IKG. The permissions are read back first so a backfilled agent's real
    permission set is never silently replaced by the bundled defaults.
    """
    url = f"{url_endpoints}/configs/v1/application-agents/{app_agent_id}"
    try:
        read = requests.get(url, headers={"Authorization": f"Bearer {sa_token}"}, timeout=REQUEST_TIMEOUT)
        # None = could not read the current permissions; a present-but-empty
        # list is a REAL permission set and must be re-saved as-is, never
        # silently replaced with the bundled defaults.
        permissions = None
        if HTTP_OK <= read.status_code < HTTP_MULTIPLE_CHOICES:
            try:
                body = read.json()
            except ValueError:
                body = None
            if isinstance(body, dict) and isinstance(body.get("api_permissions"), list):
                permissions = body["api_permissions"]
        if permissions is None:
            permissions = APP_AGENT_DEFAULTS.get("api_permissions", [])
        response = requests.put(
            url,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {sa_token}"},
            json={"api_permissions": permissions},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        logger.exception("Failed to re-save app agent permissions")
        return False
    logger.info("Re-saved app agent permissions: %s", response.status_code)
    return HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES


def _parse_json_line(line):
    """Parse one NDJSON line; return the event dict or None for blanks/garbage."""
    stripped = line.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except ValueError:
        return None


def _iter_ndjson_events(response):
    """Yield parsed events from a streamed NDJSON response, incrementally.

    A capture emits one chunk event per uploaded batch; reading the body
    piecewise (instead of get_data) lets the caller forward live progress to
    the provisioning stream WHILE the upload runs — keeping the browser
    connection busy and the user informed during multi-minute captures.
    """
    buffer = ""
    for raw in response.iter_encoded():
        buffer += raw.decode("utf-8", errors="replace")
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            event = _parse_json_line(line)
            if event is not None:
                yield event
    event = _parse_json_line(buffer)
    if event is not None:
        yield event


def _capture_once(client, step):
    """Replay a capture once, yielding ("progress", detail) per chunk and one ("outcome", (ok, kind, detail)).

    The worst chunk status and any pre-flight error are read straight from the
    streamed events. kind picks the retry strategy: "denial" (cached CAN_ACCESS
    denial - re-save the agent's permissions, retry shortly after), "transient"
    (evaluation errors / 5xx: wait out the server-side error cache) or None
    (not retryable, e.g. missing APP_TOKEN).
    """
    response = client.post(step["path"], data=step["payload"](), headers={"Accept": "application/x-ndjson"})
    worst = 0
    completed = 0
    percent = None
    preflight_error = None
    failure_sample = ""
    for evt in _iter_ndjson_events(response):
        if evt.get("type") == "chunk":
            completed += 1
            worst = max(worst, int(evt.get("status_code") or 0))
            percent = evt.get("percent", percent)
            if not failure_sample and evt.get("response_text"):
                failure_sample = evt["response_text"]
            label = f"uploading: {completed} chunk(s) done"
            if percent is not None:
                label += f" ({percent}%)"
            yield "progress", label
        elif evt.get("type") == "done":
            worst = max(worst, int(evt.get("status_code") or 0))
            preflight_error = evt.get("error")
    if preflight_error:
        yield "outcome", (False, None, preflight_error)
    elif completed == 0:
        yield "outcome", (False, "transient", "no chunks were processed: check the flask log")
    elif worst < HTTP_BAD_REQUEST:
        yield "outcome", (True, None, f"all {completed} chunks accepted (worst status {worst})")
    else:
        detail = f"worst chunk status {worst} across {completed} chunks"
        yield "outcome", (False, _classify_capture_failure(failure_sample, worst), detail)


def _classify_capture_failure(failure_sample, worst):
    """Pick the retry strategy for a failed capture: "denial", "transient", or None."""
    if _INSUFFICIENT_ACCESS in failure_sample:
        return "denial"
    if _FAILED_TO_EVALUATE in failure_sample or worst >= HTTP_SERVER_ERROR:
        return "transient"
    return None


def _capture_iter(client, step):
    """Run a capture with retries, yielding ("progress", detail) and one final ("result", (ok, detail)).

    Chunks are idempotent upserts, so re-running the whole capture is safe.
    Cached denials are purged with a permissions re-save and retried shortly;
    evaluation errors / 5xx (IKG still stabilizing after project creation) are
    retried only after the platform's ~1-minute error cache has expired -
    re-saving cannot heal those.
    """
    detail = "no attempt made"
    attempt = 0
    for attempt in range(1, CAPTURE_TRIES + 1):
        ok, kind = False, None
        for event, payload in _capture_once(client, step):
            if event == "progress":
                yield "progress", payload
            else:
                ok, kind, detail = payload
        if ok:
            update_env_variable(step["env_keys"][0], "true")
            suffix = "" if attempt == 1 else f" (try {attempt}/{CAPTURE_TRIES})"
            yield "result", (True, detail + suffix)
            return
        logger.warning("Capture %s try %s/%s failed: %s", step["path"], attempt, CAPTURE_TRIES, detail)
        if attempt >= CAPTURE_TRIES or kind is None:
            break
        if kind == "denial":
            resaved = _resave_agent_permissions(
                os.getenv("URL_ENDPOINTS"),
                os.getenv("SA_TOKEN"),
                os.getenv("APP_AGENT_ID"),
            )
            if not resaved:
                # The heal itself failed (stale agent id, expired SA token…):
                # retrying would just burn tries against the same denial.
                detail += " — permissions re-save failed, not retrying"
                break
            yield (
                "progress",
                (
                    f"try {attempt} hit a cached permission denial: re-saved the agent's permissions, "
                    f"retrying in {CAPTURE_RETRY_DELAY_SECONDS}s"
                ),
            )
            time.sleep(CAPTURE_RETRY_DELAY_SECONDS)
        else:
            yield (
                "progress",
                f"try {attempt} hit a transient platform error: waiting {EVAL_ERROR_RETRY_DELAY_SECONDS}s",
            )
            time.sleep(EVAL_ERROR_RETRY_DELAY_SECONDS)
    yield "result", (False, f"{detail} (after {attempt} tr{'y' if attempt == 1 else 'ies'})")


def _step_iterator(client, step):
    """Return the (progress, result)-yielding iterator for a step kind, or None for plain creates."""
    if step["kind"] == "ikg":
        return _ikg_step_iter()
    if step["kind"] == "settle":
        return _settle_iter()
    if step["kind"] == "capture":
        return _capture_iter(client, step)
    return None


def _execute_step(client, step):
    """Run one step, yielding ("substep", detail) progress and exactly one final ("result", (ok, detail))."""
    try:
        iterator = _step_iterator(client, step)
        if iterator is not None:
            for kind, payload in iterator:
                yield ("substep", payload) if kind == "progress" else ("result", payload)
            return
        before = dotenv_values(ENV_FILE) or {}
        response = client.post(step["path"], data=step["payload"]())
        yield "result", _assess_create(step, response.get_data(as_text=True), before)
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


def _skipped_event(step, skip_ids, event, counts):
    """Return the formatted skipped event for a step, or None when it must run."""
    if skip_ids is None or not all(key in skip_ids for key in step["env_keys"]):
        return None
    counts["skipped"] += 1
    keys = ", ".join(step["env_keys"])
    return _format_event({**event, "status": "skipped", "detail": f"{keys} already set"})


def _reset_derived_state(skip_ids, event):
    """Purge project-scoped .env keys after a fresh project create.

    Values derived from the previous project (IDs, APP_TOKEN, IKG/agent/capture
    flags) must not skip steps or unlock features for a project they don't
    belong to. Returns the refreshed skip_ids (None disables skipping when the
    purge failed — stale values must then not drive skip decisions) and the
    substep event describing what happened.
    """
    try:
        _purge_derived_env()
        if skip_ids is not None:
            skip_ids = {key for key, value in (dotenv_values(ENV_FILE) or {}).items() if value}
        detail = "cleared project-scoped values from a previous run"
    except Exception:
        logger.exception("Failed to purge project-scoped .env values")
        skip_ids = None
        detail = "could not clear previous project values — skip-existing disabled for the rest of the run"
    return skip_ids, _format_event({**event, "type": "substep", "detail": detail})


def _stream_steps(client, steps, skip_ids, counts, state):
    """Run the steps in order, yielding formatted NDJSON events and updating counts.

    skip_ids is the set of .env keys already saved (skip steps whose keys are
    all present), or None when the user unchecked skip-existing. Sets
    state["finished"] (after sending blocked + done events) when a required
    step fails, so the caller knows the run terminated early.
    """
    for index, step in enumerate(steps, 1):
        state["label"] = step["label"]
        event = {"type": "step", "index": index, "total": len(steps), "label": step["label"], "path": step["path"]}
        skipped = _skipped_event(step, skip_ids, event, counts)
        if skipped is not None:
            yield skipped
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
        # Everything after the base setup depends on it; a failed config or
        # wait step would only cascade into misleading downstream failures.
        if not ok and step["required"]:
            yield _format_event({"type": "blocked", "detail": f"Stopping: '{step['label']}' failed"})
            state["finished"] = True
            yield _format_event({"type": "done", "aborted": True, **counts})
            return
        if ok and step.get("resets_derived"):
            skip_ids, reset_event = _reset_derived_state(skip_ids, event)
            yield reset_event


@api_provision.post("/run", tags=[tag])
def run_provisioning():
    """Capture the data and create every KBAC, CIQ policy and query, streaming NDJSON progress."""
    skip_existing = request.form.get("skip_existing") == "true"
    client = current_app.test_client()

    def event_stream(state):
        load_dotenv(ENV_FILE, override=True)
        missing = missing_prerequisites()
        if missing:
            labels = ", ".join(label for _key, label in missing)
            yield _format_event({"type": "blocked", "detail": f"Missing from .env: {labels}"})
            state["finished"] = True
            yield _format_event({"type": "done", "aborted": True, "ok": 0, "failed": 0, "skipped": 0})
            return
        # Skip decisions come from the .env FILE, not os.environ: stale ids can
        # survive in the process environment after a project delete.
        saved_ids = {key for key, value in (dotenv_values(ENV_FILE) or {}).items() if value}
        steps = build_steps()
        yield _format_event({"type": "start", "total": len(steps)})
        counts = {"ok": 0, "failed": 0, "skipped": 0}
        yield from _stream_steps(client, steps, saved_ids if skip_existing else None, counts, state)
        if state["finished"]:  # a required step failed and already sent its done event
            return
        state["finished"] = True
        yield _format_event({"type": "done", "aborted": False, **counts})

    def guarded_stream():
        # A closed browser tab / page reload aborts the response mid-run with a
        # silent GeneratorExit at the next yield. Nothing can keep the run going
        # once the client is gone, but it must never be invisible in the log.
        state = {"finished": False, "label": "before the first step"}
        try:
            yield from event_stream(state)
        finally:
            if not state["finished"]:
                logger.warning(
                    "Provisioning stream closed before completion (client disconnected around '%s'). "
                    "Steps already completed are saved in .env — re-run with skip-existing to continue.",
                    state["label"],
                )

    return Response(
        stream_with_context(guarded_stream()),
        mimetype="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# --------------------------------------------------------------------------
# Backfill: recover the derived IDs in .env from the platform by fixed name.
# --------------------------------------------------------------------------


def _lookup(env_key, resource, name, label):
    # Config GET-by-name uses the parent project as ?location=<PROJECT_ID>
    # (required when querying by name); the list fallback uses ?project_id
    # instead. The published spec claims the MCP server read wants ?project_id
    # too, but the deployed API 422s project_id and accepts location (verified
    # 2026-07-28 on rc).
    return {"env_key": env_key, "resource": resource, "name": name, "label": label}


def _marker(env_key, label):
    # Local .env marker with no platform counterpart (capture state cannot be
    # read from the Config API): set on the premise that a backfilled sandbox
    # already holds its data.
    return {"env_key": env_key, "resource": None, "name": None, "label": label}


def build_backfill_lookups():
    """Return the ordered name→ID lookups (plus markers), mirroring the provisioning step order."""
    lookups = [
        _lookup("APPLICATION_ID", "applications", APPLICATION_DEFAULTS["name"], "Application"),
        _lookup("APP_AGENT_ID", "application-agents", APP_AGENT_DEFAULTS["name"], "App Agent"),
        _lookup("TOKEN_INTROSPECT_ID", "token-introspects", TOKEN_INTROSPECT_DEFAULTS["name"], "Token Introspect"),
        _lookup("MCP_SERVER_ID", "mcp-servers", MCP_SERVER_DEFAULTS["name"], "MCP Server"),
        _marker("CAPTURED_NODES", "Mark nodes as captured"),
        _marker("CAPTURED_RELATIONSHIPS", "Mark relationships as captured"),
    ]
    for slot in KBAC_SLOTS:
        spec = kbac_for_slot(slot)
        lookups.append(
            _lookup(f"KBAC_POLICY_ID_{slot}", "authorization-policies", spec["name"], f"KBAC policy {slot}"),
        )
    lookups.extend(
        _lookup(f"CIQ_POLICY_ID_{pol['slot']}", "authorization-policies", pol["name"], f"CIQ policy {pol['slot']}")
        for pol in CIQ_POLICIES
    )
    lookups.extend(
        _lookup(f"CIQ_QUERY_ID_{query['slot']}", "knowledge-queries", query["name"], f"Knowledge query {query['slot']}")
        for query in CIQ_QUERIES
    )
    return lookups


def missing_backfill_prerequisites():
    """Return the BACKFILL_PREREQUISITES entries whose .env key is absent or empty."""
    saved = {key for key, value in (dotenv_values(ENV_FILE) or {}).items() if value}
    return [entry for entry in BACKFILL_PREREQUISITES if entry[0] not in saved]


# Console-created sandboxes (Hub "create sandbox") deploy the same music
# templates but suffix every name: policies/queries become
# "<base>-<6 digits>-<index>", and the singletons are named "app-<6 digits>",
# "agent-<6 digits>", "token-introspect-<6 digits>" (no MCP server is created
# at all). When the fixed-name read misses, the fallback lists the project's
# configs once per type and matches these patterns.
_CONSOLE_SUFFIX = r"-\d{6}-\d+$"
_CONSOLE_SINGLETON_PATTERNS = {
    "applications": re.compile(r"^app-\d{6}$"),
    "application-agents": re.compile(r"^agent-\d{6}$"),
    "token-introspects": re.compile(r"^token-introspect-\d{6}$"),
    "mcp-servers": None,
}


def _list_configs(resource, cache):
    """List the project's configs of one type (cached per run); [] on any error."""
    if resource not in cache:
        url = f"{os.getenv('URL_ENDPOINTS', '')}/configs/v1/{resource}"
        entries = []
        try:
            response = requests.get(
                url,
                params={"project_id": os.getenv("PROJECT_ID", "")},
                headers={"Authorization": f"Bearer {os.getenv('SA_TOKEN', '')}"},
                timeout=REQUEST_TIMEOUT,
            )
            if HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES:
                data = response.json().get("data")
                entries = data if isinstance(data, list) else []
            else:
                logger.warning("Listing %s returned status %s", resource, response.status_code)
        except (requests.exceptions.RequestException, ValueError):
            logger.exception("Listing %s failed", resource)
        cache[resource] = entries
    return cache[resource]


def _console_candidates(lookup, entries):
    """Return the listing entries matching console naming for this lookup.

    Suffixed base name first; for singleton types, fall back to the console
    singleton pattern, then to a lone entry (unambiguous whatever its name).
    """
    pattern = re.compile(f"^{re.escape(lookup['name'])}{_CONSOLE_SUFFIX}")
    matches = [e for e in entries if pattern.match(e.get("name") or "")]
    if matches or lookup["resource"] not in _CONSOLE_SINGLETON_PATTERNS:
        return matches
    singleton = _CONSOLE_SINGLETON_PATTERNS[lookup["resource"]]
    if singleton:
        matches = [e for e in entries if singleton.match(e.get("name") or "")]
    if not matches and len(entries) == 1:
        return entries
    return matches


def _match_console_name(lookup, cache):
    """Try to resolve a lookup against console-sandbox naming: (status, detail, id) or None.

    Returns None when the listing holds no plausible match: the caller keeps
    the fixed-name "missing" outcome.
    """
    matches = _console_candidates(lookup, _list_configs(lookup["resource"], cache))
    if not matches:
        return None
    if len(matches) > 1:
        names = ", ".join(sorted(e.get("name") or "?" for e in matches))
        return "failed", f"ambiguous: several configs match: {names}", None
    matched = matches[0]
    if not matched.get("id"):
        return "failed", f"matched {matched.get('name')} but the listing entry has no id", None
    return "ok", f"matched console-sandbox config {matched.get('name')}", matched["id"]


def _fetch_config_id(lookup):
    """Resolve one config's ID by name: ("ok"|"missing"|"failed", detail, id|None)."""
    url = f"{os.getenv('URL_ENDPOINTS', '')}/configs/v1/{lookup['resource']}/{quote(lookup['name'], safe='')}"
    try:
        response = requests.get(
            url,
            params={"location": os.getenv("PROJECT_ID", "")},
            headers={"Authorization": f"Bearer {os.getenv('SA_TOKEN', '')}"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        return "failed", str(exc), None
    if response.status_code == HTTP_NOT_FOUND:
        return "missing", "not found on the platform: create it with Provision Everything", None
    if not HTTP_OK <= response.status_code < HTTP_MULTIPLE_CHOICES:
        message = _extract_message(response.text) or response.text[:200]
        return "failed", f"status {response.status_code}: {message}", None
    try:
        body = response.json()
    except ValueError:
        return "failed", "response was not JSON", None
    # Reads return the config object directly; tolerate a list-shaped wrapper too.
    config_id = body.get("id") if isinstance(body, dict) else None
    if not config_id and isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            config_id = data[0].get("id")
    if not config_id:
        return "failed", "no id in the response", None
    return "ok", config_id, config_id


def _resolve_lookup(lookup, listing_cache):
    """Resolve one lookup: fixed-name read first, console-listing fallback on a miss."""
    status, detail, config_id = _fetch_config_id(lookup)
    if status == "ok":
        return "ok", f"{lookup['env_key']} saved", config_id
    if status == "missing":
        # Console-created sandboxes suffix every name: retry against the
        # project listing before reporting the config as absent.
        fallback = _match_console_name(lookup, listing_cache)
        if fallback:
            status, fallback_detail, config_id = fallback
            detail = f"{lookup['env_key']} saved ({fallback_detail})" if status == "ok" else fallback_detail
    return status, detail, config_id


@api_provision.get("/backfill", tags=[tag])
def show_backfill_form():
    """Display the backfill page with the prerequisite checklist and lookup list."""
    load_dotenv(ENV_FILE, override=True)
    missing = missing_backfill_prerequisites()
    return render_template(
        "provision/backfill_form.html",
        lookups=build_backfill_lookups(),
        prerequisites=BACKFILL_PREREQUISITES,
        missing_keys={entry[0] for entry in missing},
        ready=not missing,
    )


@api_provision.post("/backfill", tags=[tag])
def run_backfill():
    """Recover every derived ID from the platform by name, streaming NDJSON progress."""
    skip_existing = request.form.get("skip_existing") == "true"

    def event_stream():
        load_dotenv(ENV_FILE, override=True)
        missing = missing_backfill_prerequisites()
        if missing:
            labels = ", ".join(label for _key, label in missing)
            yield _format_event({"type": "blocked", "detail": f"Missing from .env: {labels}"})
            yield _format_event({"type": "done", "aborted": True, "ok": 0, "failed": 0, "skipped": 0, "missing": 0})
            return
        saved_ids = {key for key, value in (dotenv_values(ENV_FILE) or {}).items() if value}
        lookups = build_backfill_lookups()
        yield _format_event({"type": "start", "total": len(lookups)})
        counts = {"ok": 0, "failed": 0, "skipped": 0, "missing": 0}
        listing_cache = {}
        for index, lookup in enumerate(lookups, 1):
            label = f"{lookup['label']}: {lookup['name']}" if lookup["name"] else lookup["label"]
            event = {
                "type": "step",
                "index": index,
                "total": len(lookups),
                "label": label,
                "path": lookup["env_key"],
            }
            if skip_existing and lookup["env_key"] in saved_ids:
                counts["skipped"] += 1
                yield _format_event({**event, "status": "skipped", "detail": f"{lookup['env_key']} already set"})
                continue
            if lookup["resource"] is None:
                update_env_variable(lookup["env_key"], "true")
                counts["ok"] += 1
                detail = f"{lookup['env_key']}=true (not readable via the Config API: assumed already captured)"
                yield _format_event({**event, "status": "ok", "detail": detail})
                continue
            status, detail, config_id = _resolve_lookup(lookup, listing_cache)
            if status == "ok":
                update_env_variable(lookup["env_key"], config_id)
            counts[status] += 1
            yield _format_event({**event, "status": status, "detail": detail})
        yield _format_event({"type": "done", "aborted": False, **counts})

    return Response(
        stream_with_context(event_stream()),
        mimetype="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
