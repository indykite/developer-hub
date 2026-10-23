# Copyright (c) 2026 IndyKite
import asyncio

import pytest
from app.audit import AuditHub, classify

INTENT_RECORD = {"request_id": "r1", "prompt": "x", "outcome": "success", "evaluations": []}
ITS_RECORD = {
    "decision": "TOKEN_EXCHANGED",
    "actor": "intent-demo-console",
    "subject": "leslie",
    "service": "token-service",
}


def test_classify_tells_the_two_sources_apart():
    """Classify tells the two sources apart."""
    assert classify(INTENT_RECORD) == "intent-agent"
    assert classify(ITS_RECORD) == "token-service"
    assert classify({"anything": 1}) == "unknown"


def test_api_key_check_is_exact():
    """Api key check is exact."""
    hub = AuditHub("secret", 10)
    assert hub.authorized("secret")
    assert not hub.authorized("secre")
    assert not hub.authorized(None)


@pytest.mark.asyncio
async def test_wait_for_resolves_when_the_record_arrives():
    """Wait for resolves when the record arrives."""
    hub = AuditHub("k", 10)
    waiter = asyncio.create_task(hub.wait_for("r1", max_wait=2))
    await asyncio.sleep(0)
    entry = await hub.publish(INTENT_RECORD)
    assert await waiter == entry
    assert hub.find("r1") == entry
    assert hub.recent()[-1]["seq"] == 1


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
async def test_wait_for_gives_up():
    """Wait for gives up."""
    hub = AuditHub("k", 10)
    assert await hub.wait_for("missing", max_wait=0.01) is None


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
