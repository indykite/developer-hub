# Copyright (c) 2026 IndyKite
"""Audit Trail: read, verify and tamper-test the project's signed audit chain.

Reads the Audit Log API (``/audit/v1/logs``, ``/manifests``, ``/checkpoints``)
with the App Agent credential - it needs the ``Audit`` API permission - plus the
public ``/audit/.well-known/jwks.json``, and verifies everything locally with
api.audit_verify. Each consistent fetch is kept under a snapshot id that the
page sends back: tamper actions run on a copy of that snapshot, and the export
writes its raw pages in the layout the public ``audit-verify.py`` reads.

What is chained: platform events that the project's event sink delivered while
an audit-signing configuration existed. Agent Gateway and Token Service audit
records are written to their own file or webhook and are not part of it.
"""

import io
import json
import logging
import os
import threading
import time
import uuid
import zipfile
from collections import OrderedDict
from datetime import UTC, datetime

import requests
from api import audit_verify
from flask import jsonify, render_template, request, send_file
from flask_openapi3 import APIBlueprint, Tag

tag = Tag(name="audit", description="Tamper-proof audit trail")

logger = logging.getLogger(__name__)

api_audit = APIBlueprint(
    "audit",
    __name__,
    url_prefix="/audit",
    abp_tags=[tag],
    doc_ui=True,
)

HTTP_OK = 200
PAGE_SIZE = 50
# A runaway cursor must not loop forever: 200 pages of 50 is far beyond a demo.
MAX_PAGES = 200

# Node the "add activity" button upserts, so a new batch appears in the chain.
PROBE_NODE_TYPE = "AuditProbe"
PROBE_NODE_ID = "audit-probe"

# The chain grows while it is read (batches are cut every few seconds), so one
# read of the three endpoints can catch a batch whose manifest was not read yet.
# Re-read the whole snapshot this many times before reporting what was read.
SNAPSHOT_TRIES = 3
SNAPSHOT_RETRY_DELAY_SECONDS = 1

# Consistent fetches (raw pages + parsed chain) by snapshot id, newest last, so
# tamper/export work on exactly what that page displayed - not on another tab's
# or another project's load. Every JWK Set seen is kept per project, so a key
# rotated out of the JWKS between fetches still verifies older records - and a
# key served for one project never verifies another project's records.
MAX_SNAPSHOTS = 5
_state = {"snapshots": OrderedDict(), "jwks_by_project": {}}
_state_lock = threading.Lock()


class AuditReadError(Exception):
    pass


def _get(path, params=None, *, auth=True):
    headers = {"Accept": "application/json"}
    if auth:
        headers["X-IK-ClientKey"] = os.getenv("APP_TOKEN", "")
    response = requests.get(f"{os.getenv('URL_ENDPOINTS')}{path}", headers=headers, params=params, timeout=30)
    if response.status_code != HTTP_OK:
        # An error must stop the paging, not end it: a 401 body taken for the
        # last page would silently truncate the chain.
        message = response.text[:300]
        if response.status_code == 401:  # noqa: PLR2004
            message += " - the App Agent needs the Audit API permission (granted asynchronously after it is added)"
        msg = f"GET {path} returned {response.status_code}: {message}"
        raise AuditReadError(msg)
    # Decoded as UTF-8 explicitly, not by charset guessing: the batch hash is
    # over the served bytes, which the verifier re-encodes as UTF-8.
    return response.content.decode("utf-8")


