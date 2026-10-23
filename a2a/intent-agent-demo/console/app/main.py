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

import uvicorn
from app.a2a import IntentAgentClient, SendResult, summarize_metrics
from app.audit import AuditHub
from app.config import Settings
from app.decisions import execute_plans, plan_decisions
from app.issuer import DemoIssuer, decode_unverified
from app.platform import PlatformClient, PlatformError, policy_triples, reach, schema_summary
from app.prompts import Gallery, Persona, load_gallery
from app.token_service import TokenExchangeError, TokenServiceClient
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

logger = logging.getLogger("console")

AUDIT_WAIT_SECONDS = 5.0
AuthMode = Literal["delegation", "raw_subject", "wrong_audience", "none"]


class State:
    """Everything built once at startup, reached through `request.app.state.ctx`."""

    def __init__(self, settings: Settings) -> None:
        """Build the clients from validated settings; the prompt gallery is validated here too."""
        self.settings = settings
        self.issuer = DemoIssuer.from_file(settings.demo_idp_signing_key_file, settings.demo_idp_issuer)
        self.token_service = TokenServiceClient(
            str(settings.token_service_url),
            settings.token_service_client_id,
            settings.token_service_client_secret.get_secret_value(),
            settings.request_timeout_seconds,
        )
        self.agent = IntentAgentClient(
            str(settings.intent_agent_url),
            str(settings.intent_agent_health_url),
            settings.request_timeout_seconds,
        )
        self.platform = PlatformClient(
            str(settings.indykite_base_url),
            settings.app_agent_credentials_token.get_secret_value(),
            settings.request_timeout_seconds,
        )
        self.audit = AuditHub(settings.audit_webhook_api_key.get_secret_value(), settings.audit_buffer_size)
        self.gallery: Gallery = load_gallery(settings.prompts_file)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Build the State once at startup, after Settings validated the environment."""
    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    application.state.ctx = State(settings)
    logger.info("console up: agent=%s token-service=%s", settings.intent_agent_url, settings.token_service_url)
    yield


app = FastAPI(title="Intent Agent demo console", lifespan=lifespan, docs_url=None, redoc_url=None)


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


# --- demo issuer ------------------------------------------------------------


@app.get("/.well-known/jwks.json")
async def jwks(request: Request) -> dict[str, Any]:
    """Publish the demo issuer's signing key (RFC 7517), the key the Token Service verifies against."""
    return _ctx(request).issuer.jwks()


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
        "demo_idp_issuer": s.demo_idp_issuer,
        "demo_idp_subject_audience": s.demo_idp_subject_audience,
        "console_client_id": s.console_client_id,
        "gemini_model": s.gemini_model,
        "personas": [p.model_dump() for p in ctx.gallery.personas],
    }


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
    return {"intent_agent": agent_ok, "token_service": ts_ok, "metrics": metrics}


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
    who = ctx.gallery.persona(persona)
    if who is None:
        raise HTTPException(status_code=404, detail=f"unknown persona {persona!r}")
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
    persona: str = Field(min_length=1)
    prompt: str = Field(min_length=1, max_length=8000)
    auth_mode: AuthMode = "delegation"
    decide: bool = True


def _mint_tokens(ctx: State, persona: Persona) -> tuple[str, str]:
    """Mint what a corporate IdP would issue: the user's token and the console's own (the actor)."""
    s = ctx.settings
    subject_token = ctx.issuer.mint(
        subject=persona.id,
        audience=s.demo_idp_subject_audience,
        ttl_seconds=s.token_ttl_seconds,
        claims={"name": persona.name},
    )
    actor_token = ctx.issuer.mint(
        subject=s.console_client_id,
        audience=s.intent_agent_audience,
        ttl_seconds=s.token_ttl_seconds,
    )
    return subject_token, actor_token


async def _bearer_for(
    ctx: State,
    tokens: dict[str, Any],
    mode: AuthMode,
    subject_token: str,
    actor_token: str,
) -> str | None:
    """Pick the Bearer for the chosen mode, exchanging at the Token Service when the mode calls for it.

    Fills `tokens["exchange"]` and `tokens["bearer_kind"]`. A refused exchange leaves the error in
    `tokens["exchange"]` and returns None with `bearer_kind` unset, which the caller treats as "stop here".
    """
    s = ctx.settings
    if mode in ("delegation", "wrong_audience"):
        audience = s.intent_agent_audience if mode == "delegation" else s.demo_idp_subject_audience
        try:
            exchange = await ctx.token_service.exchange(
                subject_token=subject_token,
                actor_token=actor_token,
                audience=audience,
            )
        except TokenExchangeError as exc:
            tokens["exchange"] = {"error": {"status": exc.status, "body": exc.body}}
            return None
        tokens["exchange"] = {
            "claims": exchange.claims,
            "expires_in": exchange.expires_in,
            "latency_ms": exchange.latency_ms,
            "audience": audience,
        }
        tokens["bearer_kind"] = "delegation token (Token Service)"
        return exchange.access_token
    if mode == "raw_subject":
        tokens["bearer_kind"] = "raw subject token (demo issuer, not exchanged)"
        return subject_token
    tokens["bearer_kind"] = "no Authorization header"
    return None


