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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

IBAC_ACTIONS = frozenset({"IBAC_DISCOVERY", "IBAC_USAGE"})


def classify(record: dict[str, Any]) -> str:
    """Which component wrote the record - the four formats are told apart by their fields.

    The gateways and the Token Service share one record shape; a gateway names itself in `service`
    (its `JARVIS_SERVICE_NAME`, `iag-<agent>` in this demo) and owns every record it writes, the
    IBAC_* ones included. IBAC_* records without a gateway service come from the console's preview.
    """
    service = str(record.get("service", ""))
    if service.startswith("iag-"):
        return "agent-gateway"
    if record.get("action") in IBAC_ACTIONS:
        return "ibac-engine"
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
        self._seq = 0

    def authorized(self, presented_key: str | None) -> bool:
        """Constant-time check of the webhook API key (bytes: a non-ASCII header is a mismatch, not an error)."""
        return bool(presented_key) and hmac.compare_digest(presented_key.encode(), self._api_key.encode())

    async def publish(self, record: dict[str, Any]) -> dict[str, Any]:
        """Store a record and push it to every stream."""
        self._seq += 1
        entry = {
            "seq": self._seq,
            "received_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "source": classify(record),
            "record": record,
        }
        self._records.append(entry)
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

    async def wait_until(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        seq_after: int,
        max_wait: float,
        poll: float = 0.25,
    ) -> dict[str, Any] | None:
        """Return the first entry after `seq_after` that satisfies `predicate`, waiting up to `max_wait` seconds.

        For records that carry no request id (a gateway's, or the Intent Agent's when a gateway called
        it): the caller matches on the fields it knows, such as the subject and the prompt.
        """
        deadline = asyncio.get_running_loop().time() + max_wait
        while True:
            for entry in self.since(seq_after):
                if predicate(entry):
                    return entry
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await asyncio.sleep(poll)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Open a bounded queue that receives every entry published from now on."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._buffer_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Stop delivering to a queue returned by `subscribe`."""
        self._subscribers.discard(queue)
