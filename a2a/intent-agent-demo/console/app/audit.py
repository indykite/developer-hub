# Copyright (c) 2026 IndyKite
"""Audit hub: the webhook both the Intent Agent and the Token Service deliver to, fanned out live.

Records are kept in a bounded ring (newest last) and pushed to every open
server-sent-events stream. A prompt is matched to its audit record through the
`request_id` the console set in `X-Request-ID`.
"""

from __future__ import annotations

import asyncio
import hmac
from collections import deque
from datetime import UTC, datetime
from typing import Any


def classify(record: dict[str, Any]) -> str:
    """Which service wrote the record - the two formats share no field names."""
    if "outcome" in record and "prompt" in record:
        return "intent-agent"
    if "decision" in record and "actor" in record:
        return "token-service"
    return "unknown"


class AuditHub:
    """Bounded record ring plus live fan-out; one instance per process."""

    def __init__(self, api_key: str, buffer_size: int) -> None:
        """`api_key` is what senders must present in X-API-Key; `buffer_size` bounds the ring and each stream."""
        self._api_key = api_key
        self._buffer_size = buffer_size
        self._records: deque[dict[str, Any]] = deque(maxlen=buffer_size)
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._seq = 0

    def authorized(self, presented_key: str | None) -> bool:
        """Constant-time check of the webhook API key."""
        return bool(presented_key) and hmac.compare_digest(presented_key, self._api_key)

    async def publish(self, record: dict[str, Any]) -> dict[str, Any]:
        """Store a record, wake the prompt waiting for it, push it to every stream."""
        self._seq += 1
        entry = {
            "seq": self._seq,
            "received_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "source": classify(record),
            "record": record,
        }
        self._records.append(entry)
        request_id = record.get("request_id")
        if isinstance(request_id, str) and (waiter := self._waiters.pop(request_id, None)) and not waiter.done():
            waiter.set_result(entry)
        for queue in list(self._subscribers):
            if queue.full():
                # A stream slower than the record rate loses its oldest entry; delivery never blocks or fails.
                queue.get_nowait()
            queue.put_nowait(entry)
        return entry

    @property
    def last_seq(self) -> int:
        """Sequence number of the newest entry; a bookmark for `since`."""
        return self._seq

    def since(self, seq: int) -> list[dict[str, Any]]:
        """Entries published after the bookmark `seq`, oldest first."""
        return [entry for entry in self._records if entry["seq"] > seq]

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the newest `limit` entries, oldest first."""
        return list(self._records)[-limit:]

    def find(self, request_id: str) -> dict[str, Any] | None:
        """Return the newest entry whose record carries this request id."""
        for entry in reversed(self._records):
            if entry["record"].get("request_id") == request_id:
                return entry
        return None

    async def wait_for(self, request_id: str, max_wait: float) -> dict[str, Any] | None:
        """Block until the record for `request_id` arrives, or give up after `max_wait` seconds."""
        if found := self.find(request_id):
            return found
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._waiters[request_id] = waiter
        try:
            return await asyncio.wait_for(waiter, max_wait)
        except TimeoutError:
            return None
        finally:
            self._waiters.pop(request_id, None)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Open a bounded queue that receives every entry published from now on."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._buffer_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Stop delivering to a queue returned by `subscribe`."""
        self._subscribers.discard(queue)
