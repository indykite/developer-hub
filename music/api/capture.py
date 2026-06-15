import concurrent.futures
import json
import logging
import os
import time
from pathlib import Path

import ijson
import requests
from dotenv import load_dotenv
from flask import Response, render_template, request, stream_with_context
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_capture", description="Capture")
security = [{"ApiKeyAuth": []}]

logger = logging.getLogger(__name__)


class Unauthorized(BaseModel):
    code: int = Field(-1, description="Status Code")
    message: str = Field("Unauthorized!", description="Exception Information")


api_capture = APIBlueprint(
    "api_capture",
    __name__,
    url_prefix="/api_capture",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)

NODES_FILE = Path(__file__).parent.parent / "data" / "nodes" / "nodes_music.json"
ENV_FILE = Path(__file__).parent.parent / ".env"
CHUNK_SIZE = 200
MAX_WORKERS = 10
# Cap chunks held in memory at once so a multi-GB nodes file stays flat in RAM.
# At CHUNK_SIZE=200 this is at most MAX_IN_FLIGHT * 200 nodes resident.
MAX_IN_FLIGHT = MAX_WORKERS * 4
REQUEST_TIMEOUT = 120  # seconds per chunk
RETRY_ATTEMPTS = 3  # total attempts (initial + retries) per chunk
RETRY_BACKOFF = 2.0  # seconds, doubled each retry

# HTTP status constants (avoid magic numbers in comparisons).
HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_REQUEST_TIMEOUT = 408
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR_MIN = 500
HTTP_SERVER_ERROR_MAX = 600
HTTP_CLIENT_TIMEOUT = 599  # local marker for an exhausted-retries timeout

_PREVIEW_NODE_COUNT = 5
_APP_AGENT_HELP = (
    "APP_TOKEN is not set (checked {env}). Create an App Agent for this Music project "
    "(/api_app_agent/create) — the credentials response stores APP_TOKEN automatically. "
    "If APP_TOKEN is already in .env, restart `flask run`."
)
_NO_URL_MSG = "URL_ENDPOINTS is not set in .env (e.g. https://eu.api.indykite.com)."


def _preview_file_nodes(n):
    """Return the first n nodes from NODES_FILE without loading the whole file.

    The bundled file can be several GB / millions of nodes, so json.load would
    balloon to tens of GB of Python objects and freeze the machine. ijson reads
    incrementally and we stop after n items, touching only the start of the file.
    """
    out = []
    try:
        with NODES_FILE.open("rb") as f:
            for node in ijson.items(f, "nodes.item", use_float=True):
                out.append(node)
                if len(out) >= n:
                    break
    except (FileNotFoundError, ijson.JSONError) as e:
        logger.warning("Could not preview nodes_music.json: %s", e)
    return out


def _iter_file_node_chunks(chunk_size):
    """Yield (chunk, fraction) pairs of up to chunk_size nodes streamed from NODES_FILE.

    Never holds more than one chunk in memory, so the file size is irrelevant to
    RAM usage — this is what lets a multi-GB file be captured without freezing.

    fraction is the share (0..1) of the file read when this chunk was produced; reporting it
    when the chunk completes lets the progress bar show a real percentage from this single
    pass — the file is never read a second time just to count it.
    """
    file_size = NODES_FILE.stat().st_size or 1
    with NODES_FILE.open("rb") as f:
        chunk = []
        for node in ijson.items(f, "nodes.item", use_float=True):
            chunk.append(node)
            if len(chunk) >= chunk_size:
                yield chunk, f.tell() / file_size
                chunk = []
        if chunk:
            yield chunk, f.tell() / file_size


@api_capture.get("/create", tags=[tag])
def show_create_form():
    """Display the capture form with a preview of the music node defaults."""
    preview_nodes = _preview_file_nodes(_PREVIEW_NODE_COUNT)
    preview = {"nodes": preview_nodes}
    return render_template(
        "capture/create_form.html",
        # Exact count would require a full multi-GB scan on every page load; the
        # file is streamed at capture time, so the total is reported as it runs.
        nodes_count=None,
        preview_count=len(preview_nodes),
        preview_json=json.dumps(preview, indent=2),
        nodes_file=str(NODES_FILE.relative_to(NODES_FILE.parent.parent.parent)),
    )


def _chunk_list(lst, chunk_size):
    total = len(lst) or 1
    for i in range(0, len(lst), chunk_size):
        chunk = lst[i : i + chunk_size]
        yield chunk, min(1.0, (i + len(chunk)) / total)