def _a2a_view(ctx: State, result: SendResult) -> dict[str, Any]:
    """Build the A2A leg of the trace: the exact request, the raw envelope and the parsed outcome."""
    return {
        "url": ctx.agent.rpc_url,
        "request_headers": result.request_headers,
        "request": result.request,
        "http_status": result.http_status,
        "latency_ms": result.latency_ms,
        "task_state": result.task_state,
        "status_messages": result.status_messages,
        "envelope": result.envelope,
        "body_text": None if result.envelope is not None else result.body_text,
        "rpc_error": result.rpc_error,
        "transport_error": result.transport_error,
        "payload": result.payload,
        "outcome": result.outcome,
    }


@app.post("/api/prompt")
async def send_prompt(request: Request, body: PromptRequest) -> dict[str, Any]:
    """Run one prompt end to end: mint, exchange, SendMessage, audit match, decision; return the full trace."""
    ctx = _ctx(request)
    persona = ctx.gallery.persona(body.persona)
    if persona is None:
        raise HTTPException(status_code=404, detail=f"unknown persona {body.persona!r}")
    request_id = uuid.uuid4().hex

    # 1. The demo issuer mints what a corporate IdP would.
    subject_token, actor_token = _mint_tokens(ctx, persona)
    tokens: dict[str, Any] = {
        "subject": decode_unverified(subject_token),
        "actor": decode_unverified(actor_token),
        "exchange": None,
        "bearer_kind": None,
        "token_audit": [],
    }
    trace: dict[str, Any] = {
        "request_id": request_id,
        "persona": persona.model_dump(),
        "auth_mode": body.auth_mode,
        "tokens": tokens,
        "a2a": None,
        "audit": None,
        "decisions": None,
    }
    # Bookmark the audit ring: the Token Service's records for this prompt (exchange, then the
    # agent's introspection) arrive after this point and are matched by the ids of the tokens
    # this prompt minted or received - never by position, so concurrent prompts stay apart.
    seq_before = ctx.audit.last_seq
    known_jtis = {tokens["subject"].get("jti"), tokens["actor"].get("jti")} - {None}

    # 2. Exchange at the Token Service for a delegation token, audience intent_agent.
    bearer = await _bearer_for(ctx, tokens, body.auth_mode, subject_token, actor_token)
    if bearer is None and tokens["bearer_kind"] is None:
        tokens["token_audit"] = await _token_audit(ctx, seq_before, known_jtis, expected=1)
        return trace

    # 3. A2A SendMessage to the Intent Agent, the caller's identity in message.metadata.
    result = await ctx.agent.send(
        prompt=body.prompt,
        subject_type=persona.type,
        subject_id=persona.id,
        bearer=bearer,
        request_id=request_id,
    )
    trace["a2a"] = _a2a_view(ctx, result)

    # 4. The audit record the agent delivered to our webhook for this request id, and the
    #    Token Service's records for this prompt's tokens.
    trace["audit"] = await ctx.audit.wait_for(request_id, AUDIT_WAIT_SECONDS)
    delegation_jti = ((tokens["exchange"] or {}).get("claims") or {}).get("jti")
    if delegation_jti:
        known_jtis.add(delegation_jti)
    # After an exchange two records are due (exchange + the agent's introspection); a raw token yields one.
    expected = 2 if delegation_jti else (1 if bearer else 0)
    tokens["token_audit"] = await _token_audit(ctx, seq_before, known_jtis, expected)

    # 5. The decision step: what the platform answers for these evaluations today.
    evaluations = (result.payload or {}).get("evaluations") if result.payload else None
    if body.decide and evaluations:
        trace["decisions"] = await _decide(ctx, evaluations, persona.type, persona.id)
    return trace


async def _token_audit(ctx: State, seq_before: int, jtis: set[str], expected: int) -> list[dict[str, Any]]:
    """Token Service records whose tokenID is one of this prompt's tokens (delegation, subject or actor).

    Records that carry none of them (a refused exchange logs no token id, a no-token request makes no
    Token Service call) are left to the general Audit view rather than guessed from timing. The Token
    Service delivers asynchronously: the wait ends early once `expected` records are in (the exchange and
    the agent's introspection after a delegation, the introspection alone for a raw token), otherwise at
    the end of a bounded grace period with whatever arrived.
    """
    if not jtis or expected <= 0:
        return []
    records: list[dict[str, Any]] = []
    for _ in range(6):
        records = [
            entry["record"]
            for entry in ctx.audit.since(seq_before)
            if entry["source"] == "token-service" and entry["record"].get("tokenID") in jtis
        ]
        if len(records) >= expected:
            return records
        await asyncio.sleep(0.25)
    return records


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
