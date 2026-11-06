# Copyright (c) 2026 IndyKite
"""The console's own glue: generated requests to the decision mapper, gateway answers to the board."""

from types import SimpleNamespace

import pytest
from app.main import _act_chain, _decide_generated, summarize_card_answer, summarize_gateway_answer


class RecordingPlatform:
    """Answers ALLOW to every evaluation and remembers the subject type of each listing it was asked for."""

    def __init__(self):
        """init  ."""
        self.listings = []

    async def policies(self, subject_type):
        """List one policy that declares $tier, so the mapper turns the request's tier into an input_param."""
        self.listings.append(subject_type)
        policy = {
            "subject": {"type": subject_type},
            "actions": ["READ"],
            "resource": {"type": "Document"},
            "condition": {"cypher": "MATCH (subject)-[:READS]->(resource) WHERE resource.tier = $tier"},
        }
        return [{"policy": policy, "tags": []}]

    @staticmethod
    async def evaluations(body):
        """Allow everything."""
        return {"evaluations": [{"decision": True} for _ in body["evaluations"]]}

    @staticmethod
    async def search(_kind, _body):
        """Find nothing."""
        return {"results": []}


def request(subject_type, subject_id, resource_id, tier):
    """Build a generated request as the engine emits it, its params in context.input_params."""
    return {
        "subject": {"type": subject_type, "id": subject_id},
        "action": {"name": "READ"},
        "resource": {"type": "Document", "id": resource_id},
        "context": {"input_params": {"token": {"sub": subject_id}, "parameters": {"tier": tier}}},
    }


@pytest.mark.asyncio
async def test_generated_plans_keep_the_order_of_the_requests_across_subject_types():
    """Requests of two subject types interleave; each plan must sit at the position of its request."""
    platform = RecordingPlatform()
    ctx = SimpleNamespace(platform=platform)
    requests = [
        request("User", "roy", "d1", "t1"),
        request("Customer", "ted", "d2", "t2"),
        request("User", "roy", "d3", "t3"),
    ]

    plans = await _decide_generated(ctx, requests)

    assert [p["index"] for p in plans] == [0, 1, 2]
    assert [p["request"]["resource"]["id"] for p in plans] == ["d1", "d2", "d3"]
    assert [p["request"]["subject"]["type"] for p in plans] == ["User", "Customer", "User"]
    assert all(p["result"] == {"decision": True} for p in plans)
    # Each plan is mapped from the parameters its own request was generated with.
    assert [p["input_params"]["tier"] for p in plans] == ["t1", "t2", "t3"]
    # One listing per subject type, not one per request.
    assert sorted(platform.listings) == ["Customer", "User"]


def answer(**overrides):
    """One gateway answer as the client's as_dict() returns it, forwarded by default."""
    base = {
        "agent": "billing",
        "kind": "task",
        "http_status": 200,
        "latency_ms": 1200,
        "skill": "my-invoices",
        "reply_text": "Billing Agent received the message",
        "error_text": "",
        "transport_error": None,
        "received": {"x_ik_token": {"sub": "bob", "act": {"sub": "billing", "type": "Agent"}}},
    }
    return {**base, **overrides}


ADMITTED = {"action": "noop", "decision": "AUTHORIZED", "reason": "subject Customer/bob may reach Agent/billing"}
FOUND = {"action": "INTENT", "decision": "INTENT_FOUND", "durationMs": 1850}


def test_fleet_summary_reads_the_gateway_records_and_the_chain_the_agent_received():
    """A forwarded answer: verdict, skill, admission and INTENT from the records, the chain from X-IK-Token."""
    summary = summarize_gateway_answer(answer(), [ADMITTED, FOUND])

    assert summary["verdict"] == "forwarded"
    assert summary["skill"] == "my-invoices"
    assert summary["chain"] == ["bob", "billing"]
    assert summary["admission"] == {"decision": "AUTHORIZED", "reason": ADMITTED["reason"]}
    assert summary["intent"] == {"decision": "INTENT_FOUND", "duration_ms": 1850}


@pytest.mark.parametrize(
    ("overrides", "verdict"),
    [
        ({"kind": "intent_error", "received": None}, "no intent"),
        ({"kind": "refused", "http_status": 403, "received": None}, "admission refused"),
        ({"kind": "refused", "http_status": 401, "received": None}, "token refused"),
        ({"kind": "error", "http_status": 502, "received": None}, "error"),
        ({"kind": "error", "http_status": None, "transport_error": "connect timeout", "received": None}, "unreachable"),
    ],
)
def test_fleet_summary_names_every_way_a_gateway_can_stop(overrides, verdict):
    """Each outcome of a gateway has one word on the board; without a delegated token there is no chain."""
    summary = summarize_gateway_answer(answer(**overrides), [])

    assert summary["verdict"] == verdict
    assert summary["chain"] == []
    assert summary["admission"] is None
    assert summary["intent"] is None


def test_fleet_summary_reports_a_gateway_that_could_not_decide():
    """A 401 with an ERROR record is the gateway failing to decide, not the caller's token being refused."""
    failed = {
        "action": "noop",
        "decision": "ERROR",
        "reason": "unable to evaluate the admission of subject Customer/bob",
    }

    summary = summarize_gateway_answer(answer(kind="refused", http_status=401, received=None), [failed])

    assert summary["verdict"] == "admission error"
    assert summary["error_text"] == failed["reason"]


def test_act_chain_walks_nested_actors_outermost_first():
    """A two-hop delegation: the newest actor is the outer `act`, the older one nested inside it."""
    assert _act_chain({"sub": "billing", "act": {"sub": "orchestrator"}}) == ["billing", "orchestrator"]
    assert _act_chain(None) == []
    assert _act_chain({"type": "Agent"}) == []


def test_card_summary_splits_kept_and_hidden_skills_and_reads_the_discovery_record():
    """What the fleet page shows per card: the skills kept in the order of the card, the hidden ones, the record."""
    answer = {
        "agent": "support-desk",
        "kind": "card",
        "http_status": 200,
        "latency_ms": 40,
        "skills": ["customer-360", "policy-library"],
    }
    record = {
        "action": "DISCOVERY",
        "decision": "AUTHORIZED",
        "reason": "the skills of the card were filtered to the permitted set",
        "cached": {"skill": False},
        "discovery": {"serverCalled": True, "listed": 3, "kept": 2},
    }
    summary = summarize_card_answer(answer, ["policy-library", "customer-360", "case-decisions"], [record])
    assert summary["kept"] == ["policy-library", "customer-360"]
    assert summary["hidden"] == ["case-decisions"]
    assert summary["discovery"] == {
        "decision": "AUTHORIZED",
        "reason": record["reason"],
        "cached": False,
        "listed": 3,
        "kept": 2,
    }
    assert (
        summarize_card_answer({"agent": "weather", "kind": "refused", "http_status": 403}, ["forecast"], [])[
            "discovery"
        ]
        is None
    )
