# Copyright (c) 2026 IndyKite
"""FastAPI entrypoint: the console's HTTP API, the audit webhook and the static UI."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import httpx
import uvicorn
from app import ibac
from app.a2a import IntentAgentClient, summarize_metrics
from app.audit import AuditHub
from app.auth import BEARER_KIND, Auth0Login, Session, bearer_for, decode_unverified, require_session
from app.auth import router as auth_router
from app.config import Settings
from app.decisions import execute_plans, plan_decisions
from app.fleet import Fleet, engine_params, load_fleet
from app.gateway import GatewayClient
from app.platform import PlatformClient, PlatformError, policy_triples, reach, schema_summary
from app.prompts import Gallery, Persona, load_gallery
from app.token_service import TokenServiceClient
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

logger = logging.getLogger("console")

AUDIT_WAIT_SECONDS = 5.0
AuthMode = Literal["delegation", "tampered", "wrong_audience", "none"]


class State:
    """Everything built once at startup, reached through `request.app.state.ctx`."""

    def __init__(self, settings: Settings) -> None:
        """Build the clients from validated settings; the prompt gallery is validated here too."""
        self.settings = settings
        # One connection pool for every outbound call; closed by the lifespan.
        self.http = httpx.AsyncClient(timeout=settings.request_timeout_seconds)
        self.token_service = TokenServiceClient(
            str(settings.token_service_url),
            settings.token_service_client_id,
            settings.request_timeout_seconds,
            client=self.http,
        )
        self.agent = IntentAgentClient(
            str(settings.intent_agent_url),
            str(settings.intent_agent_health_url),
            settings.request_timeout_seconds,
            client=self.http,
        )
        self.platform = PlatformClient(
            str(settings.indykite_base_url),
            settings.app_agent_credentials_token.get_secret_value(),
            settings.request_timeout_seconds,
            client=self.http,
        )
        self.audit = AuditHub(settings.audit_webhook_api_key.get_secret_value(), settings.audit_buffer_size)
        self.gallery: Gallery = load_gallery(settings.prompts_file)
        self.fleet: Fleet = load_fleet(settings.fleet_file)
        # One gateway per fleet agent, reached in-network as iag-<agent>:<port> (see docker-compose.yaml).
        self.gateways = GatewayClient(
            {
                a.id: settings.gateway_url_template.format(agent=a.id, port=settings.gateway_ports.get(a.id, 8888))
                for a in self.fleet.agents
            },
            settings.request_timeout_seconds,
            client=self.http,
        )
        # The console's login: the person's Auth0 token is what every prompt carries to a gateway.
        self.login = Auth0Login(
            domain=settings.auth0_domain,
            client_id=settings.console_idp_client_id,
            client_secret=settings.console_idp_client_secret.get_secret_value(),
            audience=settings.auth0_people_audience,
            public_url=str(settings.console_public_url),
            timeout=settings.request_timeout_seconds,
        )


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Build the State once at startup, after Settings validated the environment."""
    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    application.state.ctx = State(settings)
    logger.info(
        "console up: agent=%s token-service=%s fleet=%d agents",
        settings.intent_agent_url,
        settings.token_service_url,
        len(application.state.ctx.fleet.agents),
    )
    yield
    await application.state.ctx.http.aclose()


