import concurrent.futures
import logging
import os
from pathlib import Path

import ijson
import requests
from flask import flash, redirect, render_template, request, url_for
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_capture", description="Capture")
security = [{"ApiKeyAuth": []}]

logger = logging.getLogger(__name__)

# Directory holding the *.json node files available for ingestion.
NODES_DIR = Path(__file__).parent.parent / "data" / "nodes"

CHUNK_SIZE = 200
MAX_WORKERS = 10
# Cap chunks held in memory at once so a multi-GB nodes file stays flat in RAM.
# At CHUNK_SIZE=200 this is at most MAX_IN_FLIGHT * 200 nodes resident.
MAX_IN_FLIGHT = MAX_WORKERS * 4
REQUEST_TIMEOUT = 120  # seconds per chunk


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


def _detect_item_prefix(file_path, key):
    """Pick the ijson prefix: 'item' for a bare top-level array, '<key>.item' for {<key>: [...]}.

    Only reads the first non-whitespace byte, so it never loads the (possibly multi-GB) file.
    """
    with file_path.open("rb") as f:
        for block in iter(lambda: f.read(64), b""):
            stripped = block.lstrip()
            if stripped:
                return "item" if stripped[:1] == b"[" else f"{key}.item"
    return f"{key}.item"


def _iter_file_node_chunks(file_path, chunk_size):
    """Yield lists of up to chunk_size nodes streamed from file_path.

    Never holds more than one chunk in memory, so the file size is irrelevant to RAM
    usage — this is what lets a multi-GB file be captured without freezing.
    """
    prefix = _detect_item_prefix(file_path, "nodes")
    with file_path.open("rb") as f:
        chunk = []
        for node in ijson.items(f, prefix, use_float=True):
            chunk.append(node)
            if len(chunk) >= chunk_size:
                yield chunk
                chunk = []
        if chunk:
            yield chunk


def _make_process_chunk(api_url, app_token):
    """Build a chunk processor that PUTs a node chunk and never raises."""

    def process_chunk(index, chunk):
        try:
            response = requests.put(
                api_url,
                headers={"Content-Type": "application/json", "X-IK-ClientKey": app_token},
                json={"nodes": chunk},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.exceptions.RequestException as e:
            logger.exception("Chunk %s failed", index)
            return {
                "chunk_index": index,
                "status_code": 500,
                "response_json": {"message": str(e), "chunk_index": index},
            }

        try:
            response_json = response.json()
        except ValueError:
            response_json = {"message": "Invalid JSON response", "status": response.status_code}
        return {"chunk_index": index, "status_code": response.status_code, "response_json": response_json}

    return process_chunk


def _iter_results_bounded(chunk_iter, process_chunk):
    """Yield (index, result) per chunk as it completes, capping in-flight chunks.

    Run process_chunk over a (possibly lazy) chunk iterator while keeping at most
    MAX_IN_FLIGHT chunks in memory. Submitting every chunk up front (the old
    `list(chunk_list(...))` + submit-all approach) would pull the entire file into RAM via
    the pending futures. Here we pull chunks from the iterator only as worker slots free up,
    so memory stays flat no matter how large the file is.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for index, chunk in enumerate(chunk_iter):
            if len(futures) >= MAX_IN_FLIGHT:
                done, _ = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
                for fut in done:
                    yield futures.pop(fut), fut.result()
            futures[executor.submit(process_chunk, index, chunk)] = index
        for fut in concurrent.futures.as_completed(list(futures)):
            yield futures.pop(fut), fut.result()


@api_capture.get("/select", tags=[tag])
def select_json_file():
    json_files = sorted(f.name for f in NODES_DIR.iterdir() if f.suffix == ".json") if NODES_DIR.exists() else []
    return render_template("capture/select_file.html", json_files=json_files)


@api_capture.post("/create", tags=[tag])
def upsert_file():
    selected_file = request.form.get("json_file")
    if not selected_file:
        flash("No file selected", "danger")
        return redirect(url_for("api_capture.select_json_file"))

    file_path = (NODES_DIR / selected_file).resolve()
    if file_path.parent != NODES_DIR.resolve() or file_path.suffix != ".json" or not file_path.is_file():
        flash(f"Invalid file: {selected_file}", "danger")
        return redirect(url_for("api_capture.select_json_file"))

    api_url = os.getenv("URL_ENDPOINTS", "") + "/capture/v1/nodes"
    process_chunk = _make_process_chunk(api_url, os.getenv("APP_TOKEN", ""))

    # Stream the file off disk in chunks so multi-GB files never land in memory whole.
    chunk_iter = _iter_file_node_chunks(file_path, CHUNK_SIZE)
    logger.info("Streaming %s in chunks of %s", file_path.name, CHUNK_SIZE)

    results = []
    last_status_code = 200
    for _index, result in _iter_results_bounded(chunk_iter, process_chunk):
        results.append(result["response_json"])
        last_status_code = result["status_code"]

    return render_template(
        "capture/result.html",
        response_json=results,
        status_code=last_status_code,
        selected_file=selected_file,
    )
