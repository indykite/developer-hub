# Copyright (c) 2026 IndyKite
"""Tests of the console's record handling and graph merge; no network."""

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
os.environ["USECASE_DIR"] = str(ROOT / "usecases/cib")
os.environ["AGENT_DISPLAY_NAMES"] = "s1@clients=sentinel, o1@clients=orchestrator, a1@clients=analyst, @clients=mcp"
os.environ["INDYKITE_BASE_URL"] = ""
sys.path.insert(0, str(ROOT / "console"))

from app import main  # noqa: E402

ACTION = {"X-Console-Action": "1"}


@pytest.fixture
def client():
    """Test client with a fresh board."""
    with TestClient(main.app) as c:
        yield c


def jwt(claims: dict) -> str:
    """Build an unsigned token carrying the given claims."""
    part = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"x.{part}.y"


def start_step(client, run="r1", step=0):
    """Open a mission and its first task, so that records attach to it."""
    client.post(
        "/api/sentinel",
        json={
            "type": "run_start",
            "run_id": run,
            "trigger": {"type": "Mandate", "external_id": "mnd-1002"},
            "label": "Project Aurora",
        },
    )
    client.post(
        "/api/sentinel",
        json={"type": "step_start", "run_id": run, "step": step, "title": "t", "instruction": "i"},
    )


def test_unset_client_ids_are_not_named():
    """An unset client id gives no display name; a known subject is named."""
    assert "@clients" not in main.AGENT_NAMES
    assert main.alias("s1@clients") == "sentinel"
    assert main.alias("unknown") == "unknown"


def test_gateway_denial_overrides_the_step_outcome(client):
    """A gateway refusal during a task makes the task refused, whatever the answer said."""
    start_step(client)
    client.post(
        "/api/push-update",
        json={
            "service": "analyst-iag",
            "decision": "NOT_AUTHORIZED",
            "action": "ADMISSION",
            "subject": "s1@clients",
            "actorsChain": ["o1@clients", "a1@clients"],
            "reason": "no matching workflow",
        },
    )
    answer = client.post(
        "/api/sentinel",
        json={"type": "step_end", "run_id": "r1", "step": 0, "outcome": "done", "answer": "ok", "http_status": 200},
    ).json()
    assert answer["outcome"] == "refused"
    assert answer["observed"].startswith("analyst-iag refused admission")
    state = client.get("/api/state").json()
    step = state["runs"][0]["steps"][0]
    assert step["outcome"] == "refused"
    record = state["audit"][0]
    assert record["chain"] == ["orchestrator", "analyst"]
    assert record["subject"] == "sentinel"
    assert record["id"] in step["records"]


def test_records_outside_a_step_are_not_attached(client):
    """A record arriving outside a task belongs to no mission."""
    client.post("/api/push-update", json={"service": "orchestrator-iag", "decision": "AUTHORIZED"})
    record = client.get("/api/state").json()["audit"][-1]
    assert "run_id" not in record


def test_exchanged_token_is_decoded(client):
    """A reported delegation is decoded into subject and chain; the token leaves the reason field."""
    token = jwt(
        {"sub": "s1@clients", "aud": "cib-autonomous", "act": {"sub": "a1@clients", "act": {"sub": "o1@clients"}}},
    )
    client.post("/api/push-update", json={"service": "analyst_agent", "decision": "TOKEN_EXCHANGED", "reason": token})
    record = client.get("/api/state").json()["audit"][-1]
    assert record["chain"] == ["orchestrator", "analyst"]
    assert record["subject"] == "sentinel"
    assert record["token"] == token
    assert record["reason"] == ""


def test_an_agent_error_is_not_a_gateway_denial(client):
    """An agent's own error does not count as a gateway refusal."""
    start_step(client, "r2")
    client.post("/api/push-update", json={"service": "retriever_agent", "decision": "ERROR"})
    answer = client.post(
        "/api/sentinel",
        json={"type": "step_end", "run_id": "r2", "step": 0, "outcome": "done", "answer": "", "http_status": 200},
    ).json()
    assert answer["outcome"] == "done"


def test_messages_and_escalations_are_kept(client):
    """Wire messages and escalations are stored for the page."""
    client.post(
        "/api/sentinel",
        json={
            "type": "message",
            "id": "m1",
            "event_type": "indykite.audit.capture.upsert.node",
            "trigger": True,
            "node": {"type": "Mandate", "external_id": "mnd-1002"},
            "body": {},
        },
    )
    client.post("/api/sentinel", json={"type": "escalation", "run_id": "r1", "text": "outside coverage"})
    state = client.get("/api/state").json()
    assert state["messages"][-1]["short"] == "capture.upsert.node"
    assert state["messages"][-1]["trigger"] is True
    assert state["escalations"][-1]["text"] == "outside coverage"