app = FastAPI(title="Intent + IBAC demo console", lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(auth_router(lambda request: request.app.state.ctx.login))


def _ctx(request: Request) -> State:
    return request.app.state.ctx


# --- static UI --------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def index(request: Request) -> FileResponse:
    """Serve the console page."""
    return FileResponse(_ctx(request).settings.static_dir / "index.html")


@app.get("/architecture", include_in_schema=False)
async def architecture(request: Request) -> FileResponse:
    """Serve the architecture page."""
    return FileResponse(_ctx(request).settings.static_dir / "architecture.html")


# --- read-only configuration and status -------------------------------------


@app.get("/api/config")
async def config(request: Request) -> dict[str, Any]:
    """Non-secret runtime facts the UI needs: endpoints, audiences, identities, personas."""
    ctx = _ctx(request)
    s = ctx.settings
    return {
        "intent_agent_url": str(s.intent_agent_url),
        "intent_agent_audience": s.intent_agent_audience,
        "token_service_url": str(s.token_service_url),
        "token_service_client_id": s.token_service_client_id,
        "indykite_base_url": str(s.indykite_base_url),
        "auth0_issuer": ctx.login.issuer,
        "people_audience": s.auth0_people_audience,
        "console_idp_client_id": s.console_idp_client_id,
        "console_client_id": s.console_client_id,
        "gemini_model": s.gemini_model,
        "personas": [p.model_dump() for p in ctx.gallery.personas],
        "fleet": [
            {
                "id": a.id,
                "name": ctx.fleet.capabilities[a.id].name,
                "accent": a.accent,
                "gateway_url": ctx.gateways.url(a.id),
                "gateway_port": s.gateway_ports.get(a.id),
            }
            for a in ctx.fleet.agents
        ],
    }


@app.get("/api/gateway-card")
async def gateway_card(request: Request, agent: str) -> dict[str, Any]:
    """Serve the card a gateway publishes for one fleet agent: its interface, security scheme and extension."""
    ctx = _ctx(request)
    if ctx.fleet.agent(agent) is None:
        raise HTTPException(status_code=404, detail=f"unknown agent {agent!r}")
    try:
        return await ctx.gateways.card(agent)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"gateway card unavailable: {exc}") from exc


@app.get("/api/fleet")
async def fleet(request: Request) -> dict[str, Any]:
    """Serve the agent cards of the fleet, each with the mappings the IBAC engine read from its skills."""
    return {"agents": _ctx(request).fleet.cards()}


@app.get("/api/prompts")
async def prompts(request: Request) -> dict[str, Any]:
    """Return the prompt gallery, grouped by capability."""
    return {"categories": [c.model_dump() for c in _ctx(request).gallery.categories]}


@app.get("/api/health/console")
async def health_console() -> dict[str, str]:
    """Liveness of the console process itself (used by the container healthcheck)."""
    return {"status": "ok"}


@app.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    """Reachability of the Intent Agent and the Token Service, plus the agent's request metrics."""
    ctx = _ctx(request)
    agent_ok, ts_ok = await asyncio.gather(ctx.agent.health(), _token_service_ok(ctx), return_exceptions=False)
    metrics: dict[str, Any] | None = None
    if agent_ok:
        try:
            metrics = summarize_metrics(await ctx.agent.metrics_text())
        except Exception as exc:
            logger.debug("metrics unavailable: %s", exc)
    states = await asyncio.gather(*(ctx.gateways.healthy(a) for a in ctx.gateways.agents))
    gateways = dict(zip(ctx.gateways.agents, states, strict=True))
    return {"intent_agent": agent_ok, "token_service": ts_ok, "gateways": gateways, "metrics": metrics}


async def _token_service_ok(ctx: State) -> bool:
    try:
        await ctx.token_service.openid_configuration()
    except Exception:
        return False
    return True


@app.get("/api/agent-card")
async def agent_card(request: Request) -> dict[str, Any]:
    """Fetch the Intent Agent's public agent card and return it as is."""
    try:
        return await _ctx(request).agent.agent_card()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"agent card unavailable: {exc}") from exc


@app.get("/api/token-service")
async def token_service_info(request: Request) -> dict[str, Any]:
    """Discovery document and published keys of the Token Service, plus how the console and agent reach it."""
    ctx = _ctx(request)
    try:
        configuration, keys = await asyncio.gather(ctx.token_service.openid_configuration(), ctx.token_service.jwks())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"token service unavailable: {exc}") from exc
    return {
        "configuration": configuration,
        "keys": [{k: v for k, v in key.items() if k in ("kid", "alg", "use", "kty")} for key in keys.get("keys", [])],
        "exchange_url": ctx.token_service.exchange_url,
        "introspect_url": ctx.token_service.introspect_url,
        "client_id": ctx.settings.token_service_client_id,
    }


@app.get("/api/schema")
async def schema(request: Request) -> dict[str, Any]:
    """Fetch the project's data schema from the platform, flattened for the vocabulary page."""
    try:
        return schema_summary(await _ctx(request).platform.schema())
    except PlatformError as exc:
        raise HTTPException(status_code=502, detail={"status": exc.status, "body": exc.body}) from exc


