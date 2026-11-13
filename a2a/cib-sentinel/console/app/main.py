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
from urllib.parse import urlsplit

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


def put_node(  # noqa: PLR0913, PLR0917
    nodes: dict,
    typ: str,
    ext: str,
    label: str,
    origin: str,
    props: dict | None = None,
) -> str:
    """Add a node once and return its id."""
    nid = f"{typ}:{ext}"
    nodes.setdefault(nid, {"id": nid, "type": typ, "label": label or ext, "props": props or {}, "origin": origin})
    return nid


def put_edge(nodes: dict, edges: list, src: str, typ: str, dst: str, origin: str) -> None:  # noqa: PLR0913, PLR0917
    """Add an edge between two known nodes, once."""
    if src not in nodes or dst not in nodes:
        return
    if any(e["source"] == src and e["target"] == dst and e["type"] == typ for e in edges):
        return
    edges.append({"source": src, "target": dst, "type": typ, "workflow": "", "origin": origin})


def add_event(nodes: dict, edges: list, event: dict) -> None:
    """Add the nodes and relationships of one fed event."""
    for n in event["nodes"]:
        props = {p["type"]: p["value"] for p in n["properties"]}
        put_node(nodes, n["type"], n["external_id"], props.get("name", ""), "feed", props)
    for r in event["relationships"]:
        source = f"{r['source']['type']}:{r['source']['external_id']}"
        target = f"{r['target']['type']}:{r['target']['external_id']}"
        put_edge(nodes, edges, source, r["type"], target, "feed")


def written_nodes(written: dict) -> list[tuple[str, str]]:
    """(type, id) of the holds and restrictions read back from the graph."""
    holds = [("Hold", row["hold"]) for row in written["holds"]]
    return holds + [("Restriction", row["restriction"]) for row in written["restrictions"]]


def conventional_nodes(fed: list[tuple[str, str]], seed_nodes: list[dict]) -> list[tuple[str, str]]:
    """(type, id) of the restrictions and holds the Sentinel names after a mandate or an order."""
    orders = [ext for typ, ext in fed if typ == "TradeOrder"]
    orders += [n["id"].split(":", 1)[1] for n in seed_nodes if n["type"] == "TradeOrder"]
    restrictions = [("Restriction", f"rst-{ext}") for typ, ext in fed if typ == "Mandate"]
    return restrictions + [("Hold", f"hold-{ext}") for ext in orders]


def intent_request(evaluation: dict) -> tuple[bool, dict] | None:
    """(is a search, request body) for an evaluation, or None when its shape is not checked here."""
    subject = evaluation.get("subject") or {}
    action = evaluation.get("action") or {}
    resource = evaluation.get("resource") or {}
    if not (subject.get("id") and action.get("name") and resource.get("type")):
        return None
    body: dict[str, Any] = {"subject": subject, "action": action, "resource": resource}
    given = (evaluation.get("context") or {}).get("parameters") or {}
    # target_<type> names a second record of the task; it is not a parameter a policy reads.
    parameters = {k: v for k, v in given.items() if not k.startswith("target_")}
    if parameters:
        body["context"] = {"input_params": parameters}
    return not resource.get("id"), body


