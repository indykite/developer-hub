# Copyright (c) 2026 IndyKite
"""IndyKite platform client: data schema, policy listing, AuthZEN decisions - all with the app agent key."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx

PARAM_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
# Names the platform binds itself; never input parameters.
RESERVED_PARAMS = frozenset({"token", "ik_token", "subject_id"})
IDENTITY_LABEL = "DigitalTwin"


class PlatformError(Exception):
    """A platform call answered 4xx/5xx, or never answered."""

    def __init__(self, status: int | None, body: object) -> None:
        """`status` is None when the request never got an HTTP answer."""
        self.status = status
        self.body = body
        super().__init__(f"platform call failed: {status} {body}")


class PlatformClient:
    """Runtime API calls the console makes with the app agent credential (X-IK-ClientKey)."""

    def __init__(self, base_url: str, app_agent_key: str, timeout: float, schema_ttl_seconds: float = 60.0) -> None:
        """Cache the schema for `schema_ttl_seconds`; everything else is fetched per call."""
        self._base = base_url.rstrip("/")
        self._headers = {"X-IK-ClientKey": app_agent_key}
        self._timeout = timeout
        self._schema_ttl = schema_ttl_seconds
        self._schema_cache: tuple[float, dict[str, Any]] | None = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any] | str:
        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
                response = await client.request(method, f"{self._base}{path}", params=params, json=json)
        except httpx.HTTPError as exc:
            raise PlatformError(None, str(exc)) from exc
        try:
            body = response.json()
        except ValueError:
            body = response.text
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise PlatformError(response.status_code, body)
        return body

    async def schema(self) -> dict[str, Any]:
        """Fetch the project's observed data schema (JGF v2), cached briefly."""
        now = time.monotonic()
        if self._schema_cache and now - self._schema_cache[0] < self._schema_ttl:
            return self._schema_cache[1]
        body = await self._request("GET", "/data-schema/v1")
        if not isinstance(body, dict):
            raise PlatformError(None, "data schema is not a JSON object")
        self._schema_cache = (now, body)
        return body

    async def policies(self, subject_type: str) -> list[dict[str, Any]]:
        """ACTIVE KBAC policies for one subject type, as the platform lists them (needs ReadAuthZConfigs)."""
        body = await self._request("GET", "/access/v1/policies", params={"subject_type": subject_type})
        return body.get("results", []) if isinstance(body, dict) else []

    async def evaluations(self, body: dict[str, Any]) -> dict[str, Any]:
        """Batch AuthZEN decisions (`POST /access/v1/evaluations`)."""
        answer = await self._request("POST", "/access/v1/evaluations", json=body)
        return answer if isinstance(answer, dict) else {}

    async def search(self, kind: str, body: dict[str, Any]) -> dict[str, Any]:
        """AuthZEN search: `kind` is subject, resource or action."""
        answer = await self._request("POST", f"/access/v1/search/{kind}", json=body)
        return answer if isinstance(answer, dict) else {}


async def reach(
    platform: PlatformClient,
    subject_type: str,
    subject_id: str,
    triples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Ask the graph what one identity reaches right now: a search/resource per granted (action, resource type).

    Policies that declare `$name` parameters are not searched: the platform refuses a request that
    does not supply every declared parameter (422), so the fact only names what would be needed. A
    failing search is reported as an error on that fact, never raised, so one bad policy does not
    hide the rest.
    """
    jobs: list[tuple[str, str, list[str]]] = [
        (action, triple["resource_type"], triple.get("params") or [])
        for triple in triples
        if triple.get("subject_type") == subject_type
        for action in triple.get("actions") or []
    ]

    async def one(action: str, resource_type: str, params: list[str]) -> dict[str, Any]:
        if params:
            return {"action": action, "resource_type": resource_type, "params": params, "ids": [], "error": None}
        body = {
            "subject": {"type": subject_type, "id": subject_id},
            "action": {"name": action},
            "resource": {"type": resource_type},
        }
        fact: dict[str, Any] = {
            "action": action,
            "resource_type": resource_type,
            "params": params,
            "ids": [],
            "error": None,
        }
        try:
            answer = await platform.search("resource", body)
            fact["ids"] = [r.get("id") for r in answer.get("results") or [] if r.get("id")]
        except PlatformError as exc:
            fact["error"] = f"{exc.status}: {exc.body}"
        return fact

    facts = await asyncio.gather(*(one(*job) for job in jobs))
    return sorted(facts, key=lambda f: (not f["ids"], f["resource_type"], f["action"]))


def schema_summary(jgf: dict[str, Any]) -> dict[str, Any]:
    """Flatten the JGF v2 document to what the vocabulary page draws."""
    graph = jgf.get("graph", {})
    nodes = []
    for node_type, entry in sorted((graph.get("nodes") or {}).items()):
        meta = entry.get("metadata", {})
        labels = [label.get("name") for label in meta.get("system_labels", [])]
        properties = [
            {"name": name, "types": [t.get("type") for t in prop.get("types", [])], "count": prop.get("count", 0)}
            for name, prop in sorted((meta.get("properties") or {}).items())
        ]
        nodes.append(
            {
                "type": node_type,
                "count": meta.get("node_count", 0),
                "identity": IDENTITY_LABEL in labels,
                "properties": properties,
            },
        )
    # JGF calls them edges; IndyKite calls them relationships, which is what the UI says.
    relationships = [
        {
            "source": rel.get("source"),
            "relation": rel.get("relation"),
            "target": rel.get("target"),
            "count": (rel.get("metadata") or {}).get("count", 0),
        }
        for rel in graph.get("edges") or []
    ]
    return {
        "nodes": nodes,
        "relationships": relationships,
        "updated_at": (graph.get("metadata") or {}).get("updated_at"),
    }


def policy_triples(listing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """List the (subject type, action, resource type) combinations the agent may emit, with their conditions."""
    triples = []
    for entry in listing:
        policy = entry.get("policy") or {}
        condition = policy.get("condition") or {}
        cypher = condition.get("cypher", "")
        params = sorted({p for p in PARAM_RE.findall(cypher) if p not in RESERVED_PARAMS})
        triples.append(
            {
                "subject_type": (policy.get("subject") or {}).get("type"),
                "actions": policy.get("actions") or [],
                "resource_type": (policy.get("resource") or {}).get("type"),
                "cypher": cypher,
                "filter": condition.get("filter"),
                "params": params,
                "version": (policy.get("meta") or {}).get("policy_version"),
                "tags": entry.get("tags") or [],
            },
        )
    return triples