@app.get("/api/persona-facts")
async def persona_facts(request: Request, persona: str) -> dict[str, Any]:
    """Live facts about one persona: each granted (action, resource type) with the resources reached now."""
    ctx = _ctx(request)
    who = ctx.gallery.persona(persona) or Persona(
        id=persona,
        type="Person",
        name=persona,
        role="a Person of the tenant",
    )
    try:
        listing = await ctx.platform.policies(who.type)
    except PlatformError as exc:
        raise HTTPException(status_code=502, detail={"status": exc.status, "body": exc.body}) from exc
    facts = await reach(ctx.platform, who.type, who.id, policy_triples(listing))
    return {"persona": who.model_dump(), "facts": facts}


@app.get("/api/policies")
async def policies(request: Request, subject_type: str) -> dict[str, Any]:
    """Fetch the ACTIVE KBAC policies of one subject type, as granted triples plus the raw listing."""
    try:
        listing = await _ctx(request).platform.policies(subject_type)
    except PlatformError as exc:
        raise HTTPException(status_code=502, detail={"status": exc.status, "body": exc.body}) from exc
    return {"subject_type": subject_type, "policies": policy_triples(listing), "raw": listing}


# --- the prompt round trip --------------------------------------------------


class PromptRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    auth_mode: AuthMode = "delegation"
    decide: bool = True
    # The fleet agent whose gateway the prompt goes through; the first agent of the fleet when empty.
    agent: str = ""


def _person(ctx: State, session: Session) -> Persona:
    """Return the logged-in person as a persona: the gallery's entry when it is one of the demo's people."""
    known = ctx.gallery.persona(session.sub)
    if known is not None:
        return known
    return Persona(
        id=session.sub,
        type="Person",
        name=session.name,
        role="a Person of the tenant, not one of the demo's people",
        email=session.email,
    )


def _gateway_records(ctx: State, seq_before: int, agent: str) -> list[dict[str, Any]]:
    """Return the records the chosen gateway wrote after the bookmark, oldest first."""
    return [
        entry["record"]
        for entry in ctx.audit.since(seq_before)
        if entry["source"] == "agent-gateway" and entry["record"].get("service") == f"iag-{agent}"
    ]


def _token_service_records(ctx: State, seq_before: int, subject: str) -> list[dict[str, Any]]:
    """Return the Token Service's records for this persona after the bookmark: the exchange, introspections."""
    return [
        entry["record"]
        for entry in ctx.audit.since(seq_before)
        if entry["source"] == "token-service" and entry["record"].get("subject") == subject
    ]


def _is_intent_record(entry: dict[str, Any], persona: Persona, prompt: str) -> bool:
    """Tell whether an entry is the Intent Agent's own record of this prompt: same subject, same prompt."""
    record = entry["record"]
    return (
        entry["source"] == "intent-agent"
        and record.get("subject_id") == persona.id
        and record.get("subject_type") == persona.type
        and record.get("prompt") == prompt
    )


