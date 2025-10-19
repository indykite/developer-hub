import concurrent.futures
import json
import logging
import os

import requests
from flask import render_template, request
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


@api_relationships.get("/create", tags=[tag])
def show_create_form():
    """Display the relationships form with a default example."""
    default_data = {}
    return render_template("relationships/create_form.html", default_data=default_data)


@api_relationships.post("/create", tags=[tag])
def create_relationships():
    """Capture relationships with the provided form data."""
    # Get form data - the relationships JSON
    try:
        relationships_json = request.form.get("relationships", "{}")
        json_data = json.loads(relationships_json)
    except json.JSONDecodeError as e:
        logger.exception("Failed to parse relationships JSON")
        return render_template(
            "relationships/result.html",
            response_json={"message": f"Invalid JSON: {e!s}"},
            status_code=400,
        )

    url_endpoints = os.getenv("URL_ENDPOINTS")
    app_token = os.getenv("APP_TOKEN")

    api_url = f"{url_endpoints}/capture/v1/relationships"

    rel_list = json_data.get("relationships", [])
    rel_count = len(rel_list)
    logger.info("Total relationship entries: %s", rel_count)

    def chunk_list(lst, chunk_size):
        for i in range(0, len(lst), chunk_size):
            yield lst[i : i + chunk_size]

    def process_chunk(index, chunk):
        chunk_data = {"relationships": chunk}

        logger.info("Processing chunk %s with {len(chunk)} relationships", index)
        logger.debug("Chunk %s payload: {json.dumps(chunk_data, indent=2)}", index)

        response = requests.post(
            api_url,
            headers={
                "Content-Type": "application/json",
                "X-IK-ClientKey": app_token,
            },
            json=chunk_data,
            timeout=30,
        )

        try:
            response_json = response.json()
        except ValueError:
            response_json = {
                "message": "Invalid JSON response",
                "status": response.status_code,
            }

        logger.info("Chunk %s response status: {response.status_code}", index)

        return {
            "chunk_index": index,
            "status_code": response.status_code,
            "response_json": response_json,
        }

    chunk_size = 200
    chunks = list(chunk_list(rel_list, chunk_size))
    len(chunks)

    logger.info("Splitting %s relationships into {chunks_count} chunks of size {chunk_size}", rel_count)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(process_chunk, i, chunk): i for i, chunk in enumerate(chunks)}

        results = []
        last_status_code = 200

        for future in concurrent.futures.as_completed(futures):
            index = futures[future]
            try:
                result = future.result()
                results.append(result["response_json"])
                last_status_code = result["status_code"]
            except Exception as e:
                logger.exception("Chunk %s failed", index)
                results.append({"message": str(e), "chunk_index": index})
                last_status_code = 500

        return render_template("relationships/result.html", response_json=results, status_code=last_status_code)
