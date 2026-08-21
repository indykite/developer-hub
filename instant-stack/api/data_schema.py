# Copyright (c) 2026 IndyKite
import json
import logging
import os
import time

import requests
from flask import jsonify, render_template
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

tag = Tag(name="api_data_schema", description="Data Schema")
security = [{"ApiKeyAuth": []}]

logger = logging.getLogger(__name__)

# The GET /data-schema/v1/ call is deliberately short: it backs the index card's
# async gate, so it must not stall the browser.
SCHEMA_TIMEOUT_SECONDS = 10
HTTP_OK = 200

# The "is the graph captured?" answer changes rarely (only when a capture runs),
# so cache it briefly: the index polls it via /status on every landing, and we
# do not want to hit the platform each time.
_CAPTURED_CACHE_TTL_SECONDS = 30
_captured_cache = {"at": 0.0, "value": False}


class Unauthorized(BaseModel):
    code: int = Field(-1, description="Status Code")
    message: str = Field("Unauthorized!", description="Exception Information")


api_data_schema = APIBlueprint(
    "api_data_schema",
    __name__,
    url_prefix="/api_data_schema",
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True,
)


def _fetch_schema():
    """GET the project's observed data schema.

    Returns (status_code, json_or_none). status_code is None when the request
    could not be made (missing env, network error) - callers treat that as
    "no schema available" rather than raising.
    """
    url_endpoints = os.getenv("URL_ENDPOINTS")
    app_token = os.getenv("APP_TOKEN")
    if not url_endpoints or not app_token:
        logger.debug("Data schema unavailable: URL_ENDPOINTS or APP_TOKEN not set")
        return None, None

    api_url = f"{url_endpoints}/data-schema/v1/"
    try:
        response = requests.get(
            api_url,
            headers={"X-IK-ClientKey": app_token},
            timeout=SCHEMA_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        logger.warning("Data schema request failed: %s", e)
        return None, None

    try:
        response_json = response.json()
    except ValueError:
        response_json = None

    # A 200 whose body is not a schema document (an upstream/proxy error page,
    # or unexpected JSON) must not read as a valid empty graph - surface it as
    # unavailable so the page shows the warning instead of a blank schema.
    if response.status_code == HTTP_OK and not (
        isinstance(response_json, dict) and isinstance(response_json.get("graph"), dict)
    ):
        logger.warning("Data schema 200 with unexpected body; treating as unavailable")
        return None, None
    return response.status_code, response_json


def schema_is_captured(*, use_cache=True):
    """Report whether the IKG holds both nodes and relationships.

    True only when the schema endpoint returns a graph with a non-empty node
    map AND at least one edge combination - i.e. captures have run. An empty
    project answers 404, which reads as False. Used to gate the index card.

    The result is cached for a short TTL so the index's async gate does not hit
    the platform on every landing; pass use_cache=False to force a fresh check.
    """
    now = time.monotonic()
    if use_cache and (now - _captured_cache["at"]) < _CAPTURED_CACHE_TTL_SECONDS:
        return _captured_cache["value"]

    status_code, payload = _fetch_schema()
    captured = False
    if status_code == HTTP_OK and isinstance(payload, dict):
        graph = payload.get("graph") or {}
        captured = bool(graph.get("nodes")) and bool(graph.get("edges"))

    _captured_cache["at"] = now
    _captured_cache["value"] = captured
    return captured


@api_data_schema.get("/status", tags=[tag])
def status():
    """Return whether the graph is captured, as JSON, for the index card's async gate."""
    return jsonify({"captured": schema_is_captured()})


def _graph_of(payload):
    """Return the JGFv2 graph object from a schema payload, or {} when absent."""
    graph = payload.get("graph") if isinstance(payload, dict) else None
    return graph or {}


def _node_types(graph):
    """Flatten graph.nodes (a type-keyed map) into a list sorted by type name."""
    result = []
    for name, entry in sorted((graph.get("nodes") or {}).items()):
        metadata = (entry or {}).get("metadata") or {}
        result.append(
            {
                "name": name,
                "node_count": metadata.get("node_count"),
                "properties": metadata.get("properties") or {},
                "user_defined_labels": metadata.get("user_defined_labels") or [],
            },
        )
    return result


def _edges(graph):
    """Flatten graph.edges into a list sorted by (source, relation, target)."""
    result = []
    for edge in graph.get("edges") or []:
        metadata = (edge or {}).get("metadata") or {}
        result.append(
            {
                "source": edge.get("source"),
                "relation": edge.get("relation"),
                "target": edge.get("target"),
                "count": metadata.get("count"),
                "properties": metadata.get("properties") or {},
            },
        )
    result.sort(key=lambda e: (e["source"] or "", e["relation"] or "", e["target"] or ""))
    return result


@api_data_schema.get("/view", tags=[tag])
def view_schema():
    """Render the IKG's observed data schema (node types and relationship combinations)."""
    status_code, payload = _fetch_schema()
    graph = _graph_of(payload)
    node_types = _node_types(graph)
    edges = _edges(graph)

    return render_template(
        "data_schema/view.html",
        status_code=status_code,
        graph_metadata=graph.get("metadata") or {},
        node_types=node_types,
        edges=edges,
        total_nodes=sum((n["node_count"] or 0) for n in node_types),
        total_edges=sum((e["count"] or 0) for e in edges),
        raw_json=json.dumps(payload, indent=2) if payload is not None else "",
    )
