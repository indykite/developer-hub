# Copyright (c) 2026 IndyKite
"""The audit cards' tools and the intent view: the raw record, the re-check of a recorded question, the IBAC status."""

from __future__ import annotations

import app
import httpx
import pytest

QUESTION = {
    "subject": {"type": "Person", "id": "auth0|69c410bfb501d3a02bd01ea2"},
    "action": {"name": "CAN_VIEW"},
    "resource": {"type": "Invoice", "id": "inv-1"},
}


@pytest.fixture
def sent(monkeypatch):
    """Capture what the endpoint broadcasts instead of fanning it out to SSE clients."""
    events = []
    monkeypatch.setattr(app, "_broadcast_update", events.append)
    return events


@pytest.fixture
def platform(monkeypatch):
    """Switch the re-check on, without a login flow, and capture the AuthZEN calls; answers `decision: true`."""
    calls = []

    def post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return httpx.Response(200, json={"decision": True}, request=httpx.Request("POST", url))

    monkeypatch.setattr(app, "RECHECK_ENABLED", True)
    monkeypatch.setattr(app, "ID_SERVER_BASE_URL", "")
    monkeypatch.setattr(app, "INDYKITE_BASE_URL", "https://platform.test")
    monkeypatch.setattr(app, "APP_AGENT_CREDENTIALS_TOKEN", "app-agent-key")
    monkeypatch.setattr(app.httpx, "post", post)
    return calls


def test_gateway_record_is_forwarded_whole(sent):
    """The stream carries the record as the gateway sent it, fields the cards do not use included."""
    record = {
        "decision": "AUTHORIZED",
        "subject": "auth0|69c410bfb501d3a02bd01ea2",
        "actor": "retriever@clients",
        "action": "ADMISSION",
        "service": "retriever-iag",
        "traceID": "t-1",
        "somethingNew": {"kept": True},
    }
    app.app.test_client().post("/api/push-update", json=record)
    assert sent[0]["raw"] == record


def test_intent_agent_record_is_forwarded_whole(sent, monkeypatch):
    """An Intent Agent record keeps its own shape under `raw`."""
    monkeypatch.setattr(app, "_discovery_preview", lambda _evaluations: None)
    record = {"outcome": "success", "prompt": "my invoices", "evaluations": [], "model": "some-model"}
    app.app.test_client().post("/api/push-update", json=record)
    assert sent[0]["service"] == "intent-agent"
    assert sent[0]["raw"] == record


def test_recheck_asks_the_platform_the_recorded_question(platform):
    """One evaluation with the app agent credential; the answer is returned as a boolean."""
    resp = app.app.test_client().post("/api/recheck", json=QUESTION)
    assert resp.status_code == 200
    assert resp.get_json() == {"decision": True}
    assert platform[0]["url"] == "https://platform.test/access/v1/evaluation"
    assert platform[0]["headers"]["X-IK-ClientKey"] == "app-agent-key"
    assert platform[0]["json"] == QUESTION


def test_recheck_passes_a_context_through(platform):
    """The context of an evaluation (input parameters) is part of the question."""
    context = {"input_params": {"ticker": "CNB"}}
    app.app.test_client().post("/api/recheck", json={**QUESTION, "context": context, "unrelated": 1})
    assert platform[0]["json"] == {**QUESTION, "context": context}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {**QUESTION, "subject": {"type": "Person"}},
        {**QUESTION, "resource": {"type": "Invoice", "id": ""}},
        {**QUESTION, "action": {}},
        {**QUESTION, "action": "CAN_VIEW"},
        {**QUESTION, "subject": "auth0|x"},
    ],
)
def test_recheck_refuses_an_incomplete_question(platform, body):
    """Without a subject, an action name and a resource, nothing is sent to the platform."""
    resp = app.app.test_client().post("/api/recheck", json=body)
    assert resp.status_code == 400
    assert platform == []


def test_recheck_needs_a_session_when_the_console_has_a_login(platform, monkeypatch):
    """The route spends the app agent credential: an anonymous caller gets 401."""
    monkeypatch.setattr(app, "ID_SERVER_BASE_URL", "https://tenant.test")
    resp = app.app.test_client().post("/api/recheck", json=QUESTION)
    assert resp.status_code == 401
    assert platform == []


def test_recheck_reports_a_platform_refusal(platform, monkeypatch):
    """A platform error (a policy that needs parameters the question lacks: 422) is a 502 with the status."""

    def post(url, **_kwargs):
        return httpx.Response(422, json={"message": "missing parameter"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(app.httpx, "post", post)
    resp = app.app.test_client().post("/api/recheck", json=QUESTION)
    assert resp.status_code == 502
    assert "422" in resp.get_json()["error"]


def test_recheck_is_off_without_the_platform_binding(monkeypatch):
    """No base URL or credential: the route answers 404 and the config says so."""
    monkeypatch.setattr(app, "RECHECK_ENABLED", False)
    client = app.app.test_client()
    assert client.post("/api/recheck", json=QUESTION).status_code == 404
    assert client.get("/api/config").get_json()["recheck_enabled"] is False


def test_ibac_status_reports_the_switches(monkeypatch):
    """IBAC on with the unmapped policy; without the intent profile the Intent Agent is not probed."""
    monkeypatch.setattr(app, "IBAC_ENABLED", True)
    monkeypatch.setattr(app, "IBAC_UNMAPPED", "deny")
    monkeypatch.setattr(app, "COMPOSE_PROFILES", ["erp"])
    monkeypatch.setattr(app.httpx, "get", lambda *_a, **_k: pytest.fail("the Intent Agent must not be probed"))
    assert app.app.test_client().get("/api/ibac/status").get_json() == {
        "enabled": True,
        "unmapped": "deny",
        "agent": "off",
    }


@pytest.mark.parametrize(("status", "state"), [(200, "up"), (503, "down")])
def test_ibac_status_probes_the_intent_agent(monkeypatch, status, state):
    """With the intent profile on, the health endpoint decides between up and down."""
    asked = []

    def get(url, **_kwargs):
        asked.append(url)
        return httpx.Response(status, request=httpx.Request("GET", url))

    monkeypatch.setattr(app, "COMPOSE_PROFILES", ["intent"])
    monkeypatch.setattr(app, "INTENT_AGENT_HEALTH_URL", "http://intent-agent:8001/health")
    monkeypatch.setattr(app, "_intent_health_cache", {"fetched": 0.0, "state": "unknown"})
    monkeypatch.setattr(app.httpx, "get", get)
    client = app.app.test_client()
    assert client.get("/api/ibac/status").get_json()["agent"] == state
    assert client.get("/api/ibac/status").get_json()["agent"] == state
    assert asked == ["http://intent-agent:8001/health"]


def test_ibac_status_without_a_health_url(monkeypatch):
    """The intent profile on but no health URL: the agent's state is unknown."""
    monkeypatch.setattr(app, "COMPOSE_PROFILES", ["intent"])
    monkeypatch.setattr(app, "INTENT_AGENT_HEALTH_URL", "")
    assert app.app.test_client().get("/api/ibac/status").get_json()["agent"] == "unknown"
