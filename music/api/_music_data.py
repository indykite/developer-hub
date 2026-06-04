"""Load music defaults extracted from the postman collection.

The postman bodies contain `{{project_gid}}`, `{{app_agent_gid}}` etc. placeholders
that the individual api modules substitute with values from environment variables.
This module only owns the *static* defaults — it does not touch os.environ.
"""

from __future__ import annotations

import json
from pathlib import Path

_MANIFEST_PATH = Path(__file__).parent.parent / "data" / "music_manifest.json"


def _load() -> dict:
    with _MANIFEST_PATH.open() as f:
        return json.load(f)


_MANIFEST = _load()


def _parse_policy(entry: dict) -> dict:
    """Return a copy of `entry` with `policy` parsed from JSON to a dict (if needed)."""
    out = dict(entry)
    pol = out.get("policy")
    if isinstance(pol, str):
        out["policy"] = json.loads(pol)
    return out


def _parse_query(entry: dict) -> dict:
    """Return a copy of `entry` with `query` parsed from JSON to a dict (if needed)."""
    out = dict(entry)
    q = out.get("query")
    if isinstance(q, str):
        out["query"] = json.loads(q)
    return out


PROJECT_DEFAULTS = _MANIFEST["project"]
APPLICATION_DEFAULTS = _MANIFEST["application"]
APP_AGENT_DEFAULTS = _MANIFEST["app_agent"]
TOKEN_INTROSPECT_DEFAULTS = _MANIFEST["token_introspect"]
MCP_SERVER_DEFAULTS = _MANIFEST["mcp_server"]


# Ten KBAC entries (kbac, kbac2...kbac10). slot N -> KBACS[N-1].
KBACS: list[dict] = [_parse_policy(k) for k in _MANIFEST["kbacs"]]


# Eleven AuthZen evaluation bodies. The first entry is the single 'Evaluation';
# the second is 'Evaluations' (which wraps an array under 'evaluations'); the rest
# are Evaluation2..Evaluation10.
EVALUATIONS: list[dict] = _MANIFEST["evaluations"]


def _flatten_ciq() -> tuple[list[dict], list[dict], list[dict]]:
    """Flatten ciq_groups into (policies, queries, executes).

    Each *policy* gets a slot like '1', '2'...'24' (string).
    Each *query* gets a slot like '1', '1b', '1c', '2', '2b'...
    Executes mirror queries.
    """
    policies: list[dict] = []
    queries: list[dict] = []
    executes: list[dict] = []

    for group in _MANIFEST["ciq_groups"]:
        slot = str(group["slot"])
        pol_entry = _parse_policy(group["policy"])
        pol_entry["slot"] = slot
        policies.append(pol_entry)

        for i, q in enumerate(group["queries"]):
            # First query gets the bare slot; subsequent get 'b', 'c', 'd'... appended.
            variant_suffix = "" if i == 0 else chr(ord("b") + i - 1)
            qslot = f"{slot}{variant_suffix}"
            kq = _parse_query(q["kq"])
            kq["slot"] = qslot
            kq["policy_slot"] = slot
            queries.append(kq)

            if "exec" in q:
                executes.append(
                    {
                        "slot": qslot,
                        "policy_slot": slot,
                        "input_params": q["exec"].get("input_params", {}),
                    },
                )

    return policies, queries, executes


CIQ_POLICIES, CIQ_QUERIES, CIQ_EXECUTES = _flatten_ciq()


def kbac_for_slot(slot: str) -> dict:
    idx = int(slot) - 1
    if idx < 0 or idx >= len(KBACS):
        msg = f"Unknown KBAC slot: {slot!r}"
        raise ValueError(msg)
    return KBACS[idx]


def evaluation_for_slot(slot: str) -> dict:
    idx = int(slot) - 1
    if idx < 0 or idx >= len(EVALUATIONS):
        msg = f"Unknown evaluation slot: {slot!r}"
        raise ValueError(msg)
    return EVALUATIONS[idx]


def ciq_policy_for_slot(slot: str) -> dict:
    spec = next((p for p in CIQ_POLICIES if p["slot"] == slot), None)
    if spec is None:
        msg = f"Unknown CIQ policy slot: {slot!r}"
        raise ValueError(msg)
    return spec


def ciq_query_for_slot(slot: str) -> dict:
    spec = next((q for q in CIQ_QUERIES if q["slot"] == slot), None)
    if spec is None:
        msg = f"Unknown CIQ knowledge-query slot: {slot!r}"
        raise ValueError(msg)
    return spec


def ciq_execute_for_slot(slot: str) -> dict:
    spec = next((e for e in CIQ_EXECUTES if e["slot"] == slot), None)
    if spec is None:
        msg = f"Unknown CIQ execute slot: {slot!r}"
        raise ValueError(msg)
    return spec


def slot_to_path_suffix(slot: str) -> str:
    """Canbank-style: slot '1' -> '', slot '2' -> '2', slot '2b' -> '2b'."""
    return "" if slot == "1" else slot


KBAC_SLOTS: list[str] = [str(i) for i in range(1, len(KBACS) + 1)]
EVALUATION_SLOTS: list[str] = [str(i) for i in range(1, len(EVALUATIONS) + 1)]
CIQ_POLICY_SLOTS: list[str] = [p["slot"] for p in CIQ_POLICIES]
CIQ_QUERY_SLOTS: list[str] = [q["slot"] for q in CIQ_QUERIES]
CIQ_EXECUTE_SLOTS: list[str] = [e["slot"] for e in CIQ_EXECUTES]
