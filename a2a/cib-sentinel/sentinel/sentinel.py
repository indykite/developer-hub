# Copyright (c) 2026 IndyKite
"""Control-room Sentinel: an autonomous agent that is the subject of its own requests.

On a schedule it pulls the project's audit events from a Google Cloud Pub/Sub
subscription (the IndyKite event sink publishes them there). A Capture upsert
of a watched node type (a new Mandate, a new TradeOrder) starts a mission: a
language model decides the next task from the mission brief, the event and
what happened so far; each task is sent to the orchestrator's Agent Gateway
with the Sentinel's own Auth0 client-credentials token, so the Sentinel - not
a person - is the subject the gateways admit or refuse. Every message, thought,
task and outcome is reported to the console.
"""

import asyncio
import base64
import json
import logging
import os
import re
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("sentinel")


def _flag(name: str, default: str = "") -> bool:
    """Read a boolean environment variable."""
    return (os.getenv(name) or default).strip().lower() in ("true", "1", "yes")


PORT = int(os.getenv("SENTINEL_PORT", "7100"))
CONSOLE_URL = (os.getenv("CONSOLE_URL") or "http://console:3500").rstrip("/")
GATEWAY_URL = (os.getenv("ORCHESTRATOR_GATEWAY_URL") or "http://orchestrator-iag:9981").rstrip("/")
TOKEN_URL = os.getenv("IDP_TOKEN_URL") or "http://auth0-bridge:8090/oauth/token"
CLIENT_ID = (os.getenv("SENTINEL_IDP_CLIENT_ID") or "").strip()
CLIENT_SECRET = (os.getenv("SENTINEL_IDP_CLIENT_SECRET") or "").strip()
AUDIENCE = (os.getenv("AUTH0_AUTONOMOUS_AUDIENCE") or "cib-autonomous").strip()

PUBSUB_PROJECT = (os.getenv("PUBSUB_GCP_PROJECT") or "").strip()
PUBSUB_SUBSCRIPTION = (os.getenv("PUBSUB_SUBSCRIPTION") or "").strip()
POLL_SECONDS = float(os.getenv("SENTINEL_POLL_SECONDS", "20"))
SETTLE_SECONDS = float(os.getenv("SENTINEL_SETTLE_SECONDS", "4"))
MAX_EVENT_AGE = float(os.getenv("SENTINEL_MAX_EVENT_AGE_SECONDS", "600"))
STEP_PAUSE = float(os.getenv("SENTINEL_STEP_PAUSE_SECONDS", "3"))
MAX_STEPS = int(os.getenv("SENTINEL_MAX_STEPS", "6"))
TASK_TIMEOUT = float(os.getenv("SENTINEL_TASK_TIMEOUT_SECONDS", "240"))
PLANNER = (os.getenv("SENTINEL_PLANNER") or "llm").strip().lower()
MISSION_FILE = Path(os.getenv("MISSION_FILE") or "/app/mission.json")

GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL") or "gemini-2.0-flash"
USE_GEMINI = bool(GEMINI_API_KEY) and (os.getenv("GEMINI_ENABLED") or "").strip().lower() not in ("false", "0", "no")
OLLAMA_HOST = (os.getenv("OLLAMA_HOST") or "http://host.docker.internal:11434").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL") or "mistral-nemo:latest"

UPSERT_NODE = "capture.upsert.node"
REFUSED_RE = re.compile(
    r"not authori[sz]ed|access (is )?denied|\bdenied\b|not allowed|not permitted|no permission|unauthori[sz]ed"
    r"|\bforbidden\b|\b40[13]\b",
    re.IGNORECASE,
)
FAILED_RE = re.compile(
    r"technical error|system error|\bmcp error\b|encountered an error|unable to (retrieve|process|complete)"
    r"|try again later|\btimed? ?out\b|\bunavailable\b|\bbad request\b|rejected the request"
    r"|\b(attempt|request|call) .{0,80}failed\b",
    re.IGNORECASE,
)
EMPTY_RE = re.compile(
    r"no data|no (rows|results|records|matching)|not found|empty (answer|result)|nothing (was )?(found|returned)"
    r"|could not find|couldn't find|no tool or resource|empty answer|graph does not allow"
    r"|is not (currently )?on the restricted list",
    re.IGNORECASE,
)


