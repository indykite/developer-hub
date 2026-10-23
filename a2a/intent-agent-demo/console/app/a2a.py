# Copyright (c) 2026 IndyKite
"""A2A client for the Intent Agent, kept deliberately raw.

The console speaks the JSON-RPC transport by hand instead of through an SDK so
the exact bytes on the wire - the `SendMessage` request, the task envelope -
can be shown next to the result. Only `SendMessage` is served by the agent.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

A2A_VERSION = "1.0"
WELL_KNOWN_AGENT_CARD = "/.well-known/agent-card.json"


def build_send_message(*, prompt: str, subject_type: str, subject_id: str, message_id: str) -> dict[str, Any]:
    """Build the request body the Agent Gateway sends: the caller's identity rides in message.metadata."""
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "method": "SendMessage",
        "params": {
            "message": {
                "role": "ROLE_USER",
                "messageId": message_id,
                "parts": [{"text": prompt}],
                "metadata": {"subject_type": subject_type, "subject_id": subject_id},
            },
        },
    }


@dataclass
class SendResult:
    http_status: int | None
    latency_ms: int
    request: dict[str, Any]
    request_headers: dict[str, str]
    body_text: str = ""
    envelope: dict[str, Any] | None = None
    task_state: str | None = None
    status_messages: list[str] = field(default_factory=list)
    payload: dict[str, Any] | None = None
    rpc_error: dict[str, Any] | None = None
    transport_error: str | None = None

    @property
    def outcome(self) -> str:
        """Coarse classification for the UI: success, refused, error."""
        if self.transport_error or self.rpc_error or (self.http_status or 0) >= httpx.codes.BAD_REQUEST:
            return "error"
        if self.payload is not None and "evaluations" in self.payload:
            return "success"
        return "refused"


def _task_of(envelope: dict[str, Any]) -> dict[str, Any] | None:
    """Return the task object of a SendMessage response, whether the SDK wrapped it in `task` or not."""
    result = envelope.get("result")
    if not isinstance(result, dict):
        return None
    task = result.get("task")
    return task if isinstance(task, dict) else result


def _status_messages(task: dict[str, Any]) -> list[str]:
    """Text the agent put into the task history (its progress messages), the user's own message excluded."""
    return [
        part["text"]
        for entry in task.get("history") or []
        if entry.get("role") != "ROLE_USER"
        for part in entry.get("parts") or []
        if isinstance(part, dict) and part.get("text")
    ]


def _artifact_payload(task: dict[str, Any]) -> dict[str, Any] | None:
    """Return the last JSON object in the task's artifacts: the agent's extraction result or error shape."""
    payload: dict[str, Any] | None = None
    for artifact in task.get("artifacts") or []:
        for part in artifact.get("parts") or []:
            text = part.get("text") if isinstance(part, dict) else None
            if not text:
                continue
            try:
                candidate = json.loads(text)
            except ValueError:
                continue
            if isinstance(candidate, dict):
                payload = candidate
    return payload


def parse_envelope(envelope: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None, list[str]]:
    """Pull the task state, the JSON artifact and the status messages out of a SendMessage response."""
    task = _task_of(envelope)
    if task is None:
        return None, None, []
    state = (task.get("status") or {}).get("state")
    return state, _artifact_payload(task), _status_messages(task)


class IntentAgentClient:
    """HTTP client for one Intent Agent: the JSON-RPC listener and the health/metrics listener."""

    def __init__(self, base_url: str, health_url: str, timeout: float) -> None:
        """`base_url` serves A2A, `health_url` serves /health and /metrics."""
        self._base = base_url.rstrip("/")
        self._health = health_url.rstrip("/")
        self._timeout = timeout

    @property
    def rpc_url(self) -> str:
        """The JSON-RPC endpoint: the agent serves it at `/`."""
        return f"{self._base}/"

    async def send(
        self,
        *,
        prompt: str,
        subject_type: str,
        subject_id: str,
        bearer: str | None,
        request_id: str,
    ) -> SendResult:
        """Send one prompt; `request_id` becomes both the JSON-RPC id and the X-Request-ID the audit record keeps."""
        body = build_send_message(
            prompt=prompt,
            subject_type=subject_type,
            subject_id=subject_id,
            message_id=request_id,
        )
        headers = {"Content-Type": "application/json", "A2A-Version": A2A_VERSION, "X-Request-ID": request_id}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        shown_headers = {**headers}
        if bearer:
            shown_headers["Authorization"] = f"Bearer {bearer[:12]}…{bearer[-6:]}"
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self.rpc_url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            return SendResult(
                http_status=None,
                latency_ms=int((time.perf_counter() - started) * 1000),
                request=body,
                request_headers=shown_headers,
                transport_error=f"{type(exc).__name__}: {exc}",
            )
        result = SendResult(
            http_status=response.status_code,
            latency_ms=int((time.perf_counter() - started) * 1000),
            request=body,
            request_headers=shown_headers,
            body_text=response.text,
        )
        try:
            envelope = response.json()
        except ValueError:
            return result
        if not isinstance(envelope, dict):
            return result
        result.envelope = envelope
        if isinstance(envelope.get("error"), dict):
            result.rpc_error = envelope["error"]
            return result
        result.task_state, result.payload, result.status_messages = parse_envelope(envelope)
        return result

    async def agent_card(self) -> dict[str, Any]:
        """Fetch the public agent card (skills, security schemes); no token needed."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(f"{self._base}{WELL_KNOWN_AGENT_CARD}")
        response.raise_for_status()
        return response.json()

    async def health(self) -> bool:
        """Return True when /health answers 200; any transport failure counts as down."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{self._health}/health")
        except httpx.HTTPError:
            return False
        return response.status_code == httpx.codes.OK

    async def metrics_text(self) -> str:
        """Raw Prometheus exposition from the health listener."""
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{self._health}/metrics")
        response.raise_for_status()
        return response.text


def summarize_metrics(text: str) -> dict[str, Any]:
    """Reduce the Prometheus exposition to the two numbers the dashboard shows."""
    total = 0.0
    duration_sum = 0.0
    duration_count = 0.0
    by_status: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, rest = line.partition(" ")
        try:
            value = float(rest.strip())
        except ValueError:
            continue
        if name.startswith("http_requests_total"):
            total += value
            status = _label(name, "status")
            if status:
                by_status[status] = by_status.get(status, 0.0) + value
        elif name.startswith("http_request_duration_seconds_sum"):
            duration_sum += value
        elif name.startswith("http_request_duration_seconds_count"):
            duration_count += value
    avg_ms = int(duration_sum / duration_count * 1000) if duration_count else None
    return {"requests_total": int(total), "by_status": by_status, "avg_latency_ms": avg_ms}


def _label(series: str, key: str) -> str | None:
    start = series.find(f'{key}="')
    if start < 0:
        return None
    start += len(key) + 2
    end = series.find('"', start)
    return series[start:end] if end > start else None