@pytest.mark.usefixtures("client")
def test_graph_merges_seed_feed_and_written():
    """The graph merges the provisioned nodes, the fed events and what the Sentinel wrote."""
    board = main.board
    seed = board.graph()
    assert any(n["id"] == "Agent:sentinel" for n in seed["nodes"])
    assert not any(n["type"] in ("Tool", "Skill") for n in seed["nodes"])
    board.fed.append("mandate-aurora")
    board.written["restrictions"].append(
        {
            "restriction": "rst-mnd-1002",
            "company": "brightwater-pharma",
            "mandate": "mnd-1002",
            "agent": "s1@clients",
            "reason": "r",
        },
    )
    board.written["holds"].append({"hold": "hold-ord-7001", "order": "ord-7001", "agent": "s1@clients", "reason": "r"})
    graph = board.graph()
    origin = {n["id"]: n["origin"] for n in graph["nodes"]}
    assert origin["Mandate:mnd-1002"] == "feed"
    assert origin["Restriction:rst-mnd-1002"] == "sentinel"
    assert origin["Restriction:rst-mnd-1001"] == "seed"
    edges = {(e["source"], e["type"], e["target"]) for e in graph["edges"]}
    assert ("Restriction:rst-mnd-1002", "RESTRICTS", "Company:brightwater-pharma") in edges
    assert ("Hold:hold-ord-7001", "HOLDS", "TradeOrder:ord-7001") in edges
    assert ("Agent:sentinel", "RAISED", "Hold:hold-ord-7001") in edges


def test_feed_needs_platform_settings(client):
    """Feeding needs platform settings and a known event."""
    assert client.post("/api/feed/mandate-aurora", headers=ACTION).status_code == 503
    assert client.post("/api/feed/nope", headers=ACTION).status_code == 404


def test_intent_in_the_delegation_is_shown_on_the_task(client):
    """The intent carried in the first agent's delegation is attached to the task."""
    start_step(client, "r3")
    evaluation = {
        "subject": {"type": "Agent", "id": "s1@clients"},
        "action": {"name": "CAN_RESTRICT"},
        "resource": {"type": "Company", "id": "brightwater-pharma"},
        "context": {"parameters": {}},
    }
    token = jwt({"sub": "s1@clients", "act": {"sub": "o1@clients", "intent": {"evaluations": [evaluation]}}})
    client.post(
        "/api/push-update",
        json={"service": "orchestrator_agent", "decision": "TOKEN_EXCHANGED", "reason": token},
    )
    client.post("/api/push-update", json={"service": "retriever_agent", "decision": "TOKEN_EXCHANGED", "reason": token})
    step = client.get("/api/state").json()["runs"][-1]["steps"][0]
    assert [e["evaluation"] for e in step["intent"]] == [evaluation]
    # Without platform credentials the console says so rather than inventing a decision.
    assert step["intent"][0]["verdict"] in ("checking", "unchecked")


def test_a_task_without_an_intent_keeps_the_reason(client):
    """A task with no intent keeps the Intent Agent's reason."""
    start_step(client, "r4")
    refusal = {
        "error": "Unable to extract intent from prompt.",
        "reason": "The prompt is unclear.",
        "obligation": "Rephrase.",
    }
    client.post(
        "/api/sentinel",
        json={
            "type": "step_end",
            "run_id": "r4",
            "step": 0,
            "outcome": "unmapped",
            "answer": "x",
            "http_status": 200,
            "intent_error": refusal,
        },
    )
    step = client.get("/api/state").json()["runs"][-1]["steps"][0]
    assert step["outcome"] == "unmapped"
    assert step["intent_error"] == refusal


def test_an_evaluation_without_credentials_is_left_unchecked():
    """Without platform credentials an evaluation is left unchecked, not decided."""
    board = main.Board()
    verdict = asyncio.run(
        board.decide({"subject": {"id": "s"}, "action": {"name": "A"}, "resource": {"type": "T", "id": "x"}}),
    )
    assert verdict["verdict"] == "unchecked"