def load_mission() -> dict:
    """Mission brief and per-event-type procedures (usecase data, mounted read-only)."""
    with MISSION_FILE.open(encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------- events
def _json_body(data: bytes) -> dict:
    """Read a message's data as a JSON object; anything else is kept, wrapped."""
    if not data:
        return {}
    try:
        body = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"raw": base64.b64encode(data).decode("ascii")[:2000]}
    return body if isinstance(body, dict) else {"data": body}


def _structured_event(event: dict) -> dict:
    """Unwrap a structured-mode CloudEvent: the event's own fields, and its data as the body."""
    inner = event.get("data")
    if isinstance(inner, str):
        with suppress(json.JSONDecodeError):
            inner = json.loads(inner)
    return {
        "id": str(event.get("id") or ""),
        "type": str(event.get("type") or ""),
        "time": str(event.get("time") or ""),
        "body": inner if isinstance(inner, dict) else {"data": inner},
    }


def decode_message(data: bytes, attributes: dict[str, str]) -> dict:
    """Turn one Pub/Sub message into {id, type, time, body}.

    The event sink publishes CloudEvents: in binary mode the event attributes
    are message attributes prefixed ``ce-`` and the data is the JSON body; in
    structured mode the whole event is the JSON data.
    """
    body = _json_body(data)
    if "specversion" in body and "type" in body:
        return _structured_event(body)
    attrs = {k.lower(): v for k, v in (attributes or {}).items()}
    return {
        "id": attrs.get("ce-id", ""),
        "type": attrs.get("ce-type") or str(body.get("eventType") or ""),
        "time": attrs.get("ce-time") or str(body.get("time") or ""),
        "body": body,
    }


