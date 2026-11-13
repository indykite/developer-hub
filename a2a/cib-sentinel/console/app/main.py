# Copyright (c) 2026 IndyKite
"""Control-room console of the cib-sentinel app.

Shows what an autonomous agent does and where it is stopped. It receives
three streams and merges them into one picture:

* the Sentinel's own records (the Pub/Sub messages it pulled, its missions,
  thoughts, tasks, outcomes, escalations) on ``/api/sentinel``;
* the audit records of the Agent Gateways, the Token Service and the fleet
  agents on ``/api/push-update``;
* the graph: the provisioned seed, what the feed wrote, and what the Sentinel
  wrote, read back from the knowledge graph.

It also plays the upstream system that feeds the graph: ``/api/feed/<event>``
writes a mandate or an order through the Capture API, which is what the event
sink publishes and the Sentinel reacts to.
"""

import asyncio
import base64
import json
import logging
import os
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("console")

PORT = int(os.getenv("CONSOLE_PORT", "3500"))
STATIC = Path(__file__).resolve().parent.parent / "static"
USECASE_DIR = Path(os.getenv("USECASE_DIR") or "/app/usecase")
BASE_URL = (os.getenv("INDYKITE_BASE_URL") or "").rstrip("/")
APP_TOKEN = (os.getenv("APP_AGENT_CREDENTIALS_TOKEN") or "").strip()
SENTINEL_URL = (os.getenv("SENTINEL_URL") or "http://sentinel:7100").rstrip("/")
BRIDGE_URL = (os.getenv("BRIDGE_URL") or "http://auth0-bridge:8090").rstrip("/")
RESTRICTIONS_QUERY_ID = (os.getenv("BOARD_RESTRICTIONS_QUERY_ID") or "").strip()
HOLDS_QUERY_ID = (os.getenv("BOARD_HOLDS_QUERY_ID") or "").strip()
ORG_NAME = (os.getenv("ORG_NAME") or "Meridian").strip()
ORG_TAGLINE = (os.getenv("ORG_TAGLINE") or "").strip()

# "<subject>=<alias>,..." - an Auth0 M2M token's sub is <client_id>@clients;
# the console shows the agent's name and keeps the subject as a tooltip.
AGENT_NAMES = {
    sub.strip(): alias.strip()
    for sub, _, alias in (pair.partition("=") for pair in (os.getenv("AGENT_DISPLAY_NAMES") or "").split(","))
    if sub.strip() and not sub.strip().startswith("@") and alias.strip()
}
DENIED = {"not_authorized", "deny", "denied", "error"}


def load(name: str) -> dict:
    """Read one JSON file of the usecase bundle."""
    with (USECASE_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


def alias(subject: str | None) -> str:
    """Return the agent's display name for a token subject, or the subject itself."""
    return AGENT_NAMES.get(subject or "", subject or "")


def jwt_claims(token: str) -> dict:
    """Claims of a JWT without verifying it (display only)."""
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}


def act_chain(claims: dict) -> list[str]:
    """Actors of a delegation token, oldest first."""
    chain, act = [], claims.get("act")
    while isinstance(act, dict) and act.get("sub"):
        chain.append(act["sub"])
        act = act.get("act")
    return list(reversed(chain))