def intent_verdict(answer: dict, *, search: bool) -> dict:
    """Read the platform's answer: a decision, or for a search the resources it permits."""
    if not search:
        return {"verdict": "permitted" if answer.get("decision") else "denied", "detail": ""}
    found = [r["id"] for r in answer.get("results") or [] if r.get("id")]
    return {"verdict": "permitted" if found else "denied", "detail": ", ".join(found[:8]) or "nothing of this type"}


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
        self.identity: dict = {}
        self.messages: deque[dict] = deque(maxlen=300)
        self.audit: deque[dict] = deque(maxlen=800)
        self.runs: dict[str, dict] = {}
        self.escalations: list[dict] = []
        self.heartbeat: dict = {}
        self.fed: list[str] = []
        self.current: tuple[str, int] | None = None
        self.written: dict[str, list[dict]] = {"restrictions": [], "holds": []}
        self.board_live = False

    def reset_state(self) -> None:
        """Forget every record; the Sentinel's identity is kept."""
        self.messages.clear()
        self.audit.clear()
        self.runs = {}
        self.escalations = []
        self.heartbeat = {}
        self.fed = []
        self.current = None
        self.written = {"restrictions": [], "holds": []}
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
        for event_id in self.fed:
            add_event(nodes, edges, self.catalog[event_id])
        for row in self.written["restrictions"]:
            reason = {"reason": row.get("reason", "")}
            rid = put_node(nodes, "Restriction", row["restriction"], "Restriction", "sentinel", reason)
            put_edge(nodes, edges, rid, "RESTRICTS", f"Company:{row['company']}", "sentinel")
            put_edge(nodes, edges, rid, "ARISES_FROM", f"Mandate:{row['mandate']}", "sentinel")
            put_edge(nodes, edges, f"Agent:{alias(row['agent'])}", "RAISED", rid, "sentinel")
        for row in self.written["holds"]:
            hid = put_node(nodes, "Hold", row["hold"], "Hold", "sentinel", {"reason": row.get("reason", "")})
            put_edge(nodes, edges, hid, "HOLDS", f"TradeOrder:{row['order']}", "sentinel")
            put_edge(nodes, edges, f"Agent:{alias(row['agent'])}", "RAISED", hid, "sentinel")
        return {
            "nodes": list(nodes.values()),
            "edges": edges,
            "capabilities": self.seed.get("capabilities", {}),
            "live": self.board_live,
        }

    def runtime_nodes(self) -> list[dict]:
        """Nodes a reset deletes: what the feed and the Sentinel wrote, never the provisioned graph."""
        seed = {n["id"] for n in self.seed["nodes"]}
        fed = [(n["type"], n["external_id"]) for event in self.catalog.values() for n in event["nodes"]]
        found = written_nodes(self.written) + fed + conventional_nodes(fed, self.seed["nodes"])
        unique = {f"{typ}:{ext}": {"external_id": ext, "type": typ} for typ, ext in found if ext}
        return [node for key, node in unique.items() if key not in seed]

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
        if not (BASE_URL and APP_TOKEN):
            return {"verdict": "unchecked", "detail": "the console has no platform credentials"}
        request = intent_request(evaluation)
        if request is None:
            return {"verdict": "unchecked", "detail": "this shape of evaluation is not checked here"}
        search, body = request
        endpoint = "search/resource" if search else "evaluation"
        try:
            resp = await self.http.post(
                f"{BASE_URL}/access/v1/{endpoint}",
                headers={"Content-Type": "application/json", "X-IK-ClientKey": APP_TOKEN},
                json=body,
            )
        except httpx.HTTPError as exc:
            return {"verdict": "unchecked", "detail": f"the check could not be sent: {exc}"}
        if resp.status_code != httpx.codes.OK:
            return {"verdict": "unchecked", "detail": f"the platform answered {resp.status_code}: {resp.text[:200]}"}
        return intent_verdict(resp.json() or {}, search=search)

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
    """Create the board and follow the graph and the bridge while the service runs."""
    global board  # noqa: PLW0603
    board = Board()
    tasks = [asyncio.create_task(board.refresh_written()), asyncio.create_task(board.watch_bridge())]
    yield
    for task in tasks:
        task.cancel()
    await board.http.aclose()


app = FastAPI(title="cib-sentinel console", lifespan=lifespan)

# The console has no login, so a page of another site open in the same browser must not be able to
# write through it. Browsers name the origin of a cross-site request; the services that post records
# from inside the compose network send no such header.
ACTION_HEADER = "x-console-action"
BROWSER_ACTIONS = ("/api/feed/", "/api/reset", "/api/wake")


def cross_site(request: Request) -> bool:
    """Tell whether a browser sent this request from another origin than the console's own."""
    if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
        return True
    origin = request.headers.get("origin")
    return bool(origin) and urlsplit(origin).netloc != request.headers.get("host")


@app.middleware("http")
async def same_origin_only(request: Request, call_next):
    """Refuse writes from another origin; the page's own actions must also carry the action header."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if cross_site(request):
            return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
        if request.url.path.startswith(BROWSER_ACTIONS) and ACTION_HEADER not in request.headers:
            return JSONResponse({"detail": "this action is only available from the console page"}, status_code=403)
    return await call_next(request)


@app.get("/api/health")
async def health() -> dict:
    """Liveness."""
    return {"ok": True}


@app.get("/api/state")
async def state() -> dict:
    """Everything a page needs when it opens."""
    return board.snapshot()


@app.get("/api/stream")
async def stream(request: Request) -> StreamingResponse:
    """Server-sent events: every record as it arrives."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    board.clients.add(queue)

    async def events():
        """Yield the records as server-sent events until the page disconnects."""
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
    """Name the kind of service a record comes from: gateway, token-service or agent."""
    if service.endswith("-iag"):
        return "gateway"
    if "token" in service:
        return "token-service"
    return "agent"


# record key -> key of the audit record as the services send it
RECORD_FIELDS = {
    "reason": "reason",
    "trace_id": "traceID",
    "reason_code": "reasonCode",
    "prompt": "prompt",
    "cached": "cached",
    "permitted_set": "permittedSet",
    "discovery": "discovery",
    "capabilities": "capabilities",
    "duration_ms": "durationMs",
    "resolved": "resolvedEvaluations",
    "current_intent": "currentIntent",
    "original_intent": "originalIntent",
    "capability": "capabilityName",
    "skill": "skillID",
    "skills": "skillIDs",
    "evaluations": "evaluations",
}
TEXT_FIELDS = ("reason", "trace_id", "reason_code", "prompt")
CLAIMS_SHOWN = ("iss", "sub", "aud", "exp", "iat", "act")