def find_node(value: Any) -> dict | None:  # noqa: ANN401
    """First object in the event body that looks like a graph node (external id + type)."""
    if isinstance(value, dict):
        ext = value.get("externalId") or value.get("external_id")
        if isinstance(ext, str) and ext and isinstance(value.get("type"), str) and value["type"]:
            return {"external_id": ext, "type": value["type"], "properties": node_properties(value)}
        for child in value.values():
            found = find_node(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_node(child)
            if found:
                return found
    return None


def node_properties(node: dict) -> dict[str, Any]:
    """Flatten a node's property list to {type: value}."""
    out: dict[str, Any] = {}
    for prop in node.get("properties") or []:
        if not isinstance(prop, dict) or not prop.get("type"):
            continue
        val = prop.get("value")
        if isinstance(val, dict):  # typed value object: keep its single populated member
            val = next((v for v in val.values() if v not in (None, "", [], {})), "")
        out[str(prop["type"])] = val
    return out


def event_age_seconds(stamp: str) -> float | None:
    """Age of an RFC 3339 timestamp, or None when it cannot be read."""
    if not stamp:
        return None
    try:
        from datetime import datetime  # noqa: PLC0415

        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return time.time() - parsed.timestamp()


def trigger_of(event: dict, watched: set[str]) -> dict | None:
    """Return the mission trigger an event carries: an upsert of a watched node type."""
    if not event["type"].endswith(UPSERT_NODE):
        return None
    node = find_node(event["body"].get("data", event["body"]))
    if not node or node["type"] not in watched:
        return None
    return node


def classify(answer: str, http_status: int, intent_error: dict | None = None) -> str:  # noqa: PLR0911
    """Outcome of a task from what came back: unmapped | refused | failed | empty | done."""
    if intent_error:
        return "unmapped"
    if http_status in (401, 403):
        return "refused"
    if http_status >= 400 or http_status == 0:  # noqa: PLR2004
        return "failed"
    if REFUSED_RE.search(answer or ""):
        return "refused"
    if FAILED_RE.search(answer or ""):
        return "failed"
    if EMPTY_RE.search(answer or ""):
        return "empty"
    return "done"


# -------------------------------------------------------------------- service
class Sentinel:
    """State and behaviour of the autonomous agent."""

    def __init__(self) -> None:
        """Load the mission and start with nothing seen and nothing queued."""
        self.mission = load_mission()
        self.watched = set(self.mission.get("procedures", {}))
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(TASK_TIMEOUT))
        self._token: str | None = None
        self._token_exp = 0.0
        self.seen: deque[str] = deque(maxlen=2000)
        self.pending: dict[str, tuple[float, dict]] = {}
        self.queue: asyncio.Queue[dict] = asyncio.Queue()
        self.wake = asyncio.Event()
        self.next_wake = 0.0
        self.busy = False
        self.subscriber = None
        self.last_error = ""

    # ---- reporting
    async def tell(self, kind: str, **fields: Any) -> dict:  # noqa: ANN401
        """Report one lifecycle record to the console; its answer may carry observations."""
        record = {"type": kind, "at": time.time(), **fields}
        try:
            resp = await self.http.post(f"{CONSOLE_URL}/api/sentinel", json=record, timeout=5)
            return resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        except (httpx.HTTPError, ValueError):
            log.debug("console unreachable for %s", kind)
            return {}

    # ---- identity
    async def token(self) -> str:
        """Return the Sentinel's own access token: a client-credentials grant for the autonomous audience."""
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        if not (CLIENT_ID and CLIENT_SECRET):
            msg = "SENTINEL_IDP_CLIENT_ID / SENTINEL_IDP_CLIENT_SECRET are not set"
            raise RuntimeError(msg)
        resp = await self.http.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "audience": AUDIENCE,
            },
            timeout=20,
        )
        if resp.status_code != httpx.codes.OK:
            msg = f"token endpoint answered {resp.status_code}: {resp.text[:200]}"
            raise RuntimeError(msg)
        body = resp.json()
        self._token = body["access_token"]
        self._token_exp = time.time() + float(body.get("expires_in", 300))
        await self.tell("identity", token=self._token, audience=AUDIENCE)
        return self._token

    # ---- event intake
    def pull(self) -> list[tuple[bytes, dict[str, str]]]:
        """One synchronous pull of the subscription; received messages are acknowledged."""
        if not (PUBSUB_PROJECT and PUBSUB_SUBSCRIPTION):
            return []
        if self.subscriber is None:
            from google.cloud import pubsub_v1  # noqa: PLC0415

            self.subscriber = pubsub_v1.SubscriberClient()
        path = self.subscriber.subscription_path(PUBSUB_PROJECT, PUBSUB_SUBSCRIPTION)
        out: list[tuple[bytes, dict[str, str]]] = []
        for _ in range(5):
            response = self.subscriber.pull(
                request={"subscription": path, "max_messages": 50, "return_immediately": True},
                timeout=15,
            )
            if not response.received_messages:
                break
            self.subscriber.acknowledge(
                request={"subscription": path, "ack_ids": [m.ack_id for m in response.received_messages]},
            )
            out.extend((m.message.data, dict(m.message.attributes)) for m in response.received_messages)
        return out

    async def intake(self, event: dict, source: str) -> None:
        """Relay one event to the console and remember the mission it triggers, if any."""
        if event["id"] and event["id"] in self.seen:
            return
        if event["id"]:
            self.seen.append(event["id"])
        age = event_age_seconds(event["time"])
        stale = age is not None and age > MAX_EVENT_AGE
        node = trigger_of(event, self.watched)
        triggers = bool(node) and not stale
        await self.tell(
            "message",
            id=event["id"] or uuid.uuid4().hex,
            event_type=event["type"],
            time=event["time"],
            source=source,
            stale=stale,
            trigger=triggers,
            node=find_node(event["body"].get("data", event["body"])),
            body=event["body"],
        )
        if triggers:
            key = f"{node['type']}:{node['external_id']}"
            self.pending[key] = (time.time() + SETTLE_SECONDS, node)

    async def release_settled(self) -> None:
        """Queue the triggers whose graph neighbourhood had time to be written."""
        now = time.time()
        for key in [k for k, (due, _) in self.pending.items() if due <= now]:
            _, node = self.pending.pop(key)
            await self.queue.put(node)

    async def poll_once(self) -> None:
        """Pull the subscription once and take in what arrived."""
        try:
            messages = await asyncio.to_thread(self.pull)
            self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)[:300]
            log.warning("pull failed: %s", self.last_error)
            messages = []
        for data, attributes in messages:
            await self.intake(decode_message(data, attributes), "pubsub")

    async def schedule_loop(self) -> None:
        """Wake on the schedule (or when asked), pull, then sleep again."""
        while True:
            await self.poll_once()
            self.next_wake = time.time() + POLL_SECONDS
            await self.tell(
                "heartbeat",
                next_wake=self.next_wake,
                interval=POLL_SECONDS,
                subscription=PUBSUB_SUBSCRIPTION,
                planner=self.planner_name(),
                error=self.last_error,
                busy=self.busy,
            )
            deadline = self.next_wake
            while time.time() < deadline and not self.wake.is_set():
                await self.release_settled()
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.wake.wait(), timeout=1)
            self.wake.clear()
            await self.release_settled()

    async def mission_loop(self) -> None:
        """Run the queued missions one at a time."""
        while True:
            node = await self.queue.get()
            self.busy = True
            try:
                await self.run_mission(node)
            except Exception as exc:
                log.exception("mission failed")
                await self.tell("run_end", run_id="", status="failed", summary=f"Mission failed: {exc}")
            finally:
                self.busy = False

    # ---- reasoning
    @staticmethod
    def planner_name() -> str:
        """Name what decides the next task: the model in use, or the written procedure."""
        if PLANNER != "llm":
            return "scripted"
        return f"gemini:{GEMINI_MODEL}" if USE_GEMINI else f"ollama:{LLM_MODEL}"

    async def llm_json(self, prompt: str) -> dict:
        """One model call that must answer a JSON object."""
        if USE_GEMINI:
            resp = await self.http.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
                headers={"x-goog-api-key": GEMINI_API_KEY},
                json={
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
                },
                timeout=60,
            )
            resp.raise_for_status()
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        else:
            resp = await self.http.post(
                f"{OLLAMA_HOST}/api/chat",
                json={
                    "model": LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0},
                },
                timeout=120,
            )
            resp.raise_for_status()
            text = resp.json()["message"]["content"]
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            msg = "the model did not answer a JSON object"
            raise TypeError(msg)
        return parsed

    def scripted_decision(self, node: dict, history: list[dict]) -> dict:
        """Return the procedure's next step, stopping early when an earlier step says to."""
        steps = self.mission["procedures"][node["type"]]["steps"]
        for past in history:
            rule = steps[past["index"]].get("on_blocked")
            if past["outcome"] in ("refused", "empty", "failed", "unmapped") and rule == "escalate":
                return {
                    "action": "escalate",
                    "reason": f"'{past['title']}' came back {past['outcome']}; a person has to take over.",
                }
        if len(history) >= len(steps):
            return {"action": "finish", "reason": "The procedure is complete."}
        step = steps[len(history)]
        fields = {"id": node["external_id"], **{k: str(v) for k, v in node["properties"].items()}}
        return {
            "action": "task",
            "title": step["title"],
            "instruction": step["instruction"].format_map(_Lenient(fields)),
            "reason": step.get("why", ""),
        }

    async def decide(self, node: dict, history: list[dict]) -> dict:
        """Decide what to do next: a task for the fleet, an escalation to a person, or the end."""
        if PLANNER != "llm":
            return self.scripted_decision(node, history)
        procedure = self.mission["procedures"][node["type"]]
        past = "\n".join(
            f"{i + 1}. task: {h['instruction']}\n   outcome: {h['outcome']}\n   answer: {h['answer'][:700]}"
            + (f"\n   control plane: {h['observed']}" if h.get("observed") else "")
            for i, h in enumerate(history)
        )
        prompt = (
            f"{self.mission['brief']}\n\n"
            f"EVENT\nA {node['type']} with id {node['external_id']} was written to the graph. "
            f"Its properties: {json.dumps(node['properties'])}\n\n"
            f"PROCEDURE FOR THIS EVENT\n{procedure['guidance']}\n\n"
            f"WHAT YOU HAVE DONE SO FAR\n{past or '(nothing yet)'}\n\n"
            f"You have used {len(history)} of {MAX_STEPS} tasks. Decide the single next move. Answer one JSON "
            'object: {"action": "task" | "escalate" | "finish", "title": "<3-6 words>", "instruction": "<the '
            'task, one sentence, naming every id it needs>", "reason": "<why, one sentence>"}. '
            "A task is carried out by the fleet and may be refused; a refusal is final for that task: never "
            "repeat a refused task, escalate or move on instead. Escalate when a person must take over; finish "
            "when nothing is left to do."
        )
        try:
            decision = await self.llm_json(prompt)
        except Exception as exc:
            log.warning("model call failed (%s); following the written procedure", exc)
            await self.tell("thought", text=f"Model unavailable ({str(exc)[:120]}); following the written procedure.")
            return self.scripted_decision(node, history)
        if decision.get("action") not in ("task", "escalate", "finish"):
            return self.scripted_decision(node, history)
        if decision["action"] == "task" and not str(decision.get("instruction") or "").strip():
            return self.scripted_decision(node, history)
        return decision

    # ---- acting
    async def _rpc(self, payload: dict, headers: dict) -> tuple[int, Any]:
        """One JSON-RPC call to the gateway: (200, result) or (status, what went wrong)."""
        try:
            resp = await self.http.post(GATEWAY_URL, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            return 0, f"Gateway unreachable: {exc}"
        if resp.status_code >= httpx.codes.BAD_REQUEST:
            return resp.status_code, resp.text[:600]
        return 200, _result(resp)

    async def send_task(self, text: str) -> tuple[int, str, dict | None]:
        """Send one task to the orchestrator's gateway as the Sentinel.

        Returns (HTTP status, answer text, intent refusal). The refusal is set when the gateway asked the
        Intent Agent about the task and no intent could be read from it: the answer is then an ordinary
        message whose metadata carries the reason.
        """
        try:
            token = await self.token()
        except Exception as exc:
            return 0, f"No access token: {exc}", None
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        message = {"role": "ROLE_USER", "parts": [{"text": text}], "messageId": uuid.uuid4().hex}
        payload = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "message/send", "params": {"message": message}}
        deadline = time.time() + TASK_TIMEOUT
        while True:
            status, result = await self._rpc(payload, headers)
            if status != httpx.codes.OK:
                return status, result, None
            settled = _settled(result)
            if settled:
                return settled
            task_id = result.get("id")
            if not task_id or time.time() > deadline:
                return 504, "No answer from the fleet in time", None
            await asyncio.sleep(0.6)
            payload = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "tasks/get", "params": {"id": task_id}}

    async def perform(self, run_id: str, index: int, decision: dict) -> dict:
        """Carry out one task and return what happened, as the next entry of the mission's history."""
        title = str(decision.get("title") or f"Task {index + 1}")[:80]
        instruction = str(decision["instruction"]).strip()
        reason = str(decision.get("reason") or "")
        await self.tell("step_start", run_id=run_id, step=index, title=title, instruction=instruction, reason=reason)
        http_status, answer, refusal = await self.send_task(instruction)
        outcome = classify(answer, http_status, refusal)
        await asyncio.sleep(1)  # let the gateways' audit records of this task reach the console
        seen = await self.tell(
            "step_end",
            run_id=run_id,
            step=index,
            outcome=outcome,
            answer=answer,
            http_status=http_status,
            intent_error=refusal,
        )
        return {
            "index": index,
            "title": title,
            "instruction": instruction,
            "answer": answer,
            "outcome": seen.get("outcome") or outcome,
            "observed": str(seen.get("observed") or ""),
        }

    async def run_mission(self, node: dict) -> None:
        """One mission: decide, act, observe, until finished or escalated."""
        run_id = uuid.uuid4().hex[:10]
        label = node["properties"].get("name") or node["external_id"]
        await self.tell("run_start", run_id=run_id, trigger=node, label=label, planner=self.planner_name())
        history: list[dict] = []
        status, summary = "finished", "Task budget used."
        while len(history) < MAX_STEPS:
            decision = await self.decide(node, history)
            reason = str(decision.get("reason") or "")
            if decision["action"] == "finish":
                summary = reason or "Nothing left to do."
                break
            if decision["action"] == "escalate":
                status, summary = "escalated", reason or "A person has to take over."
                await self.tell("escalation", run_id=run_id, text=summary, trigger=node)
                break
            history.append(await self.perform(run_id, len(history), decision))
            await asyncio.sleep(STEP_PAUSE)
        counts = dict.fromkeys(OUTCOMES, 0)
        for past in history:
            counts[past["outcome"]] = counts.get(past["outcome"], 0) + 1
        await self.tell("run_end", run_id=run_id, status=status, summary=summary, counts=counts)


