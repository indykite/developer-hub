# Copyright (c) 2026 IndyKite
"""Unit tests of the Sentinel's event reading and decisions; no network."""

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ["MISSION_FILE"] = str(ROOT / "usecases/cib/mission.json")
os.environ["SENTINEL_PLANNER"] = "scripted"
sys.path.insert(0, str(ROOT / "sentinel"))

import sentinel as mod  # noqa: E402

NODE = {
    "externalId": "mnd-1002",
    "type": "Mandate",
    "isIdentity": False,
    "properties": [{"type": "name", "value": {"stringValue": "Project Aurora"}}],
}
BODY = {"eventType": "indykite.audit.capture.upsert.node", "data": {"@type": "x", "node": NODE}}


def test_binary_cloudevent_is_read_from_attributes():
    event = mod.decode_message(
        json.dumps(BODY).encode(),
        {"ce-id": "e1", "ce-type": "indykite.audit.capture.upsert.node", "ce-time": "2026-11-12T10:00:00Z"},
    )
    assert event["id"] == "e1"
    assert event["type"].endswith("capture.upsert.node")
    assert event["body"]["data"]["node"]["externalId"] == "mnd-1002"


def test_structured_cloudevent_is_unwrapped():
    wire = {"specversion": "1.0", "id": "e2", "type": "indykite.audit.capture.upsert.node", "data": BODY}
    event = mod.decode_message(json.dumps(wire).encode(), {})
    assert (event["id"], event["type"]) == ("e2", "indykite.audit.capture.upsert.node")
    assert event["body"] == BODY


def test_unreadable_payload_does_not_raise():
    event = mod.decode_message(b"\xff\xfe", {"ce-type": "t"})
    assert event["type"] == "t"
    assert "raw" in event["body"]


def test_trigger_needs_a_watched_node_upsert():
    event = mod.decode_message(json.dumps(BODY).encode(), {"ce-type": "indykite.audit.capture.upsert.node"})
    node = mod.trigger_of(event, {"Mandate", "TradeOrder"})
    assert node == {"external_id": "mnd-1002", "type": "Mandate", "properties": {"name": "Project Aurora"}}
    assert mod.trigger_of(event, {"TradeOrder"}) is None
    event["type"] = "indykite.audit.authorization.evaluation"
    assert mod.trigger_of(event, {"Mandate"}) is None


def test_plain_property_values_are_kept():
    node = mod.find_node(
        {"node": {"external_id": "ord-1", "type": "TradeOrder", "properties": [{"type": "side", "value": "BUY"}]}},
    )
    assert node["properties"] == {"side": "BUY"}


def test_event_age():
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 120))
    assert 100 < mod.event_age_seconds(stamp) < 140
    assert mod.event_age_seconds("") is None
    assert mod.event_age_seconds("yesterday") is None


def test_classify():
    assert mod.classify("Restriction rst-mnd-1002 written", 200) == "done"
    assert mod.classify("Seems like you are not authorized to perform this action", 200) == "refused"
    assert mod.classify("", 403) == "refused"
    assert mod.classify("No data found (the graph does not allow this for the caller)", 200) == "empty"
    assert mod.classify("boom", 500) == "failed"
    assert mod.classify("", 0) == "failed"
    assert mod.classify("Unable to extract intent from prompt.", 200, {"reason": "unclear"}) == "unmapped"
    assert mod.classify("I'm currently unable to retrieve the information due to a technical error.", 200) == "failed"
    assert mod.classify("MCP error. Detail: unhandled errors in a TaskGroup", 200) == "failed"
    assert (
        mod.classify(
            'The attempt to restrict the company failed. The server rejected the request with a "Bad Request" error.',
            200,
        )
        == "failed"
    )
    assert (
        mod.classify(
            "I was unable to place a hold on ord-7001 because the company is not currently on the restricted list.",
            200,
        )
        == "empty"
    )


def test_scripted_procedure_runs_in_order_and_escalates():
    agent = mod.Sentinel()
    node = {"external_id": "mnd-1003", "type": "Mandate", "properties": {"name": "Project Monsoon"}}
    first = agent.scripted_decision(node, [])
    assert first["action"] == "task"
    assert "mnd-1003" in first["instruction"]
    blocked = [
        {"index": 0, "title": first["title"], "instruction": first["instruction"], "outcome": "empty", "answer": ""},
    ]
    assert agent.scripted_decision(node, blocked)["action"] == "escalate"
    done = [{**blocked[0], "outcome": "done"}]
    second = agent.scripted_decision(node, done)
    assert second["action"] == "task"
    assert "rst-mnd-1003" in second["instruction"]
    assert "Project Monsoon" in second["instruction"]
    steps = agent.mission["procedures"]["Mandate"]["steps"]
    everything = [
        {"index": i, "title": s["title"], "instruction": "", "outcome": "done", "answer": ""}
        for i, s in enumerate(steps)
    ]
    assert agent.scripted_decision(node, everything)["action"] == "finish"


def test_a_refused_optional_step_does_not_escalate():
    agent = mod.Sentinel()
    node = {"external_id": "mnd-1002", "type": "Mandate", "properties": {"name": "Project Aurora"}}
    steps = agent.mission["procedures"]["Mandate"]["steps"]
    history = [
        {"index": i, "title": s["title"], "instruction": "", "outcome": "done", "answer": ""}
        for i, s in enumerate(steps)
    ]
    history[-1]["outcome"] = "refused"
    assert agent.scripted_decision(node, history)["action"] == "finish"


def test_a_task_without_an_intent_escalates_where_the_procedure_says_so():
    agent = mod.Sentinel()
    node = {"external_id": "mnd-1002", "type": "Mandate", "properties": {"name": "Project Aurora"}}
    history = [{"index": 0, "title": "Read the mandate", "instruction": "", "outcome": "unmapped", "answer": ""}]
    assert agent.scripted_decision(node, history)["action"] == "escalate"
