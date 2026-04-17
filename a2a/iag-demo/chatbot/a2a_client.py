"""A2A client that forwards messages to the Orchestrator Agent via a plain HTTP gateway.

The gateway is a simple reverse proxy — it has no A2A agent card and does not implement
agent discovery. We post raw JSON-RPC 2.0 directly and poll tasks/get ourselves.
No SDK client or card resolver is involved here.
"""

import asyncio
import logging
import os
import uuid

import httpx
from httpx import HTTPStatusError

logger = logging.getLogger(__name__)

# LLM responses can take a long time; use 5 min default, configurable via env
ORCHESTRATOR_TIMEOUT = float(os.getenv("ORCHESTRATOR_TIMEOUT", "300"))


def _extract_text_from_response(result: dict) -> str:  # noqa: C901
    """Extract plain text from a JSON-RPC result dict (Task or Message shape).

    The 1.0 wire format uses proto-JSON field names (snake_case) and nested
    structures. We handle both Task (with artifacts) and direct Message responses.
    """
    if not result:
        return ""

    # Task shape: result.artifacts[].parts[].text
    artifacts = result.get("artifacts") or []
    if artifacts:
        chunks: list[str] = []
        for artifact in artifacts:
            for part in artifact.get("parts") or []:
                t = part.get("text")
                if t:
                    chunks.append(t)
        if chunks:
            return "".join(chunks)

    # Direct Message shape: result.parts[].text
    parts = result.get("parts") or []
    if parts:
        chunks = []
        for part in parts:
            t = part.get("text")
            if t:
                chunks.append(t)
        if chunks:
            return "".join(chunks)

    return ""


def _build_send_message_payload(text: str) -> dict:
    """Build a JSON-RPC 2.0 message/send payload in A2A 1.0 wire format."""
    return {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "message/send",
        "params": {
            "message": {
                # 1.0 proto enum: ROLE_USER (not the 0.3 string "user")
                "role": "ROLE_USER",
                "parts": [{"text": text}],
                "messageId": uuid.uuid4().hex,
            },
        },
    }


def _build_get_task_payload(task_id: str) -> dict:
    """Build a JSON-RPC 2.0 tasks/get payload."""
    return {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "tasks/get",
        "params": {"id": task_id},
    }


_TERMINAL_STATES = {
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_REJECTED",
    "TASK_STATE_CANCELED",
    # also accept lower-case variants in case the gateway normalises them
    "completed",
    "failed",
    "rejected",
    "cancelled",
    "canceled",
}


def _is_terminal_state(state: str | None) -> bool:
    return bool(state and state in _TERMINAL_STATES)


def _is_completed(state: str | None) -> bool:
    return state in ("TASK_STATE_COMPLETED", "completed")


async def _send_text_async(  # noqa: C901,PLR0911,PLR0912
    gateway_url: str,
    text: str,
    access_token: str | None = None,
) -> str:
    """POST a message to the gateway and poll until the task is complete.

    The gateway is a plain HTTP reverse proxy — we speak raw JSON-RPC 2.0
    directly. No A2A SDK client or agent card resolution is used.
    """
    timeout = httpx.Timeout(ORCHESTRATOR_TIMEOUT)
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        # ── 1. Send the message ──────────────────────────────────────────────
        payload = _build_send_message_payload(text)
        logger.info("Sending message to %s", gateway_url)
        try:
            resp = await client.post(gateway_url, json=payload)
            resp.raise_for_status()
        except HTTPStatusError as e:
            code = e.response.status_code
            if code in (401, 403):
                return "Seems like you are not authorized to perform this action"
            return "I was unable to process your request, check the audit log terminal"
        except Exception:
            logger.exception("Gateway send failed")
            return "I was unable to process your request, check the audit trace"

        body = resp.json()
        if "error" in body:
            logger.warning("JSON-RPC error from gateway: %s", body["error"])
            return "I was unable to process your request, check the audit log terminal"

        result = body.get("result") or {}

        # ── 2. Check if we already have a terminal result ───────────────────
        # Gateway may return the finished task immediately (synchronous path)
        state = (result.get("status") or {}).get("state")
        if _is_completed(state):
            return _extract_text_from_response(result)
        if _is_terminal_state(state):
            return "I was unable to process your request, check the audit log terminal"

        # Direct Message response (no task lifecycle)
        if result.get("parts"):
            return _extract_text_from_response(result)

        # ── 3. Poll tasks/get until terminal ────────────────────────────────
        task_id = result.get("id")
        if not task_id:
            logger.warning("No task id in gateway response: %s", result)
            return "I was unable to process your request, check the audit trace"

        logger.info("Polling task: %s", task_id)
        max_retries = 100

        for _ in range(max_retries):
            await asyncio.sleep(2)
            poll_payload = _build_get_task_payload(task_id)
            try:
                poll_resp = await client.post(gateway_url, json=poll_payload)
                poll_resp.raise_for_status()
            except HTTPStatusError as e:
                code = e.response.status_code
                if code in (401, 403):
                    return "Seems like you are not authorized to perform this action"
                return "I was unable to process your request, check the audit log terminal"
            except Exception:
                logger.exception("Gateway poll failed")
                return "I was unable to process your request, check the audit trace"

            poll_body = poll_resp.json()
            if "error" in poll_body:
                logger.warning("JSON-RPC poll error: %s", poll_body["error"])
                return "I was unable to process your request, check the audit log terminal"

            poll_result = poll_body.get("result") or {}
            state = (poll_result.get("status") or {}).get("state")
            logger.info("Task %s state: %s", task_id, state)

            if _is_completed(state):
                return _extract_text_from_response(poll_result)
            if _is_terminal_state(state):
                return "I was unable to process your request, check the audit log terminal"

        return "I've reached the maximum number of retries trying to get a response"


async def stream_to_orchestrator(
    text: str,
    *,
    host: str,
    port: int,
    context_id: str | None = None,
    access_token: str | None = None,
):
    url = f"http://{host}:{port}"
    ctx_id = context_id or str(uuid.uuid4())

    try:
        final_text = await _send_text_async(url, text, access_token)
        yield {"type": "done", "text": final_text, "context_id": ctx_id}
    except Exception as e:
        logger.exception("Orchestrator request failed")
        yield {"type": "error", "error": str(e)}


def send_to_orchestrator(
    text: str,
    *,
    host: str,
    port: int,
    access_token: str | None = None,
) -> str:
    """Send text to the Orchestrator Agent and return the response. Blocking/sync."""
    url = f"http://{host}:{port}"
    return asyncio.run(_send_text_async(url, text, access_token))
