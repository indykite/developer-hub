"""Provision-everything add-on: replay every create button AFTER the MCP server config.

The five Getting Started configurations (Project, Application, App Agent,
Token Introspect, MCP Server) are assumed to exist already — their IDs and
tokens are read from .env. The run replays everything that follows them on the
landing page, in click order: both captures (nodes + relationships), the ten
KBAC policies, then each CIQ policy immediately followed by its knowledge
queries. Each step POSTs the exact form payload the corresponding create form
would submit, through the Flask test client, so it runs the same route
handlers as a person clicking the buttons.

Captures on a freshly created project/agent can hit a transient 401, "failed to
evaluate API access" (the evaluation errored while the IKG was still stabilizing,
the error is cached for ~1 minute, so the retry waits past that TTL). The
captures are idempotent upserts, so the whole upload is safe to retry. A cached
CAN_ACCESS denial ("insufficient API access level") is treated as non-retryable
and surfaced immediately.

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
from api._music_data import (
    APP_AGENT_DEFAULTS,
    APPLICATION_DEFAULTS,
    CIQ_POLICIES,
    CIQ_QUERIES,
    KBAC_SLOTS,
    MCP_SERVER_DEFAULTS,
    TOKEN_INTROSPECT_DEFAULTS,
    kbac_for_slot,
)
from api.authorization_policy import _default_for_slot as _kbac_default_for_slot
from api.ciq_knowledge_query import _default_for_slot as _query_default_for_slot
from api.ciq_policy import _default_for_slot as _policy_default_for_slot
from api.project import update_env_variable
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

# Captures retry the whole (idempotent) upload on a transient evaluation error:
# see the module docstring.
CAPTURE_TRIES = 4
EVAL_ERROR_RETRY_DELAY_SECONDS = 70
_FAILED_TO_EVALUATE = "failed to evaluate API access"

# Environment values the run cannot create itself (they come from the five
# Getting Started steps), in checklist order.
PREREQUISITES = [
    ("URL_ENDPOINTS", "Platform API base URL (e.g. https://eu.api.indykite.com)"),
    ("SA_TOKEN", "Service-account token"),
    ("PROJECT_ID", "Project ID (Getting Started step 1)"),
    ("APP_TOKEN", "App Agent token (Getting Started step 3)"),
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
# Step list: the landing-page buttons after the MCP server config, in order.
# --------------------------------------------------------------------------


def _step(label, path, payload, env_keys, kind="create"):
    return {"label": label, "path": path, "payload": payload, "env_keys": env_keys, "kind": kind}


def build_steps():
    """Return the ordered steps: captures, KBACs, then CIQ policy+query groups."""
    steps = [
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


def _assess_create(step, page_text):
    """Judge a create step: it succeeded iff its handler saved the expected IDs to .env."""
    saved = dotenv_values(ENV_FILE) or {}
    if all(saved.get(key) for key in step["env_keys"]):
        return True, ", ".join(step["env_keys"]) + " saved"
    message = _extract_message(page_text)
    return False, message or "no ID returned: check the flask log or run this form manually"


def _assess_capture_stream(raw_text):
    """Judge a capture NDJSON stream: (ok, failure_kind, detail).

    The capture routes stream one JSON event per chunk plus a final "done"
    event, so the worst chunk status and any pre-flight error are read straight
    from the events. failure_kind picks the retry strategy: "transient"
    (evaluation errors / 5xx: wait out the server-side error cache) or None
    (not retryable, e.g. an access denial or missing APP_TOKEN).
    """
    worst = 0
    completed = 0
    preflight_error = None
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except ValueError:
            continue
        if evt.get("type") == "chunk":
            completed += 1
            worst = max(worst, int(evt.get("status_code") or 0))
        elif evt.get("type") == "done":
            worst = max(worst, int(evt.get("status_code") or 0))
            preflight_error = evt.get("error")
    if preflight_error:
        return False, None, preflight_error
    if completed == 0:
        return False, "transient", "no chunks were processed: check the flask log"
    if worst < HTTP_BAD_REQUEST:
        return True, None, f"all {completed} chunks accepted (worst status {worst})"
    detail = f"worst chunk status {worst} across {completed} chunks"
    if _FAILED_TO_EVALUATE in raw_text or worst >= HTTP_SERVER_ERROR:
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
        response = client.post(
            step["path"],
            data=step["payload"](),
            headers={"Accept": "application/x-ndjson"},
        )
        ok, kind, detail = _assess_capture_stream(response.get_data(as_text=True))
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
            f"try {attempt} hit a transient platform error: waiting {EVAL_ERROR_RETRY_DELAY_SECONDS}s",
        )
        time.sleep(EVAL_ERROR_RETRY_DELAY_SECONDS)
    yield "result", (False, f"{detail} (after {attempt} tr{'y' if attempt == 1 else 'ies'})")


def _execute_step(client, step):
    """Run one step, yielding ("substep", detail) progress and exactly one final ("result", (ok, detail))."""
    try:
        if step["kind"] == "capture":
            for kind, payload in _capture_iter(client, step):
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
    """Capture the data and create every KBAC, CIQ policy and query, streaming NDJSON progress."""
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
