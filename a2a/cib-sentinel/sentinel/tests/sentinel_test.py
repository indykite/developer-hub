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
    """A binary-mode CloudEvent takes its id, type and time from the message attributes."""
    event = mod.decode_message(
        json.dumps(BODY).encode(),
        {"ce-id": "e1", "ce-type": "indykite.audit.capture.upsert.node", "ce-time": "2026-11-12T10:00:00Z"},
    )
    assert event["id"] == "e1"
    assert event["type"].endswith("capture.upsert.node")
    assert event["body"]["data"]["node"]["externalId"] == "mnd-1002"


def test_structured_cloudevent_is_unwrapped():
    """A structured-mode CloudEvent is unwrapped to the same id, type and body."""
    wire = {"specversion": "1.0", "id": "e2", "type": "indykite.audit.capture.upsert.node", "data": BODY}
    event = mod.decode_message(json.dumps(wire).encode(), {})
    assert (event["id"], event["type"]) == ("e2", "indykite.audit.capture.upsert.node")
    assert event["body"] == BODY


def test_unreadable_payload_does_not_raise():
    """Bytes that are not JSON are kept as raw data instead of raising."""
    event = mod.decode_message(b"\xff\xfe", {"ce-type": "t"})
    assert event["type"] == "t"
    assert "raw" in event["body"]


def test_trigger_needs_a_watched_node_upsert():
    """Only a node upsert of a watched type starts a mission."""
    event = mod.decode_message(json.dumps(BODY).encode(), {"ce-type": "indykite.audit.capture.upsert.node"})
    node = mod.trigger_of(event, {"Mandate", "TradeOrder"})
    assert node == {"external_id": "mnd-1002", "type": "Mandate", "properties": {"name": "Project Aurora"}}
    assert mod.trigger_of(event, {"TradeOrder"}) is None
    event["type"] = "indykite.audit.authorization.evaluation"
    assert mod.trigger_of(event, {"Mandate"}) is None


def test_plain_property_values_are_kept():
    """A property given as a plain value is read as it is."""
    node = mod.find_node(
        {"node": {"external_id": "ord-1", "type": "TradeOrder", "properties": [{"type": "side", "value": "BUY"}]}},
    )
    assert node["properties"] == {"side": "BUY"}


def test_event_age():
    """The age of a timestamp is computed; an empty or unreadable one has none."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 120))
    assert 100 < mod.event_age_seconds(stamp) < 140
    assert mod.event_age_seconds("") is None
    assert mod.event_age_seconds("yesterday") is None


def test_classify():
    """Each kind of answer gets its outcome: done, refused, failed, empty or unmapped."""
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
    """The written procedure runs step by step and escalates where a step says so."""
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
    """A refusal on a step without an escalation rule lets the procedure finish."""
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
    """A task with no readable intent counts as blocked for the escalation rule."""
    agent = mod.Sentinel()
    node = {"external_id": "mnd-1002", "type": "Mandate", "properties": {"name": "Project Aurora"}}
    history = [{"index": 0, "title": "Read the mandate", "instruction": "", "outcome": "unmapped", "answer": ""}]
    assert agent.scripted_decision(node, history)["action"] == "escalate"


def test_a_gateway_result_is_settled_or_still_running():
    """A result is final when completed, failed or refused for its intent; otherwise the task is still running."""
    done = {"status": {"state": "TASK_STATE_COMPLETED"}, "artifacts": [{"parts": [{"text": "ok"}]}]}
    assert mod._settled(done) == (200, "ok", None)
    assert mod._settled({"status": {"state": "TASK_STATE_FAILED"}}) == (502, "task ended TASK_STATE_FAILED", None)
    assert mod._settled({"id": "t1", "status": {"state": "TASK_STATE_WORKING"}}) is None
    assert mod._settled({"id": "t1"}) is None
    refusal = {"reason": "unclear"}
    refused = {"parts": [{"text": "Unable to extract intent."}], "metadata": {"intent_error": refusal}}
    assert mod._settled(refused) == (200, "Unable to extract intent.", refusal)


def test_a_task_is_polled_until_it_completes():
    """The task is sent once, then polled with tasks/get until the gateway reports it completed."""
    calls = []

    def gateway(request):
        """Answer the send with a working task, then the poll with the completed one."""
        body = json.loads(request.content)
        calls.append(body["method"])
        if body["method"] == "message/send":
            return mod.httpx.Response(200, json={"result": {"id": "t1", "status": {"state": "TASK_STATE_WORKING"}}})
        result = {"id": "t1", "status": {"state": "TASK_STATE_COMPLETED"}, "artifacts": [{"parts": [{"text": "held"}]}]}
        return mod.httpx.Response(200, json={"result": result})

    async def run():
        """Send one task through the stub gateway."""
        agent = mod.Sentinel()
        agent.http = mod.httpx.AsyncClient(transport=mod.httpx.MockTransport(gateway))
        agent._token, agent._token_exp = "t", time.time() + 3600
        return await agent.send_task("hold order ord-7001")

    assert mod.asyncio.run(run()) == (200, "held", None)
    assert calls == ["message/send", "tasks/get"]


def test_a_refusal_at_the_gateway_is_returned_with_its_status():
    """An HTTP refusal of the first hop comes back as its status and text."""

    async def run():
        """Send one task to a gateway that refuses it."""
        agent = mod.Sentinel()
        transport = mod.httpx.MockTransport(lambda _request: mod.httpx.Response(403, text="Authorization check failed"))
        agent.http = mod.httpx.AsyncClient(transport=transport)
        agent._token, agent._token_exp = "t", time.time() + 3600
        return await agent.send_task("read the term sheet")

    assert mod.asyncio.run(run()) == (403, "Authorization check failed", None)


def test_a_busy_sentinel_keeps_what_waits():
    """While a mission runs, a clear request drops nothing and says the Sentinel is busy; idle, it empties the queue."""

    async def run():
        """Queue one trigger, then ask to clear while busy and while idle."""
        mod.sentinel = mod.Sentinel()
        mod.sentinel.pending["Mandate:mnd-1"] = (time.time(), {"external_id": "mnd-1"})
        await mod.sentinel.queue.put({"external_id": "ord-1"})
        mod.sentinel.busy = True
        busy = await mod.clear()
        kept = len(mod.sentinel.pending) + mod.sentinel.queue.qsize()
        mod.sentinel.busy = False
        idle = await mod.clear()
        return busy, kept, idle, len(mod.sentinel.pending) + mod.sentinel.queue.qsize()

    busy, kept, idle, left = mod.asyncio.run(run())
    assert busy == {"ok": True, "dropped": 0, "busy": True}
    assert kept == 2
    assert idle == {"ok": True, "dropped": 2, "busy": False}
    assert left == 0


def test_the_scripted_order_procedure_holds_only_a_restricted_company():
    """The written step for an order asks for the hold only if its company is on the restricted list."""
    agent = mod.Sentinel()
    node = {"external_id": "ord-7005", "type": "TradeOrder", "properties": {"name": "BUY 2,000,000 KSTL 2031 notes"}}
    done = [{"index": 0, "title": "Read the restricted list", "instruction": "", "outcome": "done", "answer": ""}]
    instruction = agent.scripted_decision(node, done)["instruction"]
    assert "only if its company is listed" in instruction
    assert "otherwise leave the order unchanged" in instruction
    assert "hold-ord-7005" in instruction
