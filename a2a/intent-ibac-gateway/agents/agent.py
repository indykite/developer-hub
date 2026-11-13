# Copyright (c) 2026 IndyKite
"""A stand-in A2A agent for one card of the fleet, protected by an Agent Gateway.

It serves the card as published in the console's fleet file (skills and
`x-coaz-mappings` included) and answers `SendMessage` with a completed task
whose reply names the agent and the skill, and whose metadata records what the
gateway forwarded: the `A2A-Extensions` header, the claims of the delegated
token in `X-IK-Token` and whether a bearer token travelled in `Authorization`.
Nothing is decided here; the agent is what the gateway guards.

An agent with `DOWNSTREAM_GATEWAYS` also does what an agent does before it hands
work on: with the delegated token it received, and nothing else, it asks the
gateway of each downstream agent for its extended card. That gateway sees a
later hop, and answers the skills the person may use there that fit the intent
the token carries. The agent reports each answer in the task metadata.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import jwt
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

AGENT_ID = os.environ["AGENT_ID"]
FLEET_FILE = Path(os.environ.get("FLEET_FILE", "/app/fleet.json"))
ADVERTISED_URL = os.environ.get("ADVERTISED_URL", f"http://{AGENT_ID}:8000/")
PORT = int(os.environ.get("PORT", "8000"))
A2A_VERSION = "1.0"
DOWNSTREAM_TIMEOUT = float(os.environ.get("DOWNSTREAM_TIMEOUT_SECONDS", "15"))


def _downstream() -> dict[str, str]:
    """Read DOWNSTREAM_GATEWAYS, `agent=gateway url` pairs separated by commas: the agents this one may hand work to."""
    pairs = (item.split("=", 1) for item in os.environ.get("DOWNSTREAM_GATEWAYS", "").split(",") if "=" in item)
    return {name.strip(): url.strip() for name, url in pairs if name.strip() and url.strip()}


DOWNSTREAM = _downstream()


def _card() -> dict[str, Any]:
    fleet = json.loads(FLEET_FILE.read_text(encoding="utf-8"))
    entry = next((a for a in fleet["agents"] if a["id"] == AGENT_ID), None)
    if entry is None:
        msg = f"agent {AGENT_ID!r} is not in {FLEET_FILE}"
        raise SystemExit(msg)
    card = dict(entry["card"])
    card["supportedInterfaces"] = [
        {"url": ADVERTISED_URL, "protocolBinding": "JSONRPC", "protocolVersion": A2A_VERSION},
    ]
    card["protocolVersion"] = A2A_VERSION
    card.setdefault("capabilities", {})
    return card


CARD = _card()
app = FastAPI(title=CARD["name"], docs_url=None, redoc_url=None)


@app.get("/.well-known/agent-card.json")
async def agent_card() -> dict[str, Any]:
    """Serve the card the gateway reads: name, skills with their mappings, the JSON-RPC interface."""
    return CARD


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness."""
    return {"status": "ok", "agent": AGENT_ID}


def _skill_for(prompt: str) -> dict[str, Any]:
    """Name the skill whose examples look most like the prompt; the first skill otherwise."""
    words = set(prompt.lower().split())
    best = CARD["skills"][0]
    score = -1
    for skill in CARD["skills"]:
        hits = sum(len(words & set(example.lower().split())) for example in skill.get("examples", []))
        if hits > score:
            best, score = skill, hits
    return best


def _received(request: Request) -> dict[str, Any]:
    """Collect what the gateway forwarded with the message, for the console to show."""
    delegated = request.headers.get("x-ik-token")
    claims: dict[str, Any] | None = None
    if delegated:
        try:
            claims = jwt.decode(delegated, options={"verify_signature": False})
        except jwt.PyJWTError:
            claims = {"error": "X-IK-Token is not a JWT"}
    bearer = request.headers.get("authorization", "")
    return {
        "a2a_extensions": request.headers.get("a2a-extensions"),
        "x_ik_token": claims,
        "authorization": f"Bearer {bearer[7:19]}…" if bearer.startswith("Bearer ") else None,
        "a2a_version": request.headers.get("a2a-version"),
    }


async def _ask_card(client: httpx.AsyncClient, agent: str, url: str, delegated: str) -> dict[str, Any]:
    """Ask one downstream gateway for its extended card, as a later hop: the delegated token and nothing else."""
    body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "GetExtendedAgentCard", "params": {}}
    headers = {"Content-Type": "application/json", "A2A-Version": A2A_VERSION, "X-IK-Token": delegated}
    answer: dict[str, Any] = {"agent": agent, "http_status": None, "skills": [], "error": ""}
    try:
        response = await client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        answer["error"] = f"{type(exc).__name__}: {exc}"
        return answer
    answer["http_status"] = response.status_code
    try:
        envelope = response.json()
    except ValueError:
        envelope = None
    result = envelope.get("result") if isinstance(envelope, dict) else None
    if response.status_code == httpx.codes.OK and isinstance(result, dict) and isinstance(result.get("skills"), list):
        answer["skills"] = [str(sk["id"]) for sk in result["skills"] if isinstance(sk, dict) and sk.get("id")]
    elif isinstance(envelope, dict):
        error = envelope.get("error")
        text = error.get("message") if isinstance(error, dict) else error
        answer["error"] = str(text or envelope.get("message") or "")
    else:
        answer["error"] = response.text[:200]
    return answer


async def _discover_downstream(delegated: str | None) -> list[dict[str, Any]]:
    """Ask every downstream gateway what it offers for the delegated token this agent holds."""
    if not delegated or not DOWNSTREAM:
        return []
    async with httpx.AsyncClient(timeout=DOWNSTREAM_TIMEOUT) as client:
        return list(await asyncio.gather(*(_ask_card(client, a, url, delegated) for a, url in DOWNSTREAM.items())))


@app.post("/")
async def rpc(request: Request) -> JSONResponse:
    """Answer SendMessage with a completed task; every other method is unsupported."""
    body = await request.json()
    rpc_id = body.get("id")
    method = body.get("method")
    if method not in ("SendMessage", "message/send"):
        error = {"code": -32004, "message": "This operation is not supported"}
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": error})
    message = (body.get("params") or {}).get("message") or {}
    prompt = "\n".join(p.get("text", "") for p in message.get("parts", []) if isinstance(p, dict) and p.get("text"))
    skill = _skill_for(prompt)
    received = _received(request)
    downstream = await _discover_downstream(request.headers.get("x-ik-token"))
    reply = (
        f"{CARD['name']} received the message through its gateway and would handle it with the skill "
        f"'{skill['name']}' ({skill['id']}). The delegated token names {_chain(received.get('x_ik_token'))}."
    )
    task_id = uuid.uuid4().hex
    task = {
        "id": task_id,
        "contextId": message.get("contextId") or uuid.uuid4().hex,
        "status": {"state": "TASK_STATE_COMPLETED", "timestamp": _now()},
        "history": [message],
        "artifacts": [{"artifactId": uuid.uuid4().hex, "name": "reply", "parts": [{"text": reply}]}],
        "metadata": {"agent": AGENT_ID, "skill": skill["id"], "received": received, "downstream": downstream},
    }
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": {"task": task}})


def _chain(claims: dict[str, Any] | None) -> str:
    if not claims or "sub" not in claims:
        return "no delegated token"
    hops = []
    act = claims.get("act")
    while isinstance(act, dict):
        hops.append(str(act.get("sub")))
        act = act.get("act")
    return f"{claims['sub']} acting through {' ← '.join(hops) or 'nobody'}"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")  # nosec B104  # noqa: S104  # container listener