class Board:
    """Everything the page shows, kept in memory."""

    def __init__(self) -> None:
        """Load the usecase bundle and start with an empty board."""
        self.seed = load("graph.json")
        self.catalog = {e["id"]: e for e in load("events.json")["events"]}
        self.mission = load("mission.json")
        self.http = httpx.AsyncClient(timeout=30)
        self.clients: set[asyncio.Queue] = set()
        self.bridge: dict = {}
        self.reset_state()

    def reset_state(self) -> None:
        """Forget every record; the Sentinel's identity is kept."""
        self.messages: deque[dict] = deque(maxlen=300)
        self.audit: deque[dict] = deque(maxlen=800)
        self.runs: dict[str, dict] = {}
        self.escalations: list[dict] = []
        self.heartbeat: dict = {}
        self.identity: dict = getattr(self, "identity", {})
        self.fed: list[str] = []
        self.current: tuple[str, int] | None = None
        self.written: dict[str, list[dict]] = {"restrictions": [], "holds": []}
        self.board_live = False

    # ---- stream
    def publish(self, kind: str, data: Any) -> None:  # noqa: ANN401
        """Send one record to every open page."""
        for queue in list(self.clients):
            with suppress(asyncio.QueueFull):
                queue.put_nowait({"kind": kind, "data": data})

    # ---- graph
    def graph(self) -> dict:
        """Merge the provisioned graph with what the feed and the Sentinel wrote."""
        nodes = {n["id"]: {**n, "origin": "seed"} for n in self.seed["nodes"]}
        edges = [{**e, "origin": "seed"} for e in self.seed["edges"]]

        def add_node(typ: str, ext: str, label: str, origin: str, props: dict | None = None) -> str:
            nid = f"{typ}:{ext}"
            nodes.setdefault(
                nid,
                {"id": nid, "type": typ, "label": label or ext, "props": props or {}, "origin": origin},
            )
            return nid

        def add_edge(src: str, typ: str, dst: str, origin: str) -> None:
            if (
                src in nodes
                and dst in nodes
                and not any(e["source"] == src and e["target"] == dst and e["type"] == typ for e in edges)
            ):
                edges.append({"source": src, "target": dst, "type": typ, "workflow": "", "origin": origin})

        for event_id in self.fed:
            event = self.catalog[event_id]
            for n in event["nodes"]:
                props = {p["type"]: p["value"] for p in n["properties"]}
                add_node(n["type"], n["external_id"], props.get("name", ""), "feed", props)
            for r in event["relationships"]:
                add_edge(
                    f"{r['source']['type']}:{r['source']['external_id']}",
                    r["type"],
                    f"{r['target']['type']}:{r['target']['external_id']}",
                    "feed",
                )
        for row in self.written["restrictions"]:
            reason = {"reason": row.get("reason", "")}
            rid = add_node("Restriction", row["restriction"], "Restriction", "sentinel", reason)
            add_edge(rid, "RESTRICTS", f"Company:{row['company']}", "sentinel")
            add_edge(rid, "ARISES_FROM", f"Mandate:{row['mandate']}", "sentinel")
            add_edge(f"Agent:{alias(row['agent'])}", "RAISED", rid, "sentinel")
        for row in self.written["holds"]:
            hid = add_node("Hold", row["hold"], "Hold", "sentinel", {"reason": row.get("reason", "")})
            add_edge(hid, "HOLDS", f"TradeOrder:{row['order']}", "sentinel")
            add_edge(f"Agent:{alias(row['agent'])}", "RAISED", hid, "sentinel")
        return {
            "nodes": list(nodes.values()),
            "edges": edges,
            "capabilities": self.seed.get("capabilities", {}),
            "live": self.board_live,
        }

    async def ciq(self, query_id: str) -> list[dict]:
        """Run one of the board's knowledge queries with the application's credentials."""
        resp = await self.http.post(
            f"{BASE_URL}/contx-iq/v1/execute",
            headers={"Content-Type": "application/json", "X-IK-ClientKey": APP_TOKEN},
            json={"id": query_id, "input_params": {}},
        )
        resp.raise_for_status()
        return [row.get("nodes") or {} for row in (resp.json() or {}).get("data") or []]

    async def refresh_written(self) -> None:
        """Read back from the graph what the Sentinel wrote (restrictions and holds)."""
        if not (BASE_URL and APP_TOKEN and RESTRICTIONS_QUERY_ID and HOLDS_QUERY_ID):
            return
        try:
            restrictions = await self.ciq(RESTRICTIONS_QUERY_ID)
            holds = await self.ciq(HOLDS_QUERY_ID)
        except httpx.HTTPError as exc:
            log.warning("board queries failed: %s", exc)
            return
        self.written = {
            "restrictions": [
                {
                    "restriction": r.get("restriction.external_id"),
                    "company": r.get("company.external_id"),
                    "mandate": r.get("mandate.external_id"),
                    "agent": r.get("agent.external_id"),
                    "reason": r.get("restriction.property.reason") or "",
                }
                for r in restrictions
                if r.get("restriction.external_id")
            ],
            "holds": [
                {
                    "hold": h.get("hold.external_id"),
                    "order": h.get("trade.external_id"),
                    "agent": h.get("agent.external_id"),
                    "reason": h.get("hold.property.reason") or "",
                }
                for h in holds
                if h.get("hold.external_id")
            ],
        }
        self.board_live = True
        self.publish("graph", self.graph())

    # ---- capture (the upstream system that feeds the graph)
    async def check_intent(self, run_id: str, index: int) -> None:
        """Ask the platform for a decision on each evaluation of a task's intent.

        The gateway carries the intent in the delegation token and does not decide on it; this is the
        console's own check, with the application's credentials, so the page can show a verdict per evaluation.
        """
        run = self.runs.get(run_id)
        if not run or index >= len(run["steps"]):
            return
        for entry in run["steps"][index]["intent"]:
            entry.update(await self.decide(entry["evaluation"]))
        self.publish("run", run)

    async def decide(self, evaluation: dict) -> dict:
        """One evaluation -> {"verdict", "detail"}: a decision when it names a resource, a search when it does not."""
        subject, action = evaluation.get("subject") or {}, evaluation.get("action") or {}
        resource = evaluation.get("resource") or {}
        if not (BASE_URL and APP_TOKEN):
            return {"verdict": "unchecked", "detail": "the console has no platform credentials"}
        if not (subject.get("id") and action.get("name") and resource.get("type")):
            return {"verdict": "unchecked", "detail": "this shape of evaluation is not checked here"}
        body: dict[str, Any] = {"subject": subject, "action": action, "resource": resource}
        parameters = (evaluation.get("context") or {}).get("parameters")
        if parameters:
            body["context"] = {"input_params": parameters}
        search = not resource.get("id")
        try:
            resp = await self.http.post(
                f"{BASE_URL}/access/v1/{'search/resource' if search else 'evaluation'}",
                headers={"Content-Type": "application/json", "X-IK-ClientKey": APP_TOKEN},
                json=body,
            )
        except httpx.HTTPError as exc:
            return {"verdict": "unchecked", "detail": f"the check could not be sent: {exc}"}
        if resp.status_code != httpx.codes.OK:
            return {"verdict": "unchecked", "detail": f"the platform answered {resp.status_code}: {resp.text[:200]}"}
        answer = resp.json() or {}
        if search:
            found = [r.get("id") for r in answer.get("results") or [] if r.get("id")]
            return {
                "verdict": "permitted" if found else "denied",
                "detail": ", ".join(found[:8]) or "nothing of this type",
            }
        return {"verdict": "permitted" if answer.get("decision") else "denied", "detail": ""}

    async def capture(self, path: str, body: dict) -> int:
        """Send one Capture API request; a refusal becomes a 502 for the page."""
        if not (BASE_URL and APP_TOKEN):
            raise HTTPException(status_code=503, detail="INDYKITE_BASE_URL / APP_AGENT_CREDENTIALS_TOKEN are not set")
        resp = await self.http.post(
            f"{BASE_URL}/capture/v1/{path}",
            headers={"Content-Type": "application/json", "X-IK-ClientKey": APP_TOKEN},
            json=body,
        )
        if resp.status_code >= httpx.codes.BAD_REQUEST:
            detail = f"Capture {path} answered {resp.status_code}: {resp.text[:300]}"
            raise HTTPException(status_code=502, detail=detail)
        return resp.status_code

    async def watch_bridge(self) -> None:
        """Follow the bridge's count of Auth0 grants: issued by Auth0 and answered from a kept token."""
        while True:
            try:
                resp = await self.http.get(f"{BRIDGE_URL}/stats", timeout=4)
                stats = resp.json() if resp.status_code == httpx.codes.OK else {}
            except (httpx.HTTPError, ValueError):
                stats = {}
            if stats != self.bridge:
                self.bridge = stats
                self.publish("bridge", stats)
            await asyncio.sleep(5)

    def snapshot(self) -> dict:
        """Everything a page needs when it opens."""
        return {
            "org": {"name": ORG_NAME, "tagline": ORG_TAGLINE},
            "messages": list(self.messages),
            "audit": list(self.audit),
            "runs": list(self.runs.values()),
            "escalations": self.escalations,
            "heartbeat": self.heartbeat,
            "identity": self.identity,
            "bridge": self.bridge,
            "fed": self.fed,
            "catalog": [
                {k: e[k] for k in ("id", "kind", "title", "blurb", "focus", "expect")} for e in self.catalog.values()
            ],
            "graph": self.graph(),
            "agents": AGENT_NAMES,
            "brief": self.mission.get("brief", ""),
            "now": time.time(),
        }


