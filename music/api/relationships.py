import concurrent.futures
import json
import logging
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Response, render_template, request, stream_with_context
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_relationships", description="Capture Relationships")
security = [{"ApiKeyAuth": []}]

logger = logging.getLogger(__name__)


class Unauthorized(BaseModel):
    code: int = Field(-1, description="Status Code")
    message: str = Field("Unauthorized!", description="Exception Information")


api_relationships = APIBlueprint(
    "api_relationships",
    __name__,
    url_prefix="/api_relationships",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)

RELATIONSHIPS_FILE = Path(__file__).parent.parent / "data" / "relationships" / "relationships_music.json"
ENV_FILE = Path(__file__).parent.parent / ".env"
CHUNK_SIZE = 200
MAX_WORKERS = 10
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

_PREVIEW_REL_COUNT = 5
_APP_AGENT_HELP = (
    "APP_TOKEN is not set (checked {env}). Create an App Agent for this Music project "
    "(/api_app_agent/create) — the credentials response stores APP_TOKEN automatically. "
    "If APP_TOKEN is already in .env, restart `flask run`."
)
_NO_URL_MSG = "URL_ENDPOINTS is not set in .env (e.g. https://eu.api.indykite.com)."


def _load_default_relationships():
    try:
        with RELATIONSHIPS_FILE.open() as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning("Could not load relationships_music.json: %s", e)
        return {"relationships": []}


@api_relationships.get("/create", tags=[tag])
def show_create_form():
    """Display the relationships form with a preview of the music defaults."""
    default_data = _load_default_relationships()
    rel_list = default_data if isinstance(default_data, list) else default_data.get("relationships", [])
    preview = {"relationships": rel_list[:_PREVIEW_REL_COUNT]}
    return render_template(
        "relationships/create_form.html",
        rel_count=len(rel_list),
        preview_count=min(_PREVIEW_REL_COUNT, len(rel_list)),
        preview_json=json.dumps(preview, indent=2),
        relationships_file=str(RELATIONSHIPS_FILE.relative_to(RELATIONSHIPS_FILE.parent.parent.parent)),
    )


def _chunk_list(lst, chunk_size):
    for i in range(0, len(lst), chunk_size):
        yield lst[i : i + chunk_size]


def _is_retryable_status(status: int) -> bool:
    if status in {HTTP_REQUEST_TIMEOUT, HTTP_TOO_MANY_REQUESTS}:
        return True
    return HTTP_SERVER_ERROR_MIN <= status < HTTP_SERVER_ERROR_MAX


def _make_process_chunk(api_url: str, app_token: str):
    """Build a chunk processor that POSTs a relationship chunk, retrying on transient errors."""

    def process_chunk(index, chunk):
        last_exception = None
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            logger.info(
                "Processing chunk %s with %s relationships (attempt %s/%s)",
                index,
                len(chunk),
                attempt,
                RETRY_ATTEMPTS,
            )
            try:
                response = requests.post(
                    api_url,
                    headers={"Content-Type": "application/json", "X-IK-ClientKey": app_token},
                    json={"relationships": chunk},
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
    """Return (json_data, error_html_response | None) from the form (bundled file or pasted JSON)."""
    use_defaults = request.form.get("use_defaults") == "true"
    if use_defaults:
        logger.info("Loading relationships from disk: %s", RELATIONSHIPS_FILE)
        return _load_default_relationships(), None
    try:
        relationships_json = request.form.get("relationships", "{}")
        return json.loads(relationships_json), None
    except json.JSONDecodeError as e:
        logger.exception("Failed to parse relationships JSON")
        error = render_template(
            "relationships/result.html",
            response_json={"message": f"Invalid JSON: {e!s}"},
            status_code=HTTP_BAD_REQUEST,
        )
        return None, error


def _error_response(wants_stream, msg, status):
    """Report a pre-flight error either as a single NDJSON 'done' event or the result page."""
    logger.error(msg)
    if wants_stream:
        return Response(
            json.dumps({"type": "done", "status_code": status, "error": msg, "results": []}) + "\n",
            mimetype="application/x-ndjson",
        )
    return render_template("relationships/result.html", response_json={"message": msg}, status_code=status)


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


def _stream_response(chunks, process_chunk, total_relationships):
    """Run the chunks concurrently and stream NDJSON progress events for the progress bar."""
    total_chunks = len(chunks)

    def event_stream():
        start = {"type": "start", "total_chunks": total_chunks, "total_relationships": total_relationships}
        yield json.dumps(start) + "\n"
        results = []
        last_status_code = HTTP_OK
        completed = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(process_chunk, i, chunk): i for i, chunk in enumerate(chunks)}
            for future in concurrent.futures.as_completed(futures):
                completed += 1
                result = future.result()
                results.append(result["response_json"])
                last_status_code = result["status_code"]
                evt = {
                    "type": "chunk",
                    "completed": completed,
                    "total": total_chunks,
                    "chunk_index": futures[future],
                    "status_code": result["status_code"],
                }
                if result["status_code"] >= HTTP_BAD_REQUEST:
                    evt["response_text"] = result.get("response_text", "")
                yield json.dumps(evt) + "\n"
        yield json.dumps({"type": "done", "status_code": last_status_code, "results": results}) + "\n"

    return Response(
        stream_with_context(event_stream()),
        mimetype="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


def _render_result(chunks, process_chunk):
    """Run the chunks concurrently and render the result page on completion (no-JS fallback)."""
    results = []
    last_status_code = HTTP_OK
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_chunk, i, chunk): i for i, chunk in enumerate(chunks)}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result["response_json"])
            last_status_code = result["status_code"]
    return render_template("relationships/result.html", response_json=results, status_code=last_status_code)


@api_relationships.post("/create", tags=[tag])
def create_relationships():
    """Capture relationships from the bundled file or pasted JSON.

    Streams NDJSON progress events when the client accepts application/x-ndjson (driving the
    progress bar); otherwise renders the result page (kept for non-JS submissions).
    """
    json_data, error_html = _load_payload_from_form()
    if error_html is not None:
        return error_html

    wants_stream = "application/x-ndjson" in request.headers.get("Accept", "")
    env, error = _resolve_env(wants_stream)
    if error is not None:
        return error
    url_endpoints, app_token = env

    rel_list = json_data if isinstance(json_data, list) else json_data.get("relationships", [])
    chunks = list(_chunk_list(rel_list, CHUNK_SIZE))
    logger.info("Splitting %s relationships into %s chunks of size %s", len(rel_list), len(chunks), CHUNK_SIZE)
    process_chunk = _make_process_chunk(f"{url_endpoints}/capture/v1/relationships", app_token)

    if wants_stream:
        return _stream_response(chunks, process_chunk, len(rel_list))
    return _render_result(chunks, process_chunk)