def test_reset_deletes_what_was_fed_and_written_but_not_the_provisioned_graph():
    """A reset targets the fed nodes, the restrictions and holds named after them, and nothing provisioned."""
    board = main.Board()
    board.written["restrictions"].append({"restriction": "rst-mnd-1001", "company": "halcyon-energy"})
    board.written["holds"].append({"hold": "hold-custom", "order": "ord-7001"})
    doomed = {f"{n['type']}:{n['external_id']}" for n in board.runtime_nodes()}
    assert {"Mandate:mnd-1002", "Restriction:rst-mnd-1002", "TradeOrder:ord-7004", "Hold:hold-ord-7004"} <= doomed
    assert {"Hold:hold-ord-7001", "Hold:hold-custom", "DealDocument:doc-mnd-1002-termsheet"} <= doomed
    assert not {"Restriction:rst-mnd-1001", "Mandate:mnd-1001", "TradeOrder:ord-7001"} & doomed


def test_an_evaluation_becomes_a_decision_or_a_search():
    """An evaluation with a resource id is a decision; without the id, a search; without a subject, not checked."""
    full = {"subject": {"type": "Agent", "id": "s"}, "action": {"name": "A"}, "resource": {"type": "T", "id": "x"}}
    assert main.intent_request(full) == (False, {k: full[k] for k in ("subject", "action", "resource")})
    search, body = main.intent_request({**full, "resource": {"type": "T"}, "context": {"parameters": {"tier": "gold"}}})
    assert search is True
    assert body["context"] == {"input_params": {"tier": "gold"}}
    assert main.intent_request({**full, "subject": {"type": "Agent"}}) is None
    # A second record the task names is not a parameter of a policy.
    with_target = {**full, "context": {"parameters": {"target_company": "c-1"}}}
    assert "context" not in main.intent_request(with_target)[1]
    assert main.intent_verdict({"decision": True}, search=False)["verdict"] == "permitted"
    assert main.intent_verdict({}, search=False)["verdict"] == "denied"
    assert main.intent_verdict({"results": [{"type": "T", "id": "x"}]}, search=True) == {
        "verdict": "permitted",
        "detail": "x",
    }
    assert main.intent_verdict({"results": []}, search=True)["verdict"] == "denied"