def audit_record(data: dict) -> dict:
    """Normalise one audit record of a gateway, the Token Service or an agent."""
    service = str(data.get("service") or "unknown")
    record: dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "at": time.time(),
        "service": service,
        "layer": layer_of(service),
        "decision": str(data.get("decision") or "").lower(),
        "action": str(data.get("action") or ""),
        "subject": alias(data.get("subject")),
        "actor": alias(data.get("actor")),
        "chain": [alias(a) for a in data.get("actorsChain") or []],
    }
    record.update({key: data.get(source) for key, source in RECORD_FIELDS.items()})
    for key in TEXT_FIELDS:
        record[key] = record[key] or ""
    return record


def read_delegation(record: dict) -> list[dict]:
    """Decode a reported delegation into the record and return the intent it carries, if any."""
    token = str(record["reason"])
    claims = jwt_claims(token)
    record.update(
        reason="",
        token=token,
        chain=[alias(a) for a in act_chain(claims)],
        subject=alias(claims.get("sub")),
        claims={k: claims[k] for k in CLAIMS_SHOWN if k in claims},
    )
    carried = ((claims.get("act") or {}).get("intent") or {}).get("evaluations")
    if not isinstance(carried, list):
        return []
    return [e for e in carried if isinstance(e, dict)]


def keep_checked(task: dict, record: dict) -> None:
    """Keep on the task what the MCP gateway asked the platform about a tool call, with each decision."""
    for checked in record.get("resolved") or []:
        if isinstance(checked, dict) and checked not in task["checked"]:
            task["checked"].append(checked)


# The record of a gateway comparing the task's intent with what an agent or a server offers. It reports what
# fits, never a refusal of its own: a shorter list is not a denial, and a task no skill covers is refused by
# the record of the intent itself.
FIT_RECORD = "IBAC_DISCOVERY"


def keep_fit(task: dict, record: dict) -> None:
    """Keep on the task which skills or tools a gateway found to fit each evaluation of the intent."""
    for match in record.get("evaluations") or []:
        if not isinstance(match, dict):
            continue
        fit = {
            "service": record["service"],
            "among": record.get("capability") or "",
            "action": match.get("action") or "",
            "resource": match.get("resourceType") or "",
            "matched": [str(name) for name in match.get("matchedSkills") or []],
        }
        if fit not in task["fits"]:
            task["fits"].append(fit)


def attach_to_task(record: dict, intent: list[dict]) -> None:
    """Tie a record to the task in progress, with the intent it carries and any refusal it reports."""
    if not board.current:
        return
    run_id, index = board.current
    record["run_id"], record["step"] = run_id, index
    run = board.runs.get(run_id)
    if not run or index >= len(run["steps"]):
        return
    task = run["steps"][index]
    task["records"].append(record["id"])
    if intent and not task["intent"]:
        # The first agent of the task reports the delegation it received: the task's intent is in it.
        task["intent"] = [{"evaluation": e, "verdict": "checking", "detail": ""} for e in intent]
        asyncio.get_running_loop().create_task(board.check_intent(run_id, index))
    if record["action"].upper() == FIT_RECORD:
        keep_fit(task, record)
    elif record["decision"] in DENIED and record["layer"] != "agent":
        refusal = {"service": record["service"], "action": record["action"], "reason": str(record["reason"])[:300]}
        task["denials"].append(refusal)
    keep_checked(task, record)


@app.post("/api/push-update")
async def push_update(request: Request) -> dict:
    """Receive an audit record of a gateway, the Token Service or a fleet agent."""
    record = audit_record(await request.json())
    intent = read_delegation(record) if record["decision"] == "token_exchanged" else []
    attach_to_task(record, intent)
    board.audit.append(record)
    board.publish("audit", record)
    return {"ok": True}


# ------------------------------------------------------- the Sentinel's feed
def summarize_message(data: dict) -> dict:
    """Reduce a relayed wire message to what the page shows."""
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


# What a gateway refused, by the action of its record.
REFUSED_WHAT = {
    "ADMISSION": "admission",
    "INVOCATION": "a capability",
    "DISCOVERY": "discovery",
    "INTENT": "the task's intent",
    "IBAC_USAGE": "a tool call that does not fit the task's intent",
    "RESOLVED_EVALUATIONS": "a tool call the platform does not allow",
}


def observed_of(step: dict) -> tuple[str, str]:
    """Return what the control plane said about a task: (outcome override, sentence)."""
    if not step["denials"]:
        return "", ""
    first = step["denials"][0]
    what = REFUSED_WHAT.get(str(first["action"]).upper(), "the request")
    return "refused", f"{first['service']} refused {what}" + (f": {first['reason']}" if first["reason"] else "")


