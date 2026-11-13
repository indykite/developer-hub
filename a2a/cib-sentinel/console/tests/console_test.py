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


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def jwt(claims: dict) -> str:
    part = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"x.{part}.y"


def start_step(client, run="r1", step=0):
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
    assert "@clients" not in main.AGENT_NAMES
    assert main.alias("s1@clients") == "sentinel"
    assert main.alias("unknown") == "unknown"


def test_gateway_denial_overrides_the_step_outcome(client):
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
    client.post("/api/push-update", json={"service": "orchestrator-iag", "decision": "AUTHORIZED"})
    record = client.get("/api/state").json()["audit"][-1]
    assert "run_id" not in record


def test_exchanged_token_is_decoded(client):
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
    start_step(client, "r2")
    client.post("/api/push-update", json={"service": "retriever_agent", "decision": "ERROR"})
    answer = client.post(
        "/api/sentinel",
        json={"type": "step_end", "run_id": "r2", "step": 0, "outcome": "done", "answer": "", "http_status": 200},
    ).json()
    assert answer["outcome"] == "done"


def test_messages_and_escalations_are_kept(client):
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
    assert client.post("/api/feed/mandate-aurora").status_code == 503
    assert client.post("/api/feed/nope").status_code == 404


def test_intent_in_the_delegation_is_shown_on_the_task(client):
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
    board = main.Board()
    verdict = asyncio.run(
        board.decide({"subject": {"id": "s"}, "action": {"name": "A"}, "resource": {"type": "T", "id": "x"}}),
    )
    assert verdict["verdict"] == "unchecked"