OUTCOMES = ("done", "refused", "empty", "failed", "unmapped")
ENDED_BADLY = ("failed", "rejected", "canceled", "cancelled")


def _settled(result: dict) -> tuple[int, str, dict | None] | None:
    """Return the final answer in a gateway result, or None while the task is still running."""
    refusal = (result.get("metadata") or {}).get("intent_error")
    if isinstance(refusal, dict):
        return 200, _text(result), refusal
    state = (result.get("status") or {}).get("state") or ""
    if state.lower().endswith("completed") or result.get("parts"):
        return 200, _text(result), None
    if state.split("_")[-1].lower() in ENDED_BADLY:
        return 502, _text(result) or f"task ended {state}", None
    return None


class _Lenient(dict):
    """format_map source that leaves unknown placeholders readable."""

    def __missing__(self, key: str) -> str:
        """Render an unknown placeholder as <name>."""
        return f"<{key}>"


def _result(resp: httpx.Response) -> dict:
    """Extract the JSON-RPC result of a gateway answer; an error becomes a failed task."""
    try:
        body = resp.json()
    except ValueError:
        return {"status": {"state": "failed"}}
    if "error" in body:
        message = (body["error"] or {}).get("message", "error")
        return {"status": {"state": "failed"}, "parts": [], "artifacts": [{"parts": [{"text": str(message)}]}]}
    return body.get("result") or {}


