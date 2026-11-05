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


def test_empty_chain_is_left_out(sent):
    """A record without a chain, or with an empty one, broadcasts no chain field."""
    app.app.test_client().post("/api/push-update", json={**RECORD, "actorsChain": []})
    assert "actors_chain" not in sent[0]
    assert sent[0]["decision"] == "AUTHORIZED"
