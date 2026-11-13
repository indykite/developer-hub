# Copyright (c) 2026 IndyKite
"""The push-update webhook forwards the gateway's chain, trace and token fields to the audit stream."""

from __future__ import annotations

import app
import pytest

RECORD = {
    "decision": "AUTHORIZED",
    "reason": "allowed",
    "subject": "auth0|69c410bfb501d3a02bd01ea2",
    "actor": "retriever@clients",
    "action": "CAN_TRIGGER",
    "timestamp": "2026-11-05T08:00:00Z",
    "service": "retriever-iag",
}


@pytest.fixture
def sent(monkeypatch):
    """Capture what the endpoint broadcasts instead of fanning it out to SSE clients."""
    events = []
    monkeypatch.setattr(app, "_broadcast_update", events.append)
    return events


def test_chain_trace_and_token_are_forwarded(sent):
    """actorsChain, traceID and tokenID reach the stream under snake_case names."""
    chain = ["orchestrator@clients", "retriever@clients"]
    record = {**RECORD, "actorsChain": chain, "traceID": "t-1", "tokenID": "j-1"}
    resp = app.app.test_client().post("/api/push-update", json=record)
    assert resp.status_code == 200
    assert sent[0]["actors_chain"] == ["orchestrator@clients", "retriever@clients"]
    assert sent[0]["trace_id"] == "t-1"
    assert sent[0]["token_id"] == "j-1"


def test_capability_record_fields_are_forwarded(sent):
    """A DISCOVERY record keeps its permitted set, discovery counts and cache flags under snake_case names."""
    record = {
        **RECORD,
        "service": "mcp-iag",
        "action": "DISCOVERY",
        "reason": "the lists of the server were filtered to the permitted set",
        "cached": {"tool": True},
        "permittedSet": {"tool": ["ciq_execute", "get-hq-weather"]},
        "discovery": {"serverCalled": True, "listed": 19, "kept": 2},
    }
    app.app.test_client().post("/api/push-update", json=record)
    event = sent[0]
    assert event["action"] == "DISCOVERY"
    assert event["permitted_set"] == {"tool": ["ciq_execute", "get-hq-weather"]}
    assert event["discovery_stats"] == {"serverCalled": True, "listed": 19, "kept": 2}
    assert event["cached"] == {"tool": True}
    assert "capabilities" not in event


def test_empty_chain_is_left_out(sent):
    """A record without a chain, or with an empty one, broadcasts no chain field."""
    app.app.test_client().post("/api/push-update", json={**RECORD, "actorsChain": []})
    assert "actors_chain" not in sent[0]
    assert sent[0]["decision"] == "AUTHORIZED"


def test_intent_check_fields_are_forwarded(sent):
    """IBAC_USAGE and RESOLVED_EVALUATIONS records keep the intent and the platform's answers."""
    intent = [{"subject": {"type": "Person"}, "action": {"name": "CAN_VIEW"}, "resource": {"type": "Invoice"}}]
    resolved = [
        {
            "allowed": True,
            "subjectType": "Person",
            "subjectID": RECORD["subject"],
            "action": "CAN_VIEW",
            "resourceType": "Invoice",
            "resourceID": "inv-1",
        },
    ]
    client = app.app.test_client()
    usage = {**RECORD, "service": "erp-mcp-iag", "action": "IBAC_USAGE", "currentIntent": intent}
    client.post("/api/push-update", json={**usage, "reasonCode": "intent_match_authzen_required"})
    client.post("/api/push-update", json={**usage, "action": "RESOLVED_EVALUATIONS", "resolvedEvaluations": resolved})
    assert sent[0]["current_intent"] == intent
    assert sent[0]["reason_code"] == "intent_match_authzen_required"
    assert sent[1]["action"] == "RESOLVED_EVALUATIONS"
    assert sent[1]["resolved_evaluations"] == resolved


def test_intent_comparison_is_forwarded(sent):
    """An IBAC_DISCOVERY record keeps the skills each evaluation of the intent fits."""
    matches = [
        {"subjectType": "Person", "resourceType": "Invoice", "action": "CAN_VIEW", "matchedSkills": ["query-erp"]},
        {"subjectType": "Person", "resourceType": "Weather", "action": "CAN_READ", "matchedSkills": []},
    ]
    record = {**RECORD, "service": "orchestrator-iag", "action": "IBAC_DISCOVERY", "evaluations": matches}
    app.app.test_client().post("/api/push-update", json={**record, "skillIDs": ["query-erp", "orchestrator"]})
    assert sent[0]["action"] == "IBAC_DISCOVERY"
    assert sent[0]["evaluations"] == matches
    assert sent[0]["skill_ids"] == ["query-erp", "orchestrator"]