def test_another_site_cannot_write_through_the_console(client):
    """A request a browser sends from another origin is refused, and so is a page action without its header."""
    assert client.post("/api/reset", headers={**ACTION, "Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/reset", headers={**ACTION, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/feed/mandate-aurora").status_code == 403
    assert client.post("/api/wake").status_code == 403
    spoofed = client.post(
        "/api/sentinel",
        json={"type": "escalation", "text": "x"},
        headers={"Origin": "https://evil.example"},
    )
    assert spoofed.status_code == 403
    assert client.get("/api/state").json()["escalations"] == []


def test_the_page_and_the_services_are_let_through(client):
    """The page's own origin passes, and a service posting a record sends no origin at all."""
    own = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
    assert client.post("/api/feed/nope", headers={**ACTION, **own}).status_code == 404
    assert (
        client.post("/api/push-update", json={"service": "orchestrator-iag", "decision": "AUTHORIZED"}).status_code
        == 200
    )


def test_reset_deletes_nothing_unless_the_sentinel_says_it_is_idle(client, monkeypatch):
    """An unreachable Sentinel, an unreadable answer or a running mission each stop the reset before any delete."""
    deleted = []

    async def capture(path, body):
        """Record a Capture call instead of sending it."""
        deleted.append((path, body))
        return 200

    monkeypatch.setattr(main.board, "capture", capture)
    for answer, expected in (
        (main.httpx.Response(500, text="boom"), 502),
        (main.httpx.Response(200, json={"ok": True}), 502),
        (main.httpx.Response(200, json={"ok": True, "busy": True, "dropped": 0}), 409),
    ):
        main.board.http = main.httpx.AsyncClient(transport=main.httpx.MockTransport(lambda _request, a=answer: a))
        assert client.post("/api/reset", headers=ACTION).status_code == expected
    assert deleted == []
    idle = main.httpx.Response(200, json={"ok": True, "busy": False, "dropped": 1})
    main.board.http = main.httpx.AsyncClient(transport=main.httpx.MockTransport(lambda _request: idle))
    assert client.post("/api/reset", headers=ACTION).status_code == 200
    assert [path for path, _ in deleted] == ["nodes/delete"]


def test_the_gateways_platform_decisions_are_kept_on_the_task(client):
    """What the MCP gateway asked the platform about a tool call, and each decision, are shown on the task."""
    start_step(client, "r5")
    allowed = {
        "subjectType": "Agent",
        "subjectID": "s1@clients",
        "action": "CAN_HOLD",
        "resourceType": "TradeOrder",
        "resourceID": "ord-7001",
        "allowed": True,
    }
    record = {
        "service": "mcp-iag",
        "decision": "AUTHORIZED",
        "action": "RESOLVED_EVALUATIONS",
        "resolvedEvaluations": [allowed],
        "currentIntent": {"evaluations": []},
    }
    client.post("/api/push-update", json=record)
    client.post("/api/push-update", json=record)
    state = client.get("/api/state").json()
    assert state["runs"][-1]["steps"][0]["checked"] == [allowed]
    assert state["audit"][-1]["resolved"] == [allowed]
    assert state["audit"][-1]["current_intent"] == {"evaluations": []}


def test_a_tool_call_outside_the_intent_refuses_the_task(client):
    """A refusal of the MCP gateway for the intent or by the platform makes the task refused, in its own words."""
    start_step(client, "r6")
    client.post(
        "/api/push-update",
        json={
            "service": "mcp-iag",
            "decision": "NOT_AUTHORIZED",
            "action": "IBAC_USAGE",
            "reasonCode": "intent_mismatch",
            "reason": "No evaluation of the intent matches a mapping of the skill",
        },
    )
    answer = client.post(
        "/api/sentinel",
        json={"type": "step_end", "run_id": "r6", "step": 0, "outcome": "done", "answer": "x", "http_status": 200},
    ).json()
    assert answer["outcome"] == "refused"
    assert answer["observed"].startswith("mcp-iag refused a tool call that does not fit the task's intent")
    start_step(client, "r7")
    denied = {"action": "CAN_HOLD", "resourceType": "TradeOrder", "resourceID": "ord-7005", "allowed": False}
    client.post(
        "/api/push-update",
        json={
            "service": "mcp-iag",
            "decision": "NOT_AUTHORIZED",
            "action": "RESOLVED_EVALUATIONS",
            "resolvedEvaluations": [denied],
            "reason": "the platform denied a resolved evaluation",
        },
    )
    answer = client.post(
        "/api/sentinel",
        json={"type": "step_end", "run_id": "r7", "step": 0, "outcome": "done", "answer": "x", "http_status": 200},
    ).json()
    assert answer["observed"].startswith("mcp-iag refused a tool call the platform does not allow")


def test_a_list_narrowed_to_the_intent_is_not_a_refusal(client):
    """A gateway reporting that no mapped tool fits the intent shortens a list; the task goes on."""
    start_step(client, "r8")
    record = {
        "service": "mcp-iag",
        "decision": "NOT_AUTHORIZED",
        "action": "IBAC_DISCOVERY",
        "reasonCode": "intent_mismatch",
        "capabilityName": "tool",
        "skillIDs": ["raise-restriction", "hold-order"],
        "evaluations": [{"subjectType": "Agent", "resourceType": "Mandate", "action": "CAN_READ", "matchedSkills": []}],
    }
    client.post("/api/push-update", json=record)
    answer = client.post(
        "/api/sentinel",
        json={"type": "step_end", "run_id": "r8", "step": 0, "outcome": "done", "answer": "x", "http_status": 200},
    ).json()
    assert answer["outcome"] == "done"
    step = client.get("/api/state").json()["runs"][-1]["steps"][0]
    assert step["denials"] == []
    assert step["fits"] == [
        {"service": "mcp-iag", "among": "tool", "action": "CAN_READ", "resource": "Mandate", "matched": []},
    ]


def test_a_task_no_skill_covers_is_refused_by_the_first_gateway(client):
    """The skills that fit are kept on the task; an intent none covers is refused in the gateway's words."""
    start_step(client, "r9")
    fit = {
        "subjectType": "Agent",
        "resourceType": "Company",
        "action": "CAN_RESTRICT",
        "matchedSkills": ["query-retriever"],
    }
    client.post(
        "/api/push-update",
        json={
            "service": "orchestrator-iag",
            "decision": "AUTHORIZED",
            "action": "IBAC_DISCOVERY",
            "capabilityName": "orchestrator_agent",
            "evaluations": [fit],
        },
    )
    step = client.get("/api/state").json()["runs"][-1]["steps"][0]
    assert step["fits"][0]["matched"] == ["query-retriever"]
    assert step["fits"][0]["among"] == "orchestrator_agent"
    client.post(
        "/api/push-update",
        json={
            "service": "orchestrator-iag",
            "decision": "NOT_AUTHORIZED",
            "action": "INTENT",
            "reason": "intent with 1 evaluation(s), none fits the skills of the agent",
        },
    )
    answer = client.post(
        "/api/sentinel",
        json={"type": "step_end", "run_id": "r9", "step": 0, "outcome": "refused", "answer": "", "http_status": 403},
    ).json()
    assert answer["observed"].startswith("orchestrator-iag refused the task's intent: intent with 1 evaluation")
