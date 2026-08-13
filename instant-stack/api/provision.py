"""Provision-everything add-on: replay every create button AFTER the MCP server config.

The five Getting Started configurations (Project, Application, App Agent,
Token Introspect, MCP Server) are assumed to exist already - their IDs and
tokens are read from .env. The run replays everything that follows them on the
landing page, in click order: both captures (nodes + relationships), the KBAC
policy, the three external data resolvers, then each CIQ policy immediately
followed by its knowledge query (slots 1-10). Each step POSTs the exact form
payload the corresponding create form would submit, through the Flask test
client, so it runs the same route handlers as a person clicking the buttons.

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

# Environment values the run cannot create itself (they come from the five
# Getting Started steps), in checklist order.
PREREQUISITES = [
    ("URL_ENDPOINTS", "Platform API base URL (e.g. https://eu.api.indykite.com)"),
    ("SA_TOKEN", "Service-account token"),
    ("PROJECT_ID", "Project ID (Getting Started step 1)"),
    ("APP_TOKEN", "App Agent token (Getting Started step 3)"),
]
OPTIONAL_KEYS = []


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


# --------------------------------------------------------------------------
# Step list - the landing-page buttons after the MCP server config, in order.
# --------------------------------------------------------------------------


def _step(label, path, payload, env_keys, kind="create"):
    return {"label": label, "path": path, "payload": payload, "env_keys": env_keys, "kind": kind}


def build_steps():
    """Return the ordered steps: captures, KBAC, resolvers, then CIQ policy+query pairs."""
    steps = [
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