@app.post("/api/prompt")
async def send_prompt(request: Request, body: PromptRequest) -> dict[str, Any]:
    """Run one prompt end to end through a gateway and return the full trace.

    The console sends the prompt with the logged-in person's token to the chosen agent's gateway and then
    reads what happened from the records the gateway, the Token Service and the Intent Agent delivered
    to its webhook: introspection, whoami, the intent, the exchange, the admission, the forward.
    """
    ctx = _ctx(request)
    session = require_session(ctx.login, request)
    persona = _person(ctx, session)
    agent = body.agent or ctx.fleet.agents[0].id
    if ctx.fleet.agent(agent) is None:
        raise HTTPException(status_code=404, detail=f"unknown agent {agent!r}")
    request_id = uuid.uuid4().hex

    # 1. The person's own Auth0 token, as the login obtained it (or one of the guardrail variants).
    subject_token = bearer_for(session, body.auth_mode)
    tokens: dict[str, Any] = {
        "subject": decode_unverified(subject_token) if subject_token else None,
        "bearer_kind": BEARER_KIND[body.auth_mode],
        "issuer": ctx.login.issuer,
        "token_audit": [],
    }
    trace: dict[str, Any] = {
        "request_id": request_id,
        "persona": persona.model_dump(),
        "agent": agent,
        "auth_mode": body.auth_mode,
        "tokens": tokens,
        "gateway": None,
        "a2a": None,
        "ibac": None,
        "audit": None,
        "decisions": None,
    }
    # Bookmark the audit ring: everything this prompt causes lands after this point. Records are
    # matched by what they carry (the gateway's service name, the persona, the prompt), never by position.
    seq_before = ctx.audit.last_seq

    # 2. The A2A message to the gateway in front of the chosen agent.
    answer = await ctx.gateways.send(agent=agent, prompt=body.prompt, bearer=subject_token, request_id=request_id)
    trace["gateway"] = answer.as_dict()

    # 3. What the parties recorded: the Intent Agent's own record (the evaluations), the gateway's
    #    records (INTENT, admission), the Token Service's (the gateway's exchange).
    forwarded_or_answered = answer.kind in ("task", "intent_error")
    if forwarded_or_answered:
        trace["audit"] = await ctx.audit.wait_until(
            lambda e: _is_intent_record(e, persona, body.prompt),
            seq_before,
            AUDIT_WAIT_SECONDS,
        )
    else:
        await asyncio.sleep(0.5)
    trace["gateway"]["records"] = _gateway_records(ctx, seq_before, agent)
    tokens["token_audit"] = _token_service_records(ctx, seq_before, persona.id)
    # The delegated token the agent received, as the agent reported it, is the chain to show.
    received = answer.received or {}
    tokens["delegated"] = received.get("x_ik_token")

    # 4. The intent, as the Intent Agent recorded it, in the shape the tickets expect.
    record = (trace["audit"] or {}).get("record") or {}
    payload: dict[str, Any] | None = None
    if answer.kind == "intent_error":
        payload = dict(answer.intent_error or {})
    elif record.get("outcome") == "success" and isinstance(record.get("evaluations"), list):
        payload = {"prompt": body.prompt, "evaluations": record["evaluations"]}
    trace["a2a"] = {
        "outcome": "success" if payload and "evaluations" in payload else "refused" if payload else "error",
        "payload": payload,
        "http_status": answer.http_status,
        "latency_ms": answer.latency_ms,
        "task_state": (answer.task or {}).get("status", {}).get("state") if answer.task else None,
        "url": answer.url,
        "request": answer.request,
        "request_headers": answer.request_headers,
        "envelope": answer.envelope,
        "body_text": None if answer.envelope is not None else answer.body_text,
        "rpc_error": answer.rpc_error,
        "transport_error": answer.transport_error,
        "error_text": answer.error_text,
    }

    # 5. The mapping preview: the intent against every card of the fleet (the console's own check).
    evaluations = (payload or {}).get("evaluations")
    if evaluations is not None:
        trace["ibac"] = await _ibac(ctx, persona, tokens, payload or {}, decide=body.decide)

    # 6. The decision step: what the platform answers for the intent's own evaluations today.
    if body.decide and evaluations:
        trace["decisions"] = await _decide(ctx, evaluations, persona.type, persona.id)
    return trace


# --- the same prompt through every gateway ------------------------------------


class CompareRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    auth_mode: AuthMode = "delegation"
    # The fleet agents whose gateways get the prompt; every agent of the fleet when empty.
    agents: list[str] = Field(default_factory=list, max_length=16)


RECORD_SETTLE_SECONDS = 1.0
HTTP_FORBIDDEN = 403