def _fetch_pages(endpoint, project_id):
    pages = []
    cursor = ""
    for _ in range(MAX_PAGES):
        params = {"project_id": project_id, "pagesize": PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor
        text = _get(f"/audit/v1/{endpoint}", params)
        pages.append(text)
        envelope = json.loads(text)
        if not envelope.get("has_more"):
            return pages
        cursor = envelope.get("next_cursor", "")
    msg = f"{endpoint}: more than {MAX_PAGES} pages"
    raise AuditReadError(msg)


def _remember_jwks(project_id, jwks):
    with _state_lock:
        sets = _state["jwks_by_project"].setdefault(project_id, [])
        if jwks not in sets:
            sets.append(jwks)
        return list(sets)


def _newest_sequence(texts):
    return max((item["sequence"] for text in texts for item in json.loads(text).get("items", [])), default=0)


def _fetch_snapshot(project_id):
    """Read checkpoints, manifests and logs until their newest sequences agree.

    Read in that order, a checkpoint is never newer than the manifests read
    after it, and only the logs can be ahead. Returns (pages, settled, detail):
    settled is False when the sequences still disagreed after SNAPSHOT_TRIES
    reads, and detail names them.
    """
    detail = ""
    for attempt in range(1, SNAPSHOT_TRIES + 1):
        pages = {name: _fetch_pages(name, project_id) for name in ("checkpoints", "manifests", "logs")}
        newest = {name: _newest_sequence(texts) for name, texts in pages.items()}
        if newest["manifests"] == newest["logs"] and newest["checkpoints"] <= newest["manifests"]:
            return pages, True, ""
        detail = ", ".join(f"{name} {seq}" for name, seq in newest.items())
        logger.info("Audit snapshot read %s/%s not settled (%s)", attempt, SNAPSHOT_TRIES, detail)
        if attempt < SNAPSHOT_TRIES:
            time.sleep(SNAPSHOT_RETRY_DELAY_SECONDS)
    return pages, False, detail


def _store_snapshot(project_id, pages, parsed_chain, fetched_at):
    """Keep a consistent fetch under a new snapshot id (the oldest beyond MAX_SNAPSHOTS is dropped)."""
    snapshot_id = uuid.uuid4().hex
    with _state_lock:
        snapshots = _state["snapshots"]
        snapshots[snapshot_id] = {
            "project_id": project_id,
            "pages": pages,
            "chain": parsed_chain,
            "fetched_at": fetched_at,
        }
        while len(snapshots) > MAX_SNAPSHOTS:
            snapshots.popitem(last=False)
    return snapshot_id


def _snapshot_for(snapshot_id):
    """Return (snapshot, None), or (None, error response) when it is unknown or not the current project's."""
    with _state_lock:
        snapshot = _state["snapshots"].get(snapshot_id or "")
    if snapshot is None:
        return None, (jsonify({"error": "unknown or expired snapshot - load the chain again"}), 409)
    current = os.getenv("PROJECT_ID", "")
    if snapshot["project_id"] != current:
        message = f"this snapshot is project {snapshot['project_id']}, the current project is {current} - load again"
        return None, (jsonify({"error": message}), 409)
    return snapshot, None


def _report(parsed_chain, pagination_failures=()):
    report = audit_verify.verify(parsed_chain)
    if pagination_failures:
        report["failures"] = list(pagination_failures) + report["failures"]
        report["ok"] = False
    return report


@api_audit.get("/", tags=[tag])
def view():
    """Render the Audit Trail page; the chain is loaded from /audit/chain."""
    return render_template(
        "audit/view.html",
        project_id=os.getenv("PROJECT_ID", ""),
        has_app_token=bool(os.getenv("APP_TOKEN")),
        has_audit_signing=bool(os.getenv("AUDIT_SIGNING_ID")),
        has_event_sink=bool(os.getenv("EVENT_SINK_ID")),
    )


@api_audit.get("/chain", tags=[tag])
def chain():
    """Fetch every page of the chain plus the JWKS, verify it, and return the report."""
    project_id = os.getenv("PROJECT_ID", "")
    if not project_id or not os.getenv("APP_TOKEN"):
        return jsonify({"error": "PROJECT_ID and APP_TOKEN must be set - provision the project first"}), 400
    try:
        jwks = _get("/audit/.well-known/jwks.json", {"project_id": project_id}, auth=False)
        jwks_sets = _remember_jwks(project_id, json.loads(jwks))
        pages, settled, unsettled_detail = _fetch_snapshot(project_id)
    except (AuditReadError, requests.RequestException, ValueError) as e:
        logger.warning("Audit chain read failed: %s", e)
        return jsonify({"error": str(e)}), 502

    pagination_failures = [
        f"{name} {problem}" for name, texts in pages.items() for problem in audit_verify.check_pages(texts)
    ]
    if not settled:
        pagination_failures.insert(
            0,
            f"the newest sequences still differed after {SNAPSHOT_TRIES} reads ({unsettled_detail}) - "
            "a tail mismatch below may be a batch appended while reading; load again",
        )
    parsed = audit_verify.build_chain(pages, jwks_sets, project_id)
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    # Only a consistent read backs the tamper actions and the export; the page
    # sends its snapshot_id back with them.
    snapshot_id = _store_snapshot(project_id, pages, parsed, fetched_at) if settled else None

    # The sink status is read once when the page opens (/api_event_sink/status),
    # not here: that Config API read is itself an audited event, so reading it
    # on every load would add a batch to the chain each time.
    report = _report(parsed, pagination_failures)
    report["fetched_at"] = fetched_at
    report["snapshot_id"] = snapshot_id
    return jsonify(report)


@api_audit.post("/tamper", tags=[tag])
def tamper():
    """Verify a tampered copy of the page's snapshot; the real chain is untouched."""
    body = request.get_json(silent=True) or {}
    snapshot, error = _snapshot_for(body.get("snapshot_id"))
    if error:
        return error
    try:
        sequence = int(body.get("sequence", 1))
    except (TypeError, ValueError):
        return jsonify({"error": "sequence must be a number"}), 400
    forged, description = audit_verify.tamper(snapshot["chain"], body.get("action", ""), sequence)
    report = _report(forged)
    report["tamper"] = description
    return jsonify(report)


@api_audit.post("/poke", tags=[tag])
def poke():
    """Upsert one probe node through Capture so new audit events enter the chain."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    body = {
        "nodes": [
            {
                "external_id": PROBE_NODE_ID,
                "type": PROBE_NODE_TYPE,
                "properties": [{"type": "poked_at", "value": now}],
            },
        ],
    }
    response = requests.put(
        f"{os.getenv('URL_ENDPOINTS')}/capture/v1/nodes",
        headers={"Content-Type": "application/json", "X-IK-ClientKey": os.getenv("APP_TOKEN", "")},
        json=body,
        timeout=30,
    )
    logger.info("Audit probe upsert status: %s", response.status_code)
    try:
        response_json = response.json()
    except ValueError:
        response_json = {"response_text": response.text[:300]}
    return jsonify({"status": response.status_code, "poked_at": now, "response": response_json}), (
        HTTP_OK if response.ok else 502
    )


@api_audit.get("/export", tags=[tag])
def export():
    """Download the page's snapshot as a zip that the public audit-verify.py checks unchanged."""
    snapshot, error = _snapshot_for(request.args.get("snapshot"))
    if error:
        return error
    pages, fetched_at = snapshot["pages"], snapshot["fetched_at"]
    # The key sets the snapshot was verified with.
    jwks_sets = list(snapshot["chain"]["jwks"])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for n, jwks in enumerate(jwks_sets):
            archive.writestr(
                "jwks.json" if n == len(jwks_sets) - 1 else f"jwks-{n + 1:02d}.json",
                json.dumps(jwks, indent=2),
            )
        for name, texts in pages.items():
            for n, text in enumerate(texts, 1):
                # Written byte for byte: the batch hash covers the data array as served.
                archive.writestr(f"{name}/page-{n:04d}.json", text)
    buffer.seek(0)
    stamp = fetched_at.replace(":", "").replace("-", "")
    return send_file(buffer, mimetype="application/zip", as_attachment=True, download_name=f"audit-export-{stamp}.zip")
