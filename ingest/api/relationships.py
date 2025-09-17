import concurrent.futures
import json
import logging
from pathlib import Path

import app
import requests
from flask import flash, redirect, render_template, request, url_for
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_relationships", description="Capture relationships")
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


@api_relationships.get("/select", tags=[tag])
def select_json_file():
    data_dir = Path("data/relationships")
    json_files = [f.name for f in data_dir.iterdir() if f.suffix == ".json"]
    return render_template("ingest/select_relationships_file.html", json_files=json_files)


@api_relationships.post("/create", tags=[tag])
def upsert_file():
    selected_file = request.form.get("json_file")
    if not selected_file:
        flash("No file selected", "danger")
        return redirect(url_for("api_relationships.select_json_file"))

    json_file_path = Path("data/relationships") / selected_file
    with json_file_path.open() as file:
        json_data = json.load(file)

        rel_list = json_data.get("relationships", [])
        rel_count = len(rel_list)
        logger.info("Total rel entries: %s", rel_count)

        def chunk_list(lst, chunk_size):
            for i in range(0, len(lst), chunk_size):
                yield lst[i : i + chunk_size]

        def process_chunk(chunk, index):
            chunk_data = {"relationships": chunk}
            try:
                response = requests.post(
                    app.url + "/indykite.ingest.v1beta3.IngestAPI/BatchUpsertRelationships",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {app.app_token}",
                    },
                    json=chunk_data,
                    timeout=30,  # Add timeout
                )
                try:
                    response_json = response.json()
                except ValueError:
                    response_json = {
                        "message": "Invalid JSON response",
                        "status": response.status_code,
                    }
            except requests.RequestException as e:
                return {
                    "chunk_index": index,
                    "status_code": 500,
                    "response_json": {"message": str(e)},
                }

            return {
                "chunk_index": index,
                "status_code": response.status_code,
                "response_json": response_json,
            }

        chunk_size = 200
        chunks = list(chunk_list(rel_list, chunk_size))

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(process_chunk, chunk, i): i for i, chunk in enumerate(chunks)}

            results = []
            last_status = None
            for future in concurrent.futures.as_completed(futures):
                index = futures[future]
                try:
                    result = future.result()
                    last_status = result["status_code"]
                    results.append(result["response_json"])
                except Exception as e:
                    logger.exception("Chunk %s failed", index)
                    results.append({"message": str(e)})

        return render_template(
            "ingest/result_relationships.html",
            response_json=results,
            status_code=last_status,
            selected_file=selected_file,
        )