def _text(result: dict) -> str:
    """Join the text parts of a task or message."""
    chunks = [
        part.get("text", "") for artifact in result.get("artifacts") or [] for part in artifact.get("parts") or []
    ]
    chunks += [part.get("text", "") for part in result.get("parts") or []]
    return "".join(c for c in chunks if c)


sentinel: Sentinel | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start the schedule and mission loops with the service, and stop them with it."""
    global sentinel  # noqa: PLW0603
    sentinel = Sentinel()
    tasks = [asyncio.create_task(sentinel.schedule_loop()), asyncio.create_task(sentinel.mission_loop())]
    log.info(
        "sentinel up: subscription=%s every %ss, planner=%s, gateway=%s",
        PUBSUB_SUBSCRIPTION or "(none: direct feed only)",
        POLL_SECONDS,
        sentinel.planner_name(),
        GATEWAY_URL,
    )
    yield
    for task in tasks:
        task.cancel()
    await sentinel.http.aclose()


app = FastAPI(title="cib-sentinel", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict:
    """Liveness."""
    return {"ok": True}


@app.get("/state")
async def state() -> dict:
    """Report the schedule, the queue and the planner in use."""
    return {
        "subscription": PUBSUB_SUBSCRIPTION,
        "interval": POLL_SECONDS,
        "next_wake": sentinel.next_wake,
        "busy": sentinel.busy,
        "queued": sentinel.queue.qsize() + len(sentinel.pending),
        "planner": sentinel.planner_name(),
        "error": sentinel.last_error,
        "watched": sorted(sentinel.watched),
    }


@app.post("/wake")
async def wake() -> dict:
    """Pull now instead of waiting for the schedule."""
    sentinel.wake.set()
    return {"ok": True}


@app.post("/clear")
async def clear() -> dict:
    """Forget the triggers that have not started a mission yet (the board is being reset).

    While a mission runs nothing is dropped: the reset is refused, and what waits stays queued.
    """
    if sentinel.busy:
        return {"ok": True, "dropped": 0, "busy": True}
    dropped = len(sentinel.pending) + sentinel.queue.qsize()
    sentinel.pending.clear()
    while not sentinel.queue.empty():
        sentinel.queue.get_nowait()
    return {"ok": True, "dropped": dropped, "busy": False}


@app.post("/events")
async def direct_event(request: Request) -> dict:
    """Direct feed for a stack without a subscription: the console hands over the event it would have published."""
    if PUBSUB_SUBSCRIPTION:
        raise HTTPException(status_code=409, detail="a subscription is configured; events arrive through Pub/Sub")
    body = await request.json()
    event = {
        "id": str(body.get("id") or uuid.uuid4().hex),
        "type": str(body.get("type") or ""),
        "time": str(body.get("time") or ""),
        "body": body.get("body") if isinstance(body.get("body"), dict) else {},
    }
    await sentinel.intake(event, "direct")
    return {"ok": True}


if __name__ == "__main__":
    # Binds all interfaces inside the container; compose publishes no host port for the sentinel.
    uvicorn.run(
        app,
        host="0.0.0.0",  # nosec B104  # noqa: S104
        port=PORT,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