def _is_retryable_status(status: int) -> bool:
    if status in {HTTP_REQUEST_TIMEOUT, HTTP_TOO_MANY_REQUESTS}:
        return True
    return HTTP_SERVER_ERROR_MIN <= status < HTTP_SERVER_ERROR_MAX


def _make_process_chunk(api_url: str, app_token: str):
    """Build a chunk processor that PUTs a node chunk, retrying on transient errors."""

    def process_chunk(index, chunk):
        last_exception = None
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            logger.info("Processing chunk %s with %s nodes (attempt %s/%s)", index, len(chunk), attempt, RETRY_ATTEMPTS)
            try:
                response = requests.put(
                    api_url,
                    headers={"Content-Type": "application/json", "X-IK-ClientKey": app_token},
                    json={"nodes": chunk},
                    timeout=REQUEST_TIMEOUT,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last_exception = e
                logger.warning("Chunk %s attempt %s failed: %s", index, attempt, e)
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF * (2 ** (attempt - 1)))
                    continue
                return {
                    "chunk_index": index,
                    "status_code": HTTP_CLIENT_TIMEOUT,
                    "response_json": {"message": str(e)},
                    "response_text": f"After {RETRY_ATTEMPTS} attempts: {e}",
                }

            if _is_retryable_status(response.status_code) and attempt < RETRY_ATTEMPTS:
                logger.warning("Chunk %s attempt %s got retryable status %s", index, attempt, response.status_code)
                time.sleep(RETRY_BACKOFF * (2 ** (attempt - 1)))
                continue

            try:
                response_json = response.json()
            except ValueError:
                response_json = {"message": "Invalid JSON response", "status": response.status_code}
            logger.info("Chunk %s response status: %s", index, response.status_code)
            return {
                "chunk_index": index,
                "status_code": response.status_code,
                "response_json": response_json,
                "response_text": response.text[:500] if response.text else "",
            }

        return {
            "chunk_index": index,
            "status_code": HTTP_CLIENT_TIMEOUT,
            "response_json": {"message": str(last_exception or "Unknown error")},
            "response_text": str(last_exception or "Unknown error"),
        }

    return process_chunk


def _load_payload_from_form():
    """Return (descriptor, error_html_response | None) describing what to capture.

    descriptor is {"source": "file"} for the bundled multi-GB file (streamed off
    disk so it never lands in memory) or {"source": "pasted", "nodes": [...]} for a
    small JSON payload typed into the form.
    """
    use_defaults = request.form.get("use_defaults") == "true"
    if use_defaults:
        logger.info("Streaming nodes from disk: %s", NODES_FILE)
        return {"source": "file"}, None
    try:
        nodes_json = request.form.get("nodes", "{}")
        data = json.loads(nodes_json)
        nodes_list = data if isinstance(data, list) else data.get("nodes", [])
    except json.JSONDecodeError as e:
        logger.exception("Failed to parse nodes JSON")
        error = render_template(
            "capture/result.html",
            response_json={"message": f"Invalid JSON: {e!s}"},
            status_code=HTTP_BAD_REQUEST,
        )
        return None, error
    else:
        return {"source": "pasted", "nodes": nodes_list}, None


def _error_response(wants_stream, msg, status):
    """Report a pre-flight error either as a single NDJSON 'done' event or the result page."""
    logger.error(msg)
    if wants_stream:
        return Response(
            json.dumps({"type": "done", "status_code": status, "error": msg, "results": []}) + "\n",
            mimetype="application/x-ndjson",
        )
    return render_template("capture/result.html", response_json={"message": msg}, status_code=status)


def _resolve_env(wants_stream):
    """Re-read .env and return ((url_endpoints, app_token), None) or (None, error_response)."""
    load_dotenv(ENV_FILE, override=True)
    url_endpoints = os.getenv("URL_ENDPOINTS")
    app_token = os.getenv("APP_TOKEN")
    if not app_token:
        return None, _error_response(wants_stream, _APP_AGENT_HELP.format(env=ENV_FILE), HTTP_BAD_REQUEST)
    if not url_endpoints:
        return None, _error_response(wants_stream, _NO_URL_MSG, HTTP_BAD_REQUEST)
    return (url_endpoints, app_token), None