def on_heartbeat(data: dict) -> None:
    """Record the Sentinel's schedule and state."""
    board.heartbeat = {k: v for k, v in data.items() if k != "type"}
    board.publish("heartbeat", board.heartbeat)


def on_identity(data: dict) -> None:
    """Record the Sentinel's own access token, decoded for the header."""
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


def on_message(data: dict) -> None:
    """Record a message the Sentinel pulled from the wire."""
    message = summarize_message(data)
    board.messages.append(message)
    board.publish("message", message)


def on_run_start(data: dict) -> None:
    """Open a mission; the oldest are dropped beyond thirty."""
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
    while len(board.runs) > MAX_RUNS:
        board.runs.pop(next(iter(board.runs)))
    board.publish("run", run)


def on_thought(data: dict) -> None:
    """Add a note of the Sentinel to its current mission."""
    run = board.runs.get(data.get("run_id") or "")
    if run is None and board.runs:
        run = list(board.runs.values())[-1]
    if run:
        run["thoughts"].append(data.get("text") or "")
        board.publish("run", run)


def on_step_start(data: dict) -> None:
    """Open a task: records arriving from now on belong to it."""
    run = board.runs.get(data.get("run_id") or "")
    if not run:
        return
    task = {
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
        "checked": [],
        "fits": [],
        "denials": [],
    }
    run["steps"].append(task)
    board.current = (run["id"], data["step"])
    board.publish("run", run)


def on_step_end(data: dict) -> dict | None:
    """Close a task: a gateway refusal seen during it overrides the outcome the Sentinel read from the answer."""
    run = board.runs.get(data.get("run_id") or "")
    if not run:
        return None
    task = run["steps"][data["step"]]
    override, observed = observed_of(task)
    refusal = data.get("intent_error")
    task.update(
        ended=data.get("at"),
        outcome=override or data.get("outcome"),
        answer=data.get("answer") or "",
        http_status=data.get("http_status"),
        intent_error=refusal if isinstance(refusal, dict) else None,
        observed=observed,
    )
    board.current = None
    board.publish("run", run)
    asyncio.get_running_loop().create_task(board.refresh_written())
    return {"outcome": task["outcome"], "observed": observed}


def on_escalation(data: dict) -> None:
    """Record that the Sentinel hands a mission over to a person."""
    item = {
        "id": uuid.uuid4().hex[:8],
        "at": data.get("at"),
        "run_id": data.get("run_id"),
        "text": data.get("text"),
        "trigger": data.get("trigger"),
    }
    board.escalations.append(item)
    board.publish("escalation", item)


def on_run_end(data: dict) -> None:
    """Close a mission."""
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


MAX_RUNS = 30
SENTINEL_RECORDS = {
    "heartbeat": on_heartbeat,
    "identity": on_identity,
    "message": on_message,
    "run_start": on_run_start,
    "thought": on_thought,
    "step_start": on_step_start,
    "step_end": on_step_end,
    "escalation": on_escalation,
    "run_end": on_run_end,
}


@app.post("/api/sentinel")
async def sentinel_record(request: Request) -> JSONResponse:
    """One lifecycle record of the autonomous agent; the answer to a task's end carries what was observed."""
    data = await request.json()
    handler = SENTINEL_RECORDS.get(data.get("type"))
    extra = handler(data) if handler else None
    return JSONResponse({"ok": True, **(extra or {})})


# ------------------------------------------------------------------ actions
async def sentinel_state() -> dict:
    """Read the Sentinel's state; empty when it cannot be reached."""
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
async def reset() -> dict:
    """Remove what the feed and the Sentinel wrote, back to the provisioned graph."""
    # A mission in progress would keep writing to a graph that is being emptied under it, so nothing is
    # deleted unless the Sentinel itself answers that it is idle.
    try:
        response = await board.http.post(f"{SENTINEL_URL}/clear", timeout=5)
        response.raise_for_status()
        cleared = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        detail = "cannot confirm that the Sentinel is idle; nothing was reset"
        raise HTTPException(status_code=502, detail=detail) from exc
    if not isinstance(cleared, dict) or not isinstance(cleared.get("busy"), bool):
        raise HTTPException(status_code=502, detail="unreadable answer from the Sentinel; nothing was reset")
    if cleared["busy"]:
        raise HTTPException(status_code=409, detail="a mission is running; reset when it has finished")
    await board.refresh_written()
    doomed = board.runtime_nodes()
    if doomed:
        await board.capture("nodes/delete", {"nodes": doomed})
    board.reset_state()
    await board.refresh_written()
    board.publish("reset", board.snapshot())
    return {"ok": True, "deleted": len(doomed)}


@app.get("/")
async def index() -> FileResponse:
    """Serve the page."""
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