def summarize_gateway_answer(answer: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Reduce one gateway's answer and records to what the fleet comparison shows per card.

    Everything here is the product's own output: the HTTP answer of the gateway, the skill the agent
    replied with, the chain in the delegated token the agent received, and the gateway's admission and
    INTENT records. The console adds only a one-word verdict for the card.
    """
    kind = answer.get("kind")
    status = answer.get("http_status")
    admission = next(
        (r for r in records if r.get("action") != "INTENT" and r.get("decision") in ("AUTHORIZED", "NOT_AUTHORIZED")),
        None,
    )
    intent = next((r for r in records if r.get("action") == "INTENT"), None)
    failure = next((r for r in records if r.get("decision") == "ERROR"), None)
    if answer.get("transport_error"):
        verdict = "unreachable"
    elif failure:
        # The gateway could not decide (its record says why: usually the platform refused X-IK-Token).
        verdict = "admission error"
    elif kind == "task":
        verdict = "forwarded"
    elif kind == "intent_error":
        verdict = "no intent"
    elif kind == "refused":
        verdict = "admission refused" if status == HTTP_FORBIDDEN else "token refused"
    else:
        verdict = "error"
    received = answer.get("received") or {}
    delegated = received.get("x_ik_token") or {}
    return {
        "agent": answer.get("agent"),
        "verdict": verdict,
        "kind": kind,
        "http_status": status,
        "latency_ms": answer.get("latency_ms"),
        "error_text": (failure or {}).get("reason") or answer.get("error_text") or answer.get("transport_error") or "",
        "skill": answer.get("skill"),
        "reply_text": answer.get("reply_text") or "",
        "chain": [delegated.get("sub"), *_act_chain(delegated.get("act"))] if delegated.get("sub") else [],
        "admission": {"decision": admission.get("decision"), "reason": admission.get("reason")} if admission else None,
        "intent": {"decision": intent.get("decision"), "duration_ms": intent.get("durationMs")} if intent else None,
        "records": records,
    }


def _act_chain(act: object) -> list[str]:
    """Flatten the nested `act` claim of a delegated token into the chain of actors, outermost first."""
    chain: list[str] = []
    while isinstance(act, dict) and act.get("sub"):
        chain.append(str(act["sub"]))
        act = act.get("act")
    return chain


@app.post("/api/compare")
async def compare(request: Request, body: CompareRequest) -> dict[str, Any]:
    """Send the same prompt through several gateways at once and return what each one did.

    This is how a fleet is compared for real: there is no central engine, each gateway checks its own
    card and its own admission, so the question "which agents would take this prompt" is answered by
    asking each of them. One Intent Agent call per gateway.
    """
    ctx = _ctx(request)
    session = require_session(ctx.login, request)
    persona = _person(ctx, session)
    agents = body.agents or [a.id for a in ctx.fleet.agents]
    unknown = [a for a in agents if ctx.fleet.agent(a) is None]
    if unknown:
        raise HTTPException(status_code=404, detail=f"unknown agents {unknown!r}")
    seq_before = ctx.audit.last_seq

    async def one(agent: str) -> dict[str, Any]:
        token = bearer_for(session, body.auth_mode)
        answer = await ctx.gateways.send(agent=agent, prompt=body.prompt, bearer=token, request_id=uuid.uuid4().hex)
        return answer.as_dict()

    answers = await asyncio.gather(*(one(a) for a in agents))
    # The gateways deliver their records asynchronously; give them a moment, then read per service name.
    await asyncio.sleep(RECORD_SETTLE_SECONDS)
    return {
        "persona": persona.model_dump(),
        "prompt": body.prompt,
        "auth_mode": body.auth_mode,
        "answers": [
            summarize_gateway_answer(answer, _gateway_records(ctx, seq_before, agent))
            for agent, answer in zip(agents, answers, strict=True)
        ],
    }


# --- the cards as the person sees them --------------------------------------------


def summarize_card_answer(answer: dict[str, Any], listed: list[str], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Reduce one gateway's extended-card answer to what the page shows: kept skills, the DISCOVERY record."""
    discovery = next((r for r in records if r.get("action") == "DISCOVERY"), None)
    kept = answer.get("skills") or []
    return {
        "agent": answer.get("agent"),
        "kind": answer.get("kind"),
        "http_status": answer.get("http_status"),
        "latency_ms": answer.get("latency_ms"),
        "error": answer.get("error") or "",
        "listed": listed,
        "kept": [sk for sk in listed if sk in kept],
        "hidden": [sk for sk in listed if sk not in kept],
        "discovery": {
            "decision": discovery.get("decision"),
            "reason": discovery.get("reason"),
            "cached": (discovery.get("cached") or {}).get("skill"),
            "listed": (discovery.get("discovery") or {}).get("listed"),
            "kept": (discovery.get("discovery") or {}).get("kept"),
        }
        if discovery
        else None,
        "records": records,
    }


@app.get("/api/my-cards")
async def my_cards(request: Request) -> dict[str, Any]:
    """Ask every gateway for its extended card with the logged-in person's token.

    The gateway reads the skills this person may use (one AuthZEN search per gateway, action CAN_USE, type
    Skill, the chain in X-IK-Token) and answers the card with those skills only; it records the filtering as
    a DISCOVERY. The console shows the card as the person sees it, next to the preview of its mappings.
    """
    ctx = _ctx(request)
    session = require_session(ctx.login, request)
    agents = [a.id for a in ctx.fleet.agents]
    seq_before = ctx.audit.last_seq
    answers = await asyncio.gather(
        *(
            ctx.gateways.extended_card(agent=agent, bearer=session.access_token, request_id=uuid.uuid4().hex)
            for agent in agents
        ),
    )
    await asyncio.sleep(RECORD_SETTLE_SECONDS)
    cards = []
    for agent, answer in zip(agents, answers, strict=True):
        listed = [sk.id for sk in ctx.fleet.capabilities[agent].skills]
        cards.append(summarize_card_answer(answer.as_dict(), listed, _gateway_records(ctx, seq_before, agent)))
    return {"person": _person(ctx, session).model_dump(), "cards": cards}


# --- the mapping preview over the fleet ----------------------------------------


def _audit_options(ctx: State, intent: ibac.Intent, persona: Persona) -> ibac.AuditOptions:
    """Build what a gateway would add to the engine's records: the prompt, the subject, its own actor chain."""
    return ibac.AuditOptions(
        prompt=intent.prompt,
        subject=persona.id,
        actors_chain=(ctx.settings.console_client_id,),
        service=ctx.settings.console_client_id,
    )


async def _ibac(
    ctx: State,
    persona: Persona,
    tokens: dict[str, Any],
    payload: dict[str, Any],
    *,
    decide: bool,
) -> dict[str, Any]:
    """Run discovery and usage over the fleet for one intent; decide the generated requests; audit each call."""
    intent = ibac.Intent.from_dict(payload)
    claims = tokens.get("delegated") or tokens.get("subject") or {}
    params = engine_params(persona.type, persona.id, claims, payload.get("evaluations") or [])
    run = ctx.fleet.run(intent, params, _audit_options(ctx, intent, persona))
    for record in run["records"]:
        await ctx.audit.publish(record)
    if decide:
        for usage in run["usage"]:
            if usage["requests"]:
                usage["plans"] = await _decide_generated(ctx, usage["requests"], persona)
    return run


async def _decide_generated(ctx: State, requests: list[dict[str, Any]], persona: Persona) -> list[dict[str, Any]]:
    """Send the engine's generated AuthZEN requests to the platform through the same mapper as the intent.

    The engine puts the params map of its call in `context.input_params`; the platform reads scalar
    `input_params` a policy declares as `$name`, so each request goes through `plan_decisions` with
    the parameters it was generated from as its context, exactly like the intent's own evaluations.
    """
    # One plan per request, in the order of the requests: the UI pairs them by position.
    plans: list[dict[str, Any] | None] = [None] * len(requests)
    by_type: dict[str, list[int]] = {}
    shaped: list[dict[str, Any]] = []
    for position, req in enumerate(requests):
        one = {k: v for k, v in req.items() if k in ("subject", "action", "resource")}
        generated_from = ((req.get("context") or {}).get("input_params") or {}).get("parameters") or {}
        one["context"] = {"parameters": dict(generated_from)}
        shaped.append(one)
        by_type.setdefault((req.get("subject") or {}).get("type", ""), []).append(position)
    for subject_type, positions in by_type.items():
        decided = await _decide(ctx, [shaped[p] for p in positions], subject_type, persona.id)
        for offset, position in enumerate(positions):
            if decided.get("error"):
                plan: dict[str, Any] = {"kind": "evaluation", "error": decided["error"], "request": shaped[position]}
            else:
                plan = decided["plans"][offset]
            plans[position] = {**plan, "index": position}
    return [plan for plan in plans if plan is not None]


class UsageRequest(BaseModel):
    persona: str = Field(min_length=1)
    agent: str = Field(min_length=1)
    skill: str = Field(min_length=1)
    intent: dict[str, Any]
    token_claims: dict[str, Any] = Field(default_factory=dict)
    decide: bool = True


@app.post("/api/ibac/usage")
async def ibac_usage(request: Request, body: UsageRequest) -> dict[str, Any]:
    """Run the usage flow for one (agent, skill) on an intent already extracted - the "what if" of the board.

    Not audited: it is the presenter asking, not a request on its way to an agent.
    """
    ctx = _ctx(request)
    persona = ctx.gallery.persona(body.persona)
    if persona is None:
        raise HTTPException(status_code=404, detail=f"unknown persona {body.persona!r}")
    agent = ctx.fleet.agent(body.agent)
    if agent is None or body.skill not in [s.id for s in ctx.fleet.capabilities[agent.id].skills]:
        raise HTTPException(status_code=404, detail="unknown agent or skill")
    intent = ibac.Intent.from_dict(body.intent)
    params = engine_params(persona.type, persona.id, body.token_claims, body.intent.get("evaluations") or [])
    usage = ctx.fleet.usage(agent.id, body.skill, intent, params, _audit_options(ctx, intent, persona))
    if body.decide and usage["requests"]:
        usage["plans"] = await _decide_generated(ctx, usage["requests"], persona)
    return {"params": params, "usage": usage}


class DecideRequest(BaseModel):
    subject_type: str = Field(min_length=1)
    # The caller's id lets a second-subject evaluation be gated; without it every evaluation runs.
    subject_id: str | None = None
    evaluations: list[dict[str, Any]] = Field(min_length=1, max_length=64)


@app.post("/api/decide")
async def decide(request: Request, body: DecideRequest) -> dict[str, Any]:
    """Map evaluations to AuthZEN calls and execute them (the decision step on its own)."""
    return await _decide(_ctx(request), body.evaluations, body.subject_type, body.subject_id)


async def _decide(
    ctx: State,
    evaluations: list[dict[str, Any]],
    subject_type: str,
    subject_id: str | None = None,
) -> dict[str, Any]:
    try:
        listing = await ctx.platform.policies(subject_type)
    except PlatformError as exc:
        return {"error": f"policy listing failed: {exc.status} {exc.body}", "plans": []}
    caller = {"type": subject_type, "id": subject_id} if subject_id else None
    plans = plan_decisions(evaluations, policy_triples(listing), caller)
    await execute_plans(ctx.platform, plans)
    return {"plans": [plan.as_dict() for plan in plans]}


# --- audit webhook and live stream ------------------------------------------


@app.post("/audit/webhook", include_in_schema=False)
@app.put("/audit/webhook", include_in_schema=False)
async def audit_webhook(
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
) -> Response:
    """Receive one audit record from the Intent Agent or the Token Service; X-API-Key must match."""
    ctx = _ctx(request)
    if not ctx.audit.authorized(x_api_key):
        raise HTTPException(status_code=401, detail="bad or missing X-API-Key")
    try:
        record = json.loads(await request.body())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="body is not JSON") from exc
    if not isinstance(record, dict):
        raise HTTPException(status_code=400, detail="record must be a JSON object")
    await ctx.audit.publish(record)
    return Response(status_code=204)