board: Board | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global board  # noqa: PLW0603
    board = Board()
    tasks = [asyncio.create_task(board.refresh_written()), asyncio.create_task(board.watch_bridge())]
    yield
    for task in tasks:
        task.cancel()
    await board.http.aclose()


app = FastAPI(title="cib-sentinel console", lifespan=lifespan)


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/api/state")
async def state() -> dict:
    return board.snapshot()


@app.get("/api/stream")
async def stream(request: Request) -> StreamingResponse:
    """Server-sent events: every record as it arrives."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    board.clients.add(queue)

    async def events():
        try:
            yield ": open\n\n"
            while not await request.is_disconnected():
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"event: {item['kind']}\ndata: {json.dumps(item['data'])}\n\n"
        finally:
            board.clients.discard(queue)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------ audit records
def layer_of(service: str) -> str:
    if service.endswith("-iag"):
        return "gateway"
    if "token" in service:
        return "token-service"
    return "agent"


@app.post("/api/push-update")
async def push_update(request: Request) -> dict:
    """Receive an audit record of a gateway, the Token Service or a fleet agent."""
    data = await request.json()
    service = str(data.get("service") or "unknown")
    decision = str(data.get("decision") or "").lower()
    record: dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "at": time.time(),
        "service": service,
        "layer": layer_of(service),
        "decision": decision,
        "action": str(data.get("action") or ""),
        "subject": alias(data.get("subject")),
        "actor": alias(data.get("actor")),
        "reason": data.get("reason") or "",
        "chain": [alias(a) for a in data.get("actorsChain") or []],
        "trace_id": data.get("traceID") or "",
        "cached": data.get("cached"),
        "permitted_set": data.get("permittedSet"),
        "discovery": data.get("discovery"),
        "capabilities": data.get("capabilities"),
        "reason_code": data.get("reasonCode") or "",
        "prompt": data.get("prompt") or "",
        "duration_ms": data.get("durationMs"),
    }
    intent: list = []
    if decision == "token_exchanged":
        token = str(record["reason"])
        claims = jwt_claims(token)
        carried = ((claims.get("act") or {}).get("intent") or {}).get("evaluations")
        intent = [e for e in carried if isinstance(e, dict)] if isinstance(carried, list) else []
        record.update(
            reason="",
            token=token,
            chain=[alias(a) for a in act_chain(claims)],
            subject=alias(claims.get("sub")),
            claims={k: claims.get(k) for k in ("iss", "sub", "aud", "exp", "iat", "act") if k in claims},
        )
    if board.current:
        run_id, step = board.current
        record["run_id"], record["step"] = run_id, step
        run = board.runs.get(run_id)
        if run and step < len(run["steps"]):
            target = run["steps"][step]
            target["records"].append(record["id"])
            if intent and not target["intent"]:
                # The first agent of the task reports the delegation it received: the task's intent is in it.
                target["intent"] = [{"evaluation": e, "verdict": "checking", "detail": ""} for e in intent]
                asyncio.get_running_loop().create_task(board.check_intent(run_id, step))
            if decision in DENIED and record["layer"] != "agent":
                target["denials"].append(
                    {"service": service, "action": record["action"], "reason": str(record["reason"])[:300]},
                )
    board.audit.append(record)
    board.publish("audit", record)
    return {"ok": True}


# ------------------------------------------------------- the Sentinel's feed
def summarize_message(data: dict) -> dict:
    event_type = str(data.get("event_type") or "")
    body = data.get("body") if isinstance(data.get("body"), dict) else {}
    return {
        "id": data.get("id") or uuid.uuid4().hex,
        "at": data.get("at") or time.time(),
        "event_type": event_type,
        "short": ".".join(event_type.split(".")[2:]) or event_type or "event",
        "time": data.get("time") or "",
        "source": data.get("source") or "pubsub",
        "stale": bool(data.get("stale")),
        "trigger": bool(data.get("trigger")),
        "node": data.get("node"),
        "body": body,
    }


def observed_of(step: dict) -> tuple[str, str]:
    """Return what the control plane said about a task: (outcome override, sentence)."""
    if not step["denials"]:
        return "", ""
    first = step["denials"][0]
    what = {"ADMISSION": "admission", "INVOCATION": "a capability", "DISCOVERY": "discovery"}.get(
        str(first["action"]).upper(),
        "the request",
    )
    return "refused", f"{first['service']} refused {what}" + (f": {first['reason']}" if first["reason"] else "")


@app.post("/api/sentinel")
async def sentinel_record(request: Request) -> JSONResponse:  # noqa: C901
    """One lifecycle record of the autonomous agent."""
    data = await request.json()
    kind = data.get("type")
    answer: dict[str, Any] = {"ok": True}
    if kind == "heartbeat":
        board.heartbeat = {k: v for k, v in data.items() if k != "type"}
        board.publish("heartbeat", board.heartbeat)
    elif kind == "identity":
        claims = jwt_claims(str(data.get("token") or ""))
        board.identity = {
            "sub": claims.get("sub"),
            "aud": claims.get("aud"),
            "iss": claims.get("iss"),
            "exp": claims.get("exp"),
            "grant": claims.get("gty") or "client-credentials",
            "token": data.get("token"),
        }
        board.publish("identity", board.identity)
    elif kind == "message":
        message = summarize_message(data)
        board.messages.append(message)
        board.publish("message", message)
    elif kind == "run_start":
        run = {
            "id": data["run_id"],
            "at": data.get("at"),
            "trigger": data.get("trigger"),
            "label": data.get("label"),
            "planner": data.get("planner"),
            "status": "running",
            "summary": "",
            "steps": [],
            "thoughts": [],
        }
        board.runs[run["id"]] = run
        while len(board.runs) > 30:  # noqa: PLR2004
            board.runs.pop(next(iter(board.runs)))
        board.publish("run", run)
    elif kind == "thought":
        run = board.runs.get(data.get("run_id") or "") or (list(board.runs.values()) or [None])[-1]
        if run:
            run["thoughts"].append(data.get("text") or "")
            board.publish("run", run)
    elif kind == "step_start" and data.get("run_id") in board.runs:
        run = board.runs[data["run_id"]]
        run["steps"].append(
            {
                "index": data["step"],
                "title": data.get("title"),
                "instruction": data.get("instruction"),
                "reason": data.get("reason"),
                "started": data.get("at"),
                "ended": None,
                "outcome": "running",
                "answer": "",
                "http_status": None,
                "observed": "",
                "records": [],
                "intent": [],
                "intent_error": None,
                "denials": [],
            },
        )
        board.current = (run["id"], data["step"])
        board.publish("run", run)
    elif kind == "step_end" and data.get("run_id") in board.runs:
        run = board.runs[data["run_id"]]
        step = run["steps"][data["step"]]
        override, observed = observed_of(step)
        step.update(
            ended=data.get("at"),
            outcome=override or data.get("outcome"),
            answer=data.get("answer") or "",
            http_status=data.get("http_status"),
            intent_error=data.get("intent_error") if isinstance(data.get("intent_error"), dict) else None,
            observed=observed,
        )
        board.current = None
        answer.update(outcome=step["outcome"], observed=observed)
        board.publish("run", run)
        asyncio.get_running_loop().create_task(board.refresh_written())
    elif kind == "escalation":
        item = {
            "id": uuid.uuid4().hex[:8],
            "at": data.get("at"),
            "run_id": data.get("run_id"),
            "text": data.get("text"),
            "trigger": data.get("trigger"),
        }
        board.escalations.append(item)
        board.publish("escalation", item)
    elif kind == "run_end":
        run = board.runs.get(data.get("run_id") or "")
        if run:
            run.update(
                status=data.get("status"),
                summary=data.get("summary"),
                counts=data.get("counts"),
                ended=data.get("at"),
            )
            board.publish("run", run)
        board.current = None
    return JSONResponse(answer)


# ------------------------------------------------------------------ actions
async def sentinel_state() -> dict:
    try:
        return (await board.http.get(f"{SENTINEL_URL}/state", timeout=5)).json()
    except (httpx.HTTPError, ValueError):
        return {}


@app.post("/api/feed/{event_id}")
async def feed(event_id: str) -> dict:
    """Write one catalogue event to the graph through the Capture API."""
    event = board.catalog.get(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="unknown event")
    await board.capture("nodes", {"nodes": event["nodes"]})
    await board.capture("relationships", {"relationships": event["relationships"]})
    if event_id not in board.fed:
        board.fed.append(event_id)
    board.publish("graph", board.graph())
    board.publish("fed", {"id": event_id, "focus": event["focus"], "kind": event["kind"]})
    delivery = "pubsub"
    sentinel = await sentinel_state()
    if sentinel.get("error"):
        # The event is in the graph and on the topic, but the Sentinel cannot read its subscription.
        return {"ok": True, "delivery": "blocked", "error": str(sentinel["error"]).split("[")[0].strip()[:160]}
    if not sentinel.get("subscription"):
        # No subscription configured: hand the Sentinel the upsert the sink would have published.
        delivery = "direct"
        for node in event["nodes"]:
            await board.http.post(
                f"{SENTINEL_URL}/events",
                json={
                    "type": "indykite.audit.capture.upsert.node",
                    "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "body": {"eventType": "indykite.audit.capture.upsert.node", "data": {"node": node}},
                },
            )
    with suppress(httpx.HTTPError):
        await board.http.post(f"{SENTINEL_URL}/wake", timeout=5)
    return {"ok": True, "delivery": delivery}


@app.post("/api/wake")
async def wake() -> dict:
    """Ask the Sentinel to pull now instead of waiting for its schedule."""
    try:
        await board.http.post(f"{SENTINEL_URL}/wake", timeout=5)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"sentinel unreachable: {exc}") from exc
    return {"ok": True}


@app.post("/api/reset")
async def reset() -> dict:  # noqa: C901
    """Remove what the feed and the Sentinel wrote, back to the provisioned graph."""
    # A mission in progress would keep writing to a graph that is being emptied under it.
    try:
        cleared = (await board.http.post(f"{SENTINEL_URL}/clear", timeout=5)).json()
    except (httpx.HTTPError, ValueError):
        cleared = {}
    if cleared.get("busy"):
        raise HTTPException(status_code=409, detail="a mission is running; reset when it has finished")
    seed_ids = {n["id"] for n in board.seed["nodes"]}
    doomed: dict[str, dict] = {}

    def doom(typ: str, ext: str | None) -> None:
        if ext and f"{typ}:{ext}" not in seed_ids:
            doomed[f"{typ}:{ext}"] = {"external_id": ext, "type": typ}

    await board.refresh_written()
    for row in board.written["holds"]:
        doom("Hold", row["hold"])
    for row in board.written["restrictions"]:
        doom("Restriction", row["restriction"])
    for event in board.catalog.values():
        for node in event["nodes"]:
            doom(node["type"], node["external_id"])
            if node["type"] == "Mandate":
                doom("Restriction", f"rst-{node['external_id']}")
            if node["type"] == "TradeOrder":
                doom("Hold", f"hold-{node['external_id']}")
    for node in board.seed["nodes"]:
        if node["type"] == "TradeOrder":
            doom("Hold", f"hold-{node['id'].split(':', 1)[1]}")
    if doomed:
        await board.capture("nodes/delete", {"nodes": list(doomed.values())})
    board.reset_state()
    await board.refresh_written()
    board.publish("reset", board.snapshot())
    return {"ok": True, "deleted": len(doomed)}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")

if __name__ == "__main__":
    # Binds all interfaces inside the container; compose publishes the port on the host's loopback only.
    uvicorn.run(
        app,
        host="0.0.0.0",  # nosec B104  # noqa: S104
        port=PORT,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
