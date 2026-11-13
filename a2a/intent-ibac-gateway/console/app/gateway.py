# Copyright (c) 2026 IndyKite
"""A2A client for the fleet's Agent Gateways, kept as raw as the Intent Agent one.

The console sends a prompt to one gateway, the way any A2A client would: the
persona's own token as Bearer and a `SendMessage` with the prompt. What comes
back is one of three things, each kept whole for the wire tab:

- the protected agent's task (the gateway forwarded the message);
- a message from the gateway itself, carrying the Intent Agent's error under
  `metadata.intent_error` (there was no intent, nothing was forwarded);
- an HTTP error from the gateway (401 for a token it will not take, 403 for a
  refused admission, 502/504 when the Intent Agent could not be asked).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from app.a2a import A2A_VERSION, WELL_KNOWN_AGENT_CARD

SEND_METHODS = ("SendMessage",)


def build_message(*, prompt: str, message_id: str) -> dict[str, Any]:
    """Build the request a client sends a gateway: no subject in the metadata, the gateway resolves the caller."""
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "method": "SendMessage",
        "params": {"message": {"role": "ROLE_USER", "messageId": message_id, "parts": [{"text": prompt}]}},
    }


@dataclass
class GatewayAnswer:
    agent: str
    url: str
    http_status: int | None
    latency_ms: int
    request: dict[str, Any]
    request_headers: dict[str, str]
    body_text: str = ""
    envelope: dict[str, Any] | None = None
    rpc_error: dict[str, Any] | None = None
    transport_error: str | None = None
    # What the answer is: "task" (forwarded, the agent answered), "intent_error" (the gateway answered in
    # place of the agent), "refused" (HTTP 401/403), "error" (anything else).
    kind: str = "error"
    task: dict[str, Any] | None = None
    message: dict[str, Any] | None = None
    reply_text: str = ""
    intent_error: dict[str, Any] | None = None
    received: dict[str, Any] | None = None
    # What the gateways of the agent's downstream agents answered to its card requests, as the agent reported it.
    downstream: list[dict[str, Any]] = field(default_factory=list)
    skill: str | None = None
    error_text: str = ""
    extensions_declared: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """JSON view for the trace."""
        return {
            "agent": self.agent,
            "url": self.url,
            "http_status": self.http_status,
            "latency_ms": self.latency_ms,
            "request": self.request,
            "request_headers": self.request_headers,
            "envelope": self.envelope,
            "body_text": None if self.envelope is not None else self.body_text,
            "rpc_error": self.rpc_error,
            "transport_error": self.transport_error,
            "kind": self.kind,
            "reply_text": self.reply_text,
            "intent_error": self.intent_error,
            "received": self.received,
            "downstream": self.downstream,
            "skill": self.skill,
            "error_text": self.error_text,
        }


def _texts(parts: list[Any] | None) -> str:
    return "\n".join(p.get("text", "") for p in parts or [] if isinstance(p, dict) and p.get("text"))


_HTTP_ERROR = 400
_REFUSED = (401, 403)


def _failed(answer: GatewayAnswer, text: str) -> GatewayAnswer:
    status = answer.http_status or 0
    answer.kind = "refused" if status in _REFUSED else "error"
    answer.error_text = text
    return answer


def _read_task(answer: GatewayAnswer, task: dict[str, Any]) -> GatewayAnswer:
    answer.kind, answer.task = "task", task
    answer.reply_text = "\n".join(_texts(a.get("parts")) for a in task.get("artifacts") or [] if isinstance(a, dict))
    meta = task.get("metadata") or {}
    answer.received = meta.get("received") if isinstance(meta.get("received"), dict) else None
    answer.skill = meta.get("skill") if isinstance(meta.get("skill"), str) else None
    if isinstance(meta.get("downstream"), list):
        answer.downstream = [d for d in meta["downstream"] if isinstance(d, dict) and d.get("agent")]
    return answer


def _read_message(answer: GatewayAnswer, message: dict[str, Any]) -> GatewayAnswer:
    answer.message = message
    meta = message.get("metadata") or {}
    answer.intent_error = meta.get("intent_error") if isinstance(meta.get("intent_error"), dict) else None
    answer.reply_text = _texts(message.get("parts"))
    answer.kind = "intent_error" if answer.intent_error else "task"
    return answer


def _failure_of(answer: GatewayAnswer) -> str | None:
    """Return why the answer is not a result, or None when `envelope.result` is an object to read."""
    env = answer.envelope
    status = answer.http_status or 0
    if answer.transport_error:
        return answer.transport_error
    if env is None:
        return answer.body_text.strip() or f"HTTP {status}"
    if isinstance(env.get("error"), dict):
        answer.rpc_error = env["error"]
        return f"JSON-RPC {env['error'].get('code')}: {env['error'].get('message')}"
    if status >= _HTTP_ERROR:
        return str(env.get("message") or env.get("error") or env)
    if not isinstance(env.get("result"), dict):
        return "no result in the answer"
    return None


def _unwrap(result: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (task, message): the SDK wraps the object under `task` / `message` or writes it in place."""
    task = result.get("task") if isinstance(result.get("task"), dict) else None
    message = result.get("message") if isinstance(result.get("message"), dict) else None
    if task is None and message is None:
        if "status" in result or "artifacts" in result:
            task = result
        elif "parts" in result:
            message = result
    return task, message


def parse_answer(answer: GatewayAnswer) -> GatewayAnswer:
    """Classify the envelope: a task from the agent, a gateway message with intent_error, or an error."""
    failure = _failure_of(answer)
    if failure is not None:
        return _failed(answer, failure)
    task, message = _unwrap(answer.envelope["result"])  # type: ignore[index]  # _failure_of checked it
    if task is not None:
        return _read_task(answer, task)
    if message is not None:
        return _read_message(answer, message)
    return _failed(answer, "unreadable answer")


