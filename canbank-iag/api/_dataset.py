# Copyright (c) 2026 IndyKite
"""Per-dataset static defaults (manifest loader) for canbank-iag.

Config-API elements that don't depend on runtime values live here as *data*, in
``data/<dataset>/manifest.json``, instead of being hardcoded inside the api
modules. The api modules still own env substitution (PROJECT_ID,
CIQ_POLICY_ID_*, ...); this module owns only the static, per-dataset data.

Select the active dataset with the ``DATASET`` env var (default ``"iag"``)::

    data/<DATASET>/manifest.json

Sourced from the manifest (each migrated from the matching ``api/*.py`` module):
CIQ knowledge queries, CIQ policies, external data resolvers, and the create-form
defaults for project, application, app agent, token introspect, MCP server and
the KBAC authorization policy. New sections follow the same pattern.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Load .env here (before reading DATASET): this module is imported by the api
# blueprints *before* app.py calls load_dotenv(), so without this DATASET could
# only be set as a real env var. Pointed at the known path so it is cwd-agnostic;
# load_dotenv is idempotent, so app.py's later call is harmless.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Which dataset to load. Defaults to "iag" (the only one today). Drop a new
# ``data/<name>/`` folder (manifest.json + nodes.json + relationships.json) to
# add another and select it with DATASET=<name> (in .env or as an env var).
DATASET = (os.getenv("DATASET") or "iag").strip() or "iag"

DATASET_DIR = _DATA_DIR / DATASET
_MANIFEST_PATH = DATASET_DIR / "manifest.json"

# Graph data files, now self-contained under the dataset dir (relocated from the
# old data/nodes/nodes_iag.json and data/relationships/relationships_iag.json).
NODES_PATH = DATASET_DIR / "nodes.json"
RELATIONSHIPS_PATH = DATASET_DIR / "relationships.json"


def available_datasets() -> list[str]:
    """Dataset names that have a ``data/<name>/manifest.json`` (for a future selector)."""
    if not _DATA_DIR.is_dir():
        return []
    return sorted(p.name for p in _DATA_DIR.iterdir() if p.is_dir() and (p / "manifest.json").is_file())


def _load() -> dict:
    if not _MANIFEST_PATH.is_file():
        msg = f"Dataset manifest not found: {_MANIFEST_PATH} (DATASET={DATASET!r}; available: {available_datasets()})"
        raise FileNotFoundError(msg)
    with _MANIFEST_PATH.open() as f:
        return json.load(f)


_MANIFEST = _load()

# --- CIQ knowledge queries (migrated from api/ciq_knowledge_query.py) --------
# Each entry: {slot, name, display_name, description, query{dict}} — the exact
# shape ciq_knowledge_query._QUERY_DEFS used to hold inline.
CIQ_QUERIES: list[dict] = _MANIFEST.get("ciq_queries", [])
CIQ_QUERY_SLOTS: list[str] = [q["slot"] for q in CIQ_QUERIES]


def ciq_query_for_slot(slot: str) -> dict:
    """Return the CIQ knowledge-query defaults for a slot (e.g. '1', '10')."""
    spec = next((q for q in CIQ_QUERIES if q["slot"] == slot), None)
    if spec is None:
        msg = f"Unknown CIQ knowledge-query slot: {slot!r}"
        raise ValueError(msg)
    return spec


# --- CIQ policies (migrated from api/ciq_policy.py) --------------------------
# Each entry: {slot, name, display_name, description, policy{dict}, tags[]} —
# the exact shape ciq_policy._POLICY_DEFS used to hold inline.
CIQ_POLICIES: list[dict] = _MANIFEST.get("ciq_policies", [])
CIQ_POLICY_SLOTS: list[str] = [p["slot"] for p in CIQ_POLICIES]


def ciq_policy_for_slot(slot: str) -> dict:
    """Return the CIQ policy defaults for a slot (e.g. '1', '10')."""
    spec = next((p for p in CIQ_POLICIES if p["slot"] == slot), None)
    if spec is None:
        msg = f"Unknown CIQ policy slot: {slot!r}"
        raise ValueError(msg)
    return spec


# --- External data resolvers (migrated from api/external_data_resolver.py) ---
# Each entry: {slot, name, display_name, description, url, method, headers{dict},
# request_payload, request_content_type, response_content_type, response_selector}
# — the exact shape external_data_resolver._RESOLVER_DEFS used to hold inline.
RESOLVERS: list[dict] = _MANIFEST.get("resolvers", [])
RESOLVER_SLOTS: list[str] = [r["slot"] for r in RESOLVERS]


def resolver_for_slot(slot: str) -> dict:
    """Return the external data resolver defaults for a slot (e.g. '1', '3')."""
    spec = next((r for r in RESOLVERS if r["slot"] == slot), None)
    if spec is None:
        msg = f"Unknown external data resolver slot: {slot!r}"
        raise ValueError(msg)
    return spec


# --- App agent (migrated from api/app_agent.py) -----------------------------
# Static app-agent form defaults; application_id is supplied at runtime from env.
APP_AGENT: dict = _MANIFEST.get("app_agent", {})
# Kept as a module-level list so api/app_agent.py (and provision.py, which
# imports it) can reference DEFAULT_API_PERMISSIONS unchanged.
DEFAULT_API_PERMISSIONS: list[str] = list(APP_AGENT.get("api_permissions", []))
APP_AGENT_CREDENTIALS_EXPIRE_DAYS: int = int(APP_AGENT.get("credentials_expire_days", 180))

# --- MCP server (migrated from api/mcp_server.py) ---------------------------
# Static mcp-server form defaults; project_id / app_agent_id / token_introspect_id
# are supplied at runtime from env.
MCP_SERVER: dict = _MANIFEST.get("mcp_server", {})

# --- Token introspect (migrated from api/token_introspect.py) ---------------
# Static token-introspect form defaults (jwt_matcher, claims_mapping, ...);
# project_id is supplied at runtime from env. offline_validation is kept (not
# online_validation) so IdP ID tokens validate against the issuer JWKS instead
# of /userinfo.
TOKEN_INTROSPECT: dict = _MANIFEST.get("token_introspect", {})

# --- KBAC authorization policies (migrated from api/authorization_policy.py) ---
# Static KBAC form defaults. Both api/authorization_policy.py and provision.py's
# _kbac_payload build the create-form payload from here (previously duplicated
# inline in each). The manifest holds a list of policies; a bare object (the
# pre-list manifest format) is accepted and treated as a one-element list.
_KBAC_RAW = _MANIFEST.get("kbac", [])
KBAC_POLICIES: list = _KBAC_RAW if isinstance(_KBAC_RAW, list) else [_KBAC_RAW]
# First policy kept under the old name for callers that predate the list.
KBAC: dict = KBAC_POLICIES[0] if KBAC_POLICIES else {}


def kbac_env_key(index: int) -> str:
    """Env key the created policy's ID is recorded under.

    The first policy keeps the historical KBAC_POLICY_ID name so existing
    .env files keep their meaning; later ones get KBAC_POLICY_ID_<n>.
    """
    return "KBAC_POLICY_ID" if index == 0 else f"KBAC_POLICY_ID_{index + 1}"


def kbac_form_default(project_id: str = "", index: int = 0) -> dict:
    """Build the create-form defaults for the KBAC policy at *index*.

    project_id is supplied by the caller (from env). The policy body is
    serialized compactly to match the JSON string the create form expects.
    env_key tells the create route where to record the resulting policy ID,
    so several policies don't overwrite each other's entry.
    """
    policy = KBAC_POLICIES[index] if index < len(KBAC_POLICIES) else {}
    return {
        "project_id": project_id,
        "description": policy.get("description", ""),
        "display_name": policy.get("display_name", ""),
        "name": policy.get("name", ""),
        "policy": json.dumps(policy.get("policy", {}), separators=(",", ":")),
        "status": policy.get("status", "ACTIVE"),
        "env_key": kbac_env_key(index),
    }


# --- Application (migrated from api/application.py) --------------------------
# Static application form defaults; project_id is supplied at runtime from env.
APPLICATION: dict = _MANIFEST.get("application", {})

# --- Project (migrated from api/project.py) ---------------------------------
# Static project form defaults; organization_id is supplied at runtime from env.
PROJECT: dict = _MANIFEST.get("project", {})
