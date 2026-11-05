# Copyright (c) 2026 IndyKite
"""The console's own glue between the engine and the decision mapper."""

from types import SimpleNamespace

import pytest
from app.main import _decide_generated
from app.prompts import Persona


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
    persona = Persona(id="roy", type="User", name="Roy", role="r")
    requests = [
        request("User", "roy", "d1", "t1"),
        request("Customer", "ted", "d2", "t2"),
        request("User", "roy", "d3", "t3"),
    ]

    plans = await _decide_generated(ctx, requests, persona)

    assert [p["index"] for p in plans] == [0, 1, 2]
    assert [p["request"]["resource"]["id"] for p in plans] == ["d1", "d2", "d3"]
    assert [p["request"]["subject"]["type"] for p in plans] == ["User", "Customer", "User"]
    assert all(p["result"] == {"decision": True} for p in plans)
    # Each plan is mapped from the parameters its own request was generated with.
    assert [p["input_params"]["tier"] for p in plans] == ["t1", "t2", "t3"]
    # One listing per subject type, not one per request.
    assert sorted(platform.listings) == ["Customer", "User"]
