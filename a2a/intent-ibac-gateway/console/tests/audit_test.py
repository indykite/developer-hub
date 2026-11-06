# Copyright (c) 2026 IndyKite
import pytest
from app.audit import AuditHub, classify

INTENT_RECORD = {"request_id": "r1", "prompt": "x", "outcome": "success", "evaluations": []}
ITS_RECORD = {
    "decision": "TOKEN_EXCHANGED",
    "actor": "intent-ibac-console",
    "subject": "leslie",
    "service": "token-service",
}


IBAC_RECORD = {
    "decision": "AUTHORIZED",
    "actor": "",
    "action": "IBAC_DISCOVERY",
    "capabilityName": "Billing Agent",
    "skillIDs": ["my-invoices"],
}


def test_classify_tells_the_three_sources_apart():
    """Classify tells the three sources apart."""
    assert classify(INTENT_RECORD) == "intent-agent"
    assert classify(ITS_RECORD) == "token-service"
    assert classify(IBAC_RECORD) == "ibac-engine"
    assert classify({**IBAC_RECORD, "action": "IBAC_USAGE"}) == "ibac-engine"
    assert classify({"anything": 1}) == "unknown"


def test_api_key_check_is_exact():
    """Api key check is exact."""
    hub = AuditHub("secret", 10)
    assert hub.authorized("secret")
    assert not hub.authorized("secre")
    assert not hub.authorized(None)
    assert not hub.authorized("sécret")  # non-ASCII is a mismatch, not a server error


@pytest.mark.asyncio
async def test_since_returns_only_entries_after_the_bookmark():
    """Since returns only entries after the bookmark."""
    hub = AuditHub("k", 10)
    await hub.publish({**ITS_RECORD, "n": 0})
    bookmark = hub.last_seq
    await hub.publish({**ITS_RECORD, "n": 1})
    await hub.publish({**INTENT_RECORD, "n": 2})
    assert [e["record"]["n"] for e in hub.since(bookmark)] == [1, 2]
    assert hub.since(hub.last_seq) == []


@pytest.mark.asyncio
async def test_subscribers_receive_every_record_and_the_ring_is_bounded():
    """A subscriber that keeps up sees every record; the ring keeps only the newest buffer_size entries."""
    hub = AuditHub("k", 2)
    queue = hub.subscribe()
    seen = []
    for i in range(3):
        await hub.publish({**ITS_RECORD, "n": i})
        seen.append(queue.get_nowait()["record"]["n"])
    assert seen == [0, 1, 2]
    assert [e["record"]["n"] for e in hub.recent()] == [1, 2]
    hub.unsubscribe(queue)


@pytest.mark.asyncio
async def test_slow_subscriber_loses_oldest_entries_and_never_blocks_publish():
    """A full stream queue drops its oldest entry so the webhook delivery always succeeds."""
    hub = AuditHub("k", 2)
    queue = hub.subscribe()
    for i in range(5):
        await hub.publish({**ITS_RECORD, "n": i})
    assert queue.qsize() == 2
    assert [queue.get_nowait()["record"]["n"] for _ in range(2)] == [3, 4]


def test_a_gateway_record_is_told_from_a_token_service_one_by_its_service_name():
    """Both share the shape; a gateway names itself iag-<agent> in `service`."""
    gateway = {**ITS_RECORD, "service": "iag-billing", "action": "INTENT", "decision": "INTENT_FOUND"}
    assert classify(gateway) == "agent-gateway"
    assert classify({**ITS_RECORD, "service": "token-service"}) == "token-service"
