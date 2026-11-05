# Copyright (c) 2026 IndyKite
"""The console's own glue between the engine and the decision mapper."""

from types import SimpleNamespace

import pytest
from app.main import _decide_generated
from app.prompts import Persona


class RecordingPlatform:
    """Answers ALLOW to every evaluation and remembers the subject type of each listing it was asked for."""

    def __init__(self):
        self.listings = []

    async def policies(self, subject_type):
        self.listings.append(subject_type)
        return []

    async def evaluations(self, body):
        return {"evaluations": [{"decision": True} for _ in body["evaluations"]]}

    async def search(self, _kind, _body):
        return {"results": []}


def request(subject_type, subject_id, resource_id):
    return {
        "subject": {"type": subject_type, "id": subject_id},
        "action": {"name": "READ"},
        "resource": {"type": "Document", "id": resource_id},
        "context": {"input_params": {"token": {"sub": subject_id}}},
    }


@pytest.mark.asyncio
async def test_generated_plans_keep_the_order_of_the_requests_across_subject_types():
    """Requests of two subject types interleave; each plan must sit at the position of its request."""
    platform = RecordingPlatform()
    ctx = SimpleNamespace(platform=platform)
    persona = Persona(id="roy", type="User", name="Roy", role="r")
    requests = [request("User", "roy", "d1"), request("Customer", "ted", "d2"), request("User", "roy", "d3")]

    plans = await _decide_generated(ctx, requests, {"tier": "tier_1"}, persona)

    assert [p["index"] for p in plans] == [0, 1, 2]
    assert [p["request"]["resource"]["id"] for p in plans] == ["d1", "d2", "d3"]
    assert [p["request"]["subject"]["type"] for p in plans] == ["User", "Customer", "User"]
    assert all(p["result"] == {"decision": True} for p in plans)
    # One listing per subject type, not one per request.
    assert sorted(platform.listings) == ["Customer", "User"]