@dataclass
class CardAnswer:
    """What a gateway answered to GetExtendedAgentCard for one person."""

    agent: str
    http_status: int | None
    latency_ms: int
    skills: list[str] = field(default_factory=list)
    card: dict[str, Any] | None = None
    error: str = ""

    @property
    def kind(self) -> str:
        """Return `card` when a card came back, `refused` on 401/403, `error` otherwise."""
        if self.card is not None:
            return "card"
        if self.http_status in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            return "refused"
        return "error"

    def as_dict(self) -> dict[str, Any]:
        """JSON view."""
        return {
            "agent": self.agent,
            "kind": self.kind,
            "http_status": self.http_status,
            "latency_ms": self.latency_ms,
            "skills": self.skills,
            "card": self.card,
            "error": self.error,
        }


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def parse_card_answer(
    agent: str,
    status: int,
    envelope: dict[str, Any] | None,
    latency_ms: int,
    body_text: str = "",
) -> CardAnswer:
    """Classify a GetExtendedAgentCard answer: a card with its skill ids, a refusal, or an error with its text."""
    answer = CardAnswer(agent=agent, http_status=status, latency_ms=latency_ms)
    result = (envelope or {}).get("result") if envelope else None
    if status == httpx.codes.OK and isinstance(result, dict) and isinstance(result.get("skills"), list):
        answer.card = result
        answer.skills = [str(sk.get("id")) for sk in result["skills"] if isinstance(sk, dict) and sk.get("id")]
        return answer
    if envelope and isinstance(envelope.get("error"), dict):
        answer.error = str(envelope["error"].get("message") or envelope["error"])
    elif envelope and envelope.get("message"):
        answer.error = str(envelope["message"])
    else:
        answer.error = body_text[:200]
    return answer


class GatewayClient:
    """One client for all gateways: the base URL of each is looked up by agent id."""

    def __init__(self, urls: dict[str, str], timeout: float, client: httpx.AsyncClient | None = None) -> None:
        """`urls` maps an agent id to its gateway's in-network base URL; `client` is the shared pool when given."""
        self._urls = {k: v.rstrip("/") for k, v in urls.items()}
        self._timeout = timeout
        self._client = client

    async def _post(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(url, json=body, headers=headers, timeout=self._timeout)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.post(url, json=body, headers=headers)

    async def _get(self, url: str, wait_seconds: float) -> httpx.Response:
        if self._client is not None:
            return await self._client.get(url, timeout=wait_seconds)
        async with httpx.AsyncClient(timeout=wait_seconds) as client:
            return await client.get(url)

    @property
    def agents(self) -> list[str]:
        """The agent ids that have a gateway."""
        return list(self._urls)

    def url(self, agent: str) -> str:
        """JSON-RPC endpoint of the gateway in front of `agent`."""
        return f"{self._urls[agent]}/"

    async def send(self, *, agent: str, prompt: str, bearer: str | None, request_id: str) -> GatewayAnswer:
        """Send one prompt through the gateway of `agent` and classify what came back."""
        body = build_message(prompt=prompt, message_id=request_id)
        headers = {"Content-Type": "application/json", "A2A-Version": A2A_VERSION, "X-Request-ID": request_id}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        shown = {**headers}
        if bearer:
            shown["Authorization"] = f"Bearer {bearer[:12]}…{bearer[-6:]}"
        started = time.perf_counter()
        answer = GatewayAnswer(
            agent=agent,
            url=self.url(agent),
            http_status=None,
            latency_ms=0,
            request=body,
            request_headers=shown,
        )
        try:
            response = await self._post(answer.url, body, headers)
        except httpx.HTTPError as exc:
            answer.latency_ms = int((time.perf_counter() - started) * 1000)
            answer.transport_error = f"{type(exc).__name__}: {exc}"
            return parse_answer(answer)
        answer.latency_ms = int((time.perf_counter() - started) * 1000)
        answer.http_status = response.status_code
        answer.body_text = response.text
        try:
            env = response.json()
            answer.envelope = env if isinstance(env, dict) else None
        except ValueError:
            answer.envelope = None
        return parse_answer(answer)

    async def extended_card(self, *, agent: str, bearer: str, request_id: str) -> CardAnswer:
        """Ask the gateway of `agent` for the extended card as the bearer's owner sees it: the skills they may use."""
        body = {"jsonrpc": "2.0", "id": request_id, "method": "GetExtendedAgentCard", "params": {}}
        headers = {
            "Content-Type": "application/json",
            "A2A-Version": A2A_VERSION,
            "X-Request-ID": request_id,
            "Authorization": f"Bearer {bearer}",
        }
        started = time.perf_counter()
        try:
            response = await self._post(self.url(agent), body, headers)
        except httpx.HTTPError as exc:
            return CardAnswer(
                agent=agent,
                http_status=None,
                latency_ms=_ms(started),
                error=f"{type(exc).__name__}: {exc}",
            )
        try:
            env = response.json()
        except ValueError:
            env = None
        return parse_card_answer(
            agent,
            response.status_code,
            env if isinstance(env, dict) else None,
            _ms(started),
            response.text,
        )

    async def card(self, agent: str) -> dict[str, Any]:
        """Fetch the card the gateway serves for `agent` (public, no token): interface, security scheme, extension."""
        response = await self._get(f"{self._urls[agent]}{WELL_KNOWN_AGENT_CARD}", 5.0)
        response.raise_for_status()
        return response.json()

    async def healthy(self, agent: str) -> bool:
        """Whether the gateway answers its card."""
        try:
            await self.card(agent)
        except Exception:
            return False
        return True
