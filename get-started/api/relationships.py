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

# Directory holding the *.json relationship files available for ingestion.
RELATIONSHIPS_DIR = Path(__file__).parent.parent / "data" / "relationships"
ENV_FILE = Path(__file__).parent.parent / ".env"

# The IndyKite Capture API rejects requests with more than 250 entries, so each
# request is chunked well under that limit and the chunks are sent concurrently,
# with retries/backoff on transient failures (same approach as the music app).
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

_APP_AGENT_HELP = (
    "Create an App Agent and its credentials (/api_app_agent/create); the credentials response "
    "stores APP_TOKEN automatically. If APP_TOKEN is already in .env, restart `flask run`."
)


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


def _list_relationship_files():
    """Return the available *.json relationship files in data/relationships (sorted)."""
    if not RELATIONSHIPS_DIR.exists():
        return []
    return sorted(f.name for f in RELATIONSHIPS_DIR.iterdir() if f.suffix == ".json")


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


def _error_response(wants_stream, msg, status):
    """Report a pre-flight error either as a single NDJSON 'done' event or the result page."""
    if wants_stream:
        return Response(
            json.dumps({"type": "done", "status_code": status, "error": msg, "results": []}) + "\n",
            mimetype="application/x-ndjson",
        )
    return render_template("relationships/result.html", response_json={"message": msg}, status_code=status)


def _prepare_request(wants_stream):
    """Validate the selected file and env. Return ((rel_list, app_token, api_url), None) or (None, error_response)."""
    selected_file = request.form.get("json_file", "")
    if not selected_file:
        return None, _error_response(wants_stream, "No file selected", HTTP_BAD_REQUEST)

    file_path = (RELATIONSHIPS_DIR / selected_file).resolve()
    if file_path.parent != RELATIONSHIPS_DIR.resolve() or file_path.suffix != ".json" or not file_path.is_file():
        return None, _error_response(wants_stream, f"Invalid file: {selected_file}", HTTP_BAD_REQUEST)

    try:
        with file_path.open() as f:
            json_data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.exception("Failed to read relationship file %s", selected_file)
        return None, _error_response(wants_stream, f"Could not read file: {e!s}", HTTP_BAD_REQUEST)

    # Re-read .env in case it was updated since the server booted (e.g. APP_TOKEN was just written).
    load_dotenv(ENV_FILE, override=True)
    url_endpoints = os.getenv("URL_ENDPOINTS")
    app_token = os.getenv("APP_TOKEN")
    if not app_token:
        msg = f"APP_TOKEN is not set (checked {ENV_FILE}). {_APP_AGENT_HELP}"
        return None, _error_response(wants_stream, msg, HTTP_BAD_REQUEST)
    if not url_endpoints:
        msg = "URL_ENDPOINTS is not set in .env (e.g. https://eu.api.indykite.com)."
        return None, _error_response(wants_stream, msg, HTTP_BAD_REQUEST)

    rel_list = json_data if isinstance(json_data, list) else json_data.get("relationships", [])
    return (rel_list, app_token, f"{url_endpoints}/capture/v1/relationships"), None


def _stream_response(chunks, process_chunk, total_relationships):
    """Run the chunks concurrently and stream NDJSON progress events for the progress bar."""
    total_chunks = len(chunks)

    def event_stream():
        yield (
            json.dumps({"type": "start", "total_chunks": total_chunks, "total_relationships": total_relationships})
            + "\n"
        )
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


def _render_result(chunks, process_chunk, selected_file):
    """Run the chunks concurrently and render the result page on completion (no-JS fallback)."""
    results = []
    last_status_code = HTTP_OK
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_chunk, i, chunk): i for i, chunk in enumerate(chunks)}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result["response_json"])
            last_status_code = result["status_code"]
    return render_template(
        "relationships/result.html",
        response_json=results,
        status_code=last_status_code,
        selected_file=selected_file,
    )


@api_relationships.get("/create", tags=[tag])
def show_create_form():
    """Display the relationships form: pick a JSON file from data/relationships to ingest."""
    return render_template("relationships/create_form.html", json_files=_list_relationship_files())


@api_relationships.post("/create", tags=[tag])
def create_relationships():
    """Capture relationships from the selected data/relationships/*.json file.

    Streams NDJSON progress events when the client accepts application/x-ndjson (driving the
    progress bar); otherwise renders the result page. The file is chunked under the 250-entry
    Capture API limit and POSTed concurrently.
    """
    wants_stream = "application/x-ndjson" in request.headers.get("Accept", "")
    context, error = _prepare_request(wants_stream)
    if error is not None:
        return error

    rel_list, app_token, api_url = context
    chunks = list(_chunk_list(rel_list, CHUNK_SIZE))
    logger.info("Splitting %s relationships into %s chunks of size %s", len(rel_list), len(chunks), CHUNK_SIZE)
    process_chunk = _make_process_chunk(api_url, app_token)

    if wants_stream:
        return _stream_response(chunks, process_chunk, len(rel_list))
    return _render_result(chunks, process_chunk, request.form.get("json_file", ""))