@app.get("/api/audit/recent")
async def audit_recent(request: Request, limit: int = 100) -> dict[str, Any]:
    """Return the newest audit entries kept in memory."""
    return {"entries": _ctx(request).audit.recent(min(max(limit, 1), 500))}


@app.get("/api/audit/stream")
async def audit_stream(request: Request) -> StreamingResponse:
    """Server-sent events: every audit entry as it arrives, with keepalives."""
    hub = _ctx(request).audit

    async def events():
        queue = hub.subscribe()
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"event: audit\ndata: {json.dumps(entry)}\n\n"
        finally:
            hub.unsubscribe(queue)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# --- static assets (mounted last so API routes win) -------------------------


def mount_static(application: FastAPI, static_dir: str) -> None:
    """Serve the UI files under /static (mounted last so API routes win)."""
    application.mount("/static", StaticFiles(directory=static_dir), name="static")


mount_static(app, str(Settings.model_fields["static_dir"].default))


def run() -> None:
    """Process entrypoint; the full Settings validation happens in lifespan and fails the boot loudly."""
    port = int(os.getenv("CONSOLE_PORT") or Settings.model_fields["console_port"].default)
    # Container listener: every interface on purpose, the port is published by compose/Kubernetes.
    bind_all = "0.0.0.0"  # nosec B104  # noqa: S104
    uvicorn.run("app.main:app", host=bind_all, port=port, log_level="info")


if __name__ == "__main__":
    run()