def _iter_results_bounded(chunk_iter, process_chunk):
    """Yield (index, result) per chunk as it completes, capping in-flight chunks.

    Run process_chunk over a (possibly lazy) chunk iterator while keeping at most
    MAX_IN_FLIGHT chunks in memory. Submitting every chunk up front (the old
    `list(_chunk_list(...))` + submit-all approach) would pull the entire file into RAM via
    the pending futures. Here we pull chunks from the iterator only as worker slots free up,
    so memory stays flat no matter how large the source file is.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for index, (chunk, frac) in enumerate(chunk_iter):
            if len(futures) >= MAX_IN_FLIGHT:
                done, _ = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
                for fut in done:
                    yield futures.pop(fut), fut.result()
            futures[executor.submit(process_chunk, index, chunk)] = (index, frac)
        for fut in concurrent.futures.as_completed(list(futures)):
            yield futures.pop(fut), fut.result()


def _stream_response(chunk_iter, process_chunk, total_nodes, total_chunks):
    """Run the chunks concurrently and stream NDJSON progress events for the progress bar.

    Each chunk event carries a server-computed `percent` (0..100) so the bar is always
    determinate: for pasted JSON it comes from the chunk count, and for the streamed file it
    comes from the bytes read so far — no separate counting pass. total_nodes / total_chunks are
    None for the streamed file (its item count is unknown without a full scan) and only label the
    start event.
    """

    def event_stream():
        yield json.dumps({"type": "start", "total_chunks": total_chunks, "total_nodes": total_nodes}) + "\n"
        results = []
        last_status_code = HTTP_OK
        completed = 0
        percent = 0
        for (index, frac), result in _iter_results_bounded(chunk_iter, process_chunk):
            completed += 1
            # max() keeps the bar monotonic even if chunks complete slightly out of order.
            percent = max(percent, round(frac * 100))
            results.append(result["response_json"])
            last_status_code = result["status_code"]
            evt = {
                "type": "chunk",
                "completed": completed,
                "total": total_chunks,
                "percent": percent,
                "chunk_index": index,
                "status_code": result["status_code"],
            }
            if result["status_code"] >= HTTP_BAD_REQUEST:
                evt["response_text"] = result.get("response_text", "")
            yield json.dumps(evt) + "\n"
        yield (
            json.dumps({"type": "done", "status_code": last_status_code, "results": results, "completed": completed})
            + "\n"
        )

    return Response(
        stream_with_context(event_stream()),
        mimetype="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


def _render_result(chunk_iter, process_chunk):
    """Run the chunks concurrently and render the result page on completion (no-JS fallback)."""
    results = []
    last_status_code = HTTP_OK
    for (_index, _frac), result in _iter_results_bounded(chunk_iter, process_chunk):
        results.append(result["response_json"])
        last_status_code = result["status_code"]
    return render_template("capture/result.html", response_json=results, status_code=last_status_code)


@api_capture.post("/create", tags=[tag])
def create_capture():
    """Capture nodes from the bundled file or pasted JSON.

    Streams NDJSON progress events when the client accepts application/x-ndjson (driving the
    progress bar); otherwise renders the result page (kept for non-JS submissions).
    """
    descriptor, error_html = _load_payload_from_form()
    if error_html is not None:
        return error_html

    wants_stream = "application/x-ndjson" in request.headers.get("Accept", "")
    env, error = _resolve_env(wants_stream)
    if error is not None:
        return error
    url_endpoints, app_token = env
    process_chunk = _make_process_chunk(f"{url_endpoints}/capture/v1/nodes", app_token)

    if descriptor["source"] == "file":
        # Lazily streamed off disk — totals are unknown without a full multi-GB scan.
        chunk_iter = _iter_file_node_chunks(CHUNK_SIZE)
        total_nodes = total_chunks = None
        logger.info("Streaming bundled file in chunks of %s", CHUNK_SIZE)
    else:
        nodes_list = descriptor["nodes"]
        total_nodes = len(nodes_list)
        total_chunks = (total_nodes + CHUNK_SIZE - 1) // CHUNK_SIZE
        chunk_iter = _chunk_list(nodes_list, CHUNK_SIZE)
        logger.info("Splitting %s nodes into %s chunks of size %s", total_nodes, total_chunks, CHUNK_SIZE)

    if wants_stream:
        return _stream_response(chunk_iter, process_chunk, total_nodes, total_chunks)
    return _render_result(chunk_iter, process_chunk)
