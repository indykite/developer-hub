// Intent + IBAC demo console - front end. Plain JS, no build step.
// Everything shown comes from /api/*; nothing is inferred client-side that the
// backend did not return.

const state = {
  config: null,
  gallery: null,
  persona: null,
  authMode: "delegation",
  category: null,
  view: "playground",
  trace: null,
  wireTab: "request",
  auditEntries: [],
  suggested: "",
  vocabSubject: "User",
  graph: { mode: "schema", persona: null, policies: false, cy: null, loaded: false },
  schema: null,
  policies: {},
  fleet: null,
  // The (agent, skill) whose usage is open on the IBAC board, and the what-if answers fetched for unmatched skills.
  ibacPick: null,
  ibacWhatIf: new Map(),
};

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};
const pretty = (value) => (value === undefined || value === null ? "—" : JSON.stringify(value, null, 2));

// ---------- boot ----------

async function boot() {
  const [config, gallery] = await Promise.all([
    fetch("/api/config").then((r) => r.json()),
    fetch("/api/prompts").then((r) => r.json()),
  ]);
  state.config = config;
  state.gallery = gallery.categories;
  state.persona = config.personas[0];
  $("#railFleet").textContent = `· ${config.fleet.length} cards`;
  // Deep links for a talk track: ?view=fleet, or ?persona=roy&prompt=...&send=1 to replay a beat.
  const link = new URLSearchParams(location.search);
  const linked = config.personas.find((p) => p.id === link.get("persona"));
  if (linked) state.persona = linked;
  renderPersonas();
  renderPersonaFacts();
  suggestPrompt();
  selectView(["playground", "guardrails", "howibac", "fleet", "vocabulary", "audit", "agents", "graph"].includes(link.get("view")) ? link.get("view") : "playground");
  bindEvents();
  refreshHealth();
  setInterval(refreshHealth, 15000);
  loadAuditRecent();
  openAuditStream();
  if (link.get("prompt")) {
    $("#promptText").value = link.get("prompt");
    if (link.get("send") === "1") {
      // Send once per link: a reload or a restored tab must not replay the round trip (and the Gemini call).
      link.delete("send");
      history.replaceState(null, "", `${location.pathname}${link.size ? `?${link}` : ""}${location.hash}`);
      sendPrompt(link.get("prompt"));
    }
  }
}

function bindEvents() {
  $("#nav").addEventListener("click", (e) => {
    const btn = e.target.closest("button.nav-item");
    if (btn) selectView(btn.dataset.view);
  });
  $("#authMode").addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    state.authMode = btn.dataset.mode;
    [...$("#authMode").children].forEach((b) => b.classList.toggle("active", b === btn));
  });
  $("#sendBtn").addEventListener("click", () => sendPrompt($("#promptText").value));
  // Enter sends; Shift+Enter keeps a newline for multi-line prompts.
  $("#promptText").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      sendPrompt($("#promptText").value);
    }
  });
  $("#wireTabs").addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    state.wireTab = btn.dataset.tab;
    [...$("#wireTabs").children].forEach((b) => b.classList.toggle("active", b === btn));
    renderWire();
  });
  $("#vocabSubject").addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    state.vocabSubject = btn.dataset.type;
    [...$("#vocabSubject").children].forEach((b) => b.classList.toggle("active", b === btn));
    renderVocabulary();
  });
}

function selectView(view) {
  state.view = view;
  document.querySelectorAll(".nav-item[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  const playground = view === "playground" || view === "guardrails";
  $("#view-playground").hidden = !playground;
  $("#view-vocabulary").hidden = view !== "vocabulary";
  $("#view-audit").hidden = view !== "audit";
  $("#view-agents").hidden = view !== "agents";
  $("#view-graph").hidden = view !== "graph";
  $("#view-fleet").hidden = view !== "fleet";
  $("#view-howibac").hidden = view !== "howibac";
  if (playground) {
    // The failure modes belong to Guardrails; the Playground always sends a proper delegation token.
    document.querySelector(".auth-wrap").classList.toggle("guardrails", view === "guardrails");
    if (view !== "guardrails") setAuthMode("delegation");
    $("#viewTitle").textContent = view === "guardrails" ? "Try to break it." : "Ask as someone. Watch the intent find its agent.";
    $("#viewLede").textContent =
      view === "guardrails"
        ? "Instructions, encoded payloads, overrides, SQL, oversized filters, the wrong token: every one is refused with the same fixed shape, and every refusal is audited. Nothing refused ever reaches the IBAC engine."
        : "Pick an identity, send a sentence. The Intent Agent turns it into AuthZEN evaluations; the IBAC engine matches them against what every agent in the fleet declares it can do; the platform decides.";
    renderGallery();
  }
  if (view === "fleet") renderFleet();
  if (view === "vocabulary") renderVocabulary();
  if (view === "audit") renderAudit();
  if (view === "agents") renderAgents();
  if (view === "graph") renderGraph();
}

// ---------- health ----------

async function refreshHealth() {
  try {
    const h = await fetch("/api/health").then((r) => r.json());
    $("#dotAgent").className = `dot ${h.intent_agent ? "up" : "down"}`;
    $("#dotIts").className = `dot ${h.token_service ? "up" : "down"}`;
    const m = h.metrics;
    $("#railMetrics").textContent = m
      ? `${m.requests_total} requests · avg ${m.avg_latency_ms ?? "—"} ms`
      : "metrics unavailable";
  } catch {
    $("#dotAgent").className = "dot down";
    $("#dotIts").className = "dot down";
  }
}

// ---------- personas and gallery ----------

function renderPersonas() {
  const wrap = $("#personas");
  wrap.replaceChildren();
  for (const p of state.config.personas) {
    const b = el("button", `persona ${p.type.toLowerCase()}${p.id === state.persona.id ? " active" : ""}`);
    b.dataset.id = p.id;
    const av = el("span", "avatar", p.name.split(" ").map((w) => w[0]).join("").slice(0, 2));
    const txt = el("span");
    txt.append(el("div", "name", p.name), el("div", "type", `${p.type} · ${p.id}`));
    b.append(av, txt);
    b.addEventListener("click", () => {
      state.persona = p;
      renderPersonas();
      renderPersonaFacts();
      suggestPrompt();
    });
    wrap.append(b);
  }
}

// Prefill the composer with a real, sendable prompt for the selected identity.
// Only replaces text the console put there itself, never something the user typed.
function suggestPrompt() {
  const box = $("#promptText");
  if (box.value.trim() && box.value !== state.suggested) return;
  const first = state.gallery
    .filter((c) => c.kind !== "guardrail")
    .flatMap((c) => c.prompts)
    .find((p) => p.persona === state.persona.id);
  state.suggested = first ? first.text : "";
  box.value = state.suggested;
}

// Facts come from the graph: one AuthZEN search/resource per policy of the persona's type.
// The hand-written facts from prompts.json are shown only while loading or if the platform is down.
const factsCache = new Map();

async function renderPersonaFacts() {
  const wrap = $("#personaFacts");
  const persona = state.persona;
  wrap.replaceChildren(el("span", "role", persona.role), el("span", "loading", "reading the graph…"));
  let facts = factsCache.get(persona.id);
  if (!facts) {
    try {
      const res = await fetch(`/api/persona-facts?persona=${encodeURIComponent(persona.id)}`);
      if (!res.ok) throw new Error(res.statusText);
      facts = (await res.json()).facts;
      factsCache.set(persona.id, facts);
      setTimeout(() => factsCache.delete(persona.id), 60000);
    } catch {
      if (state.persona !== persona) return;
      wrap.replaceChildren(el("span", "role", persona.role), ...persona.facts.map((f) => el("span", "", f)), el("span", "loading", "platform unreachable: showing the static description"));
      return;
    }
  }
  if (state.persona !== persona) return;
  wrap.replaceChildren(el("span", "role", persona.role));
  const reached = facts.filter((f) => f.ids.length);
  const needsParam = facts.filter((f) => f.params.length && !f.error);
  const empty = facts.filter((f) => !f.ids.length && !f.params.length && !f.error);
  for (const f of reached) {
    const chip = el("span", "fact");
    chip.append(el("b", "", f.action), document.createTextNode(` → ${f.resource_type}: `), el("i", "", f.ids.join(", ")));
    wrap.append(chip);
  }
  for (const f of needsParam) {
    const chip = el("span", "fact param");
    chip.append(el("b", "", f.action), document.createTextNode(` → ${f.resource_type} needs ${f.params.map((p) => `$${p}`).join(", ")}`));
    wrap.append(chip);
  }
  if (empty.length) wrap.append(el("span", "fact none", `nothing reached for ${empty.map((f) => f.action).join(", ")}`));
  for (const f of facts.filter((x) => x.error)) wrap.append(el("span", "fact none", `${f.action}: ${f.error}`));
}

function renderGallery() {
  const guard = state.view === "guardrails";
  const cats = state.gallery.filter((c) => (c.kind === "guardrail") === guard);
  if (!cats.find((c) => c.id === state.category)) state.category = cats[0]?.id;
  const tabs = $("#galleryTabs");
  tabs.replaceChildren();
  for (const c of cats) {
    const b = el("button", `${c.id === state.category ? "active" : ""}${guard ? " guard" : ""}`, c.title);
    b.addEventListener("click", () => {
      state.category = c.id;
      renderGallery();
    });
    tabs.append(b);
  }
  const cat = cats.find((c) => c.id === state.category);
  $("#galleryBlurb").textContent = cat?.blurb ?? "";
  const cards = $("#galleryCards");
  cards.replaceChildren();
  for (const p of cat?.prompts ?? []) {
    const persona = state.config.personas.find((x) => x.id === p.persona);
    const card = el("button", "card");
    const text = el("div", "text", p.text);
    text.title = p.text;
    const expect = el("div", "expect", p.expect ? `→ ${p.expect}` : "");
    if (p.expect) expect.title = p.expect;
    card.append(
      el("div", "who", `${persona?.name ?? p.persona} · ${persona?.type ?? ""}${p.auth_mode !== "delegation" ? ` · ${p.auth_mode.replace("_", " ")}` : ""}`),
      text,
      expect,
    );
    card.addEventListener("click", () => {
      state.persona = persona;
      renderPersonas();
      renderPersonaFacts();
      setAuthMode(p.auth_mode);
      $("#promptText").value = p.text;
      sendPrompt(p.text);
    });
    cards.append(card);
  }
}

function setAuthMode(mode) {
  state.authMode = mode;
  [...$("#authMode").children].forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
}

// ---------- the round trip ----------

const STEPS = ["issue", "exchange", "introspect", "bind", "vocab", "extract", "validate", "discover", "confirm", "audit"];

function setStep(step, status, note) {
  const node = document.querySelector(`.pipe-node[data-step="${step}"]`);
  node.className = `pipe-node ${status}`;
  node.querySelector("em").textContent = note ?? "";
}

function resetPipeline() {
  for (const s of STEPS) setStep(s, "");
}

async function sendPrompt(text) {
  const prompt = (text || "").trim();
  if (!prompt) return;
  const btn = $("#sendBtn");
  if (btn.disabled) return; // a request is already running; Enter and the cards share this guard
  btn.disabled = true;
  $("#result").hidden = true;
  resetPipeline();
  let i = 0;
  setStep(STEPS[0], "running");
  const ticker = setInterval(() => {
    if (i < STEPS.length - 1) {
      setStep(STEPS[i], "done");
      i += 1;
      setStep(STEPS[i], "running");
    }
  }, 420);
  try {
    const res = await fetch("/api/prompt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ persona: state.persona.id, prompt, auth_mode: state.authMode, decide: true }),
    });
    const trace = await res.json();
    clearInterval(ticker);
    if (!res.ok) throw new Error(trace.detail ? JSON.stringify(trace.detail) : res.statusText);
    state.trace = trace;
    paintPipeline(trace);
    renderResult(trace);
  } catch (err) {
    clearInterval(ticker);
    resetPipeline();
    setStep("issue", "fail", "console error");
    $("#result").hidden = false;
    $("#verdict").className = "result-verdict error";
    $("#verdict").textContent = "The console could not complete the request.";
    $("#resultMeta").textContent = String(err);
    $("#tickets").replaceChildren();
    $("#refusal").hidden = true;
    $("#wireBody").textContent = String(err);
  } finally {
    btn.disabled = false;
    refreshHealth();
  }
}

function paintPipeline(trace) {
  const t = trace.tokens;
  setStep("issue", "done", `${t.subject.sub} · ${t.actor.sub}`);
  if (t.exchange?.error) {
    setStep("exchange", "fail", `${t.exchange.error.status ?? "no answer"}`);
    STEPS.slice(2).forEach((s) => setStep(s, "skip"));
    return;
  }
  if (t.exchange) setStep("exchange", trace.auth_mode === "wrong_audience" ? "warn" : "done", `${t.exchange.latency_ms} ms · aud ${t.exchange.audience}`);
  else setStep("exchange", "skip", trace.auth_mode === "none" ? "no token" : "not exchanged");

  const a = trace.a2a;
  const status = a.http_status;
  if (a.transport_error) {
    setStep("introspect", "fail", "unreachable");
    STEPS.slice(3).forEach((s) => setStep(s, "skip"));
    return;
  }
  if (status === 401 || status === 500 || status === 502) {
    setStep("introspect", "fail", `HTTP ${status}`);
    STEPS.slice(3, 9).forEach((s) => setStep(s, "skip"));
    setStep("audit", trace.audit ? "done" : "skip", trace.audit ? "rejection audited" : "");
    return;
  }
  setStep("introspect", "done", `${a.latency_ms} ms total`);
  if (a.rpc_error) {
    setStep("bind", "fail", `rpc ${a.rpc_error.code}`);
    STEPS.slice(4, 9).forEach((s) => setStep(s, "skip"));
    setStep("audit", trace.audit ? "done" : "skip");
    return;
  }
  setStep("bind", "done", `${trace.persona.type} ${trace.persona.id}`);
  const rec = trace.audit?.record;
  const payload = a.payload || {};
  const vocabFailed = rec?.policy_fetch_failed || /schema|polic/i.test(payload.reason || "");
  setStep("vocab", vocabFailed ? "warn" : "done", rec ? `schema ${short(rec.schema_version)} · policies ${short(rec.policy_version)}` : "");
  if (a.outcome === "success") {
    setStep("extract", "done", rec ? `${rec.llm_model}` : "");
    setStep("validate", "done", rec ? `${rec.validation_retries} retr${rec.validation_retries === 1 ? "y" : "ies"}` : "");
  } else {
    setStep("extract", "warn", rec ? `${rec.llm_model}` : "");
    setStep("validate", "fail", rec?.outcome ?? "refused");
  }
  paintIbacSteps(trace.ibac);
  const ibacRecords = trace.ibac ? trace.ibac.records.length : 0;
  setStep(
    "audit",
    trace.audit ? "done" : "warn",
    trace.audit ? `${rec.outcome} · ${rec.latency_ms} ms${ibacRecords ? ` · +${ibacRecords} IBAC` : ""}` : "no record yet",
  );
}

// The two IBAC steps: discovery over the fleet, usage on what discovery matched.
function paintIbacSteps(ib) {
  if (!ib) {
    setStep("discover", "skip", "no intent");
    setStep("confirm", "skip");
    return;
  }
  const matched = ib.summary.matched_agents.length;
  setStep("discover", matched ? "done" : "fail", `${matched} of ${ib.summary.agents} agents`);
  if (!ib.usage.length) {
    setStep("confirm", "skip", "nothing to confirm");
    return;
  }
  const requests = ib.summary.requests;
  const searches = ib.usage.filter((u) => u.reason_code === "intent_match_search").length;
  const parts = [];
  if (requests) parts.push(`${requests} request${requests === 1 ? "" : "s"}`);
  if (searches) parts.push(`${searches} search match${searches === 1 ? "" : "es"}`);
  setStep("confirm", "done", `${ib.usage.length} skill${ib.usage.length === 1 ? "" : "s"} · ${parts.join(", ")}`);
}

const short = (v) => (typeof v === "string" && v.length > 10 ? v.slice(0, 8) : v || "—");

// ---------- result ----------

function renderResult(trace) {
  const wrap = $("#result");
  wrap.hidden = false;
  const a = trace.a2a;
  const verdict = $("#verdict");
  const meta = $("#resultMeta");
  $("#tickets").replaceChildren();
  $("#refusal").hidden = true;

  if (!a) {
    verdict.className = "result-verdict error";
    verdict.textContent = "The Token Service refused the exchange.";
    meta.textContent = pretty(trace.tokens.exchange?.error);
  } else if (a.outcome === "error") {
    verdict.className = "result-verdict error";
    verdict.textContent = a.transport_error
      ? "The agent could not be reached."
      : a.rpc_error
        ? `JSON-RPC error ${a.rpc_error.code}: ${a.rpc_error.message}`
        : `HTTP ${a.http_status}: ${(a.body_text || "").trim() || "refused before the prompt was read"}`;
  } else if (a.outcome === "refused") {
    verdict.className = "result-verdict refused";
    verdict.textContent = "No intent extracted - refused with an obligation.";
    const p = a.payload || {};
    const box = $("#refusal");
    box.hidden = false;
    box.replaceChildren(
      el("b", "", p.error || "Unable to extract intent from prompt."),
      el("div", "", p.reason || ""),
      (() => {
        const d = el("div", "obl");
        d.append(el("span", "", "Obligation · "), document.createTextNode(p.obligation || ""));
        return d;
      })(),
    );
  } else {
    const n = a.payload.evaluations.length;
    verdict.className = "result-verdict ok";
    verdict.textContent = `${n} evaluation${n === 1 ? "" : "s"} extracted.`;
    renderTickets(a.payload.evaluations, trace.decisions);
  }
  const bits = [];
  if (a) bits.push(`HTTP ${a.http_status ?? "—"}`, `${a.latency_ms} ms`, a.task_state ?? "");
  if (trace.audit?.record) bits.push(`audit ${trace.audit.record.outcome}`);
  if (trace.ibac) bits.push(`IBAC ${trace.ibac.summary.matched_agents.length}/${trace.ibac.summary.agents} agents`);
  bits.push(`req ${trace.request_id.slice(0, 8)}`);
  meta.textContent = bits.filter(Boolean).join("  ·  ");
  renderActors(trace);
  renderChain(trace);
  // The second part exists only once the Intent Agent answered; a refused exchange or an unreachable agent ends at part 1.
  $("#zoneIbac").hidden = !a || a.outcome === "error";
  renderHandoff(trace);
  renderIbac(trace);
  renderWire();
}

// ---------- handoff lane: Intent Agent out → IBAC in → IBAC out → Agent Gateway ----------

function stage(kind, who, title, rows, status) {
  const card = el("div", `stage stage-${kind} ${status || ""}`);
  const head = el("div", "stage-head");
  head.append(el("span", "stage-who", who), el("b", "", title));
  card.append(head);
  const list = el("div", "stage-rows");
  for (const [k, v, cls] of rows) {
    const row = el("div", `stage-row ${cls || ""}`);
    row.append(el("span", "k", k), el("span", "v", v));
    list.append(row);
  }
  card.append(list);
  return card;
}

function arrow(label, status) {
  const a = el("div", `stage-arrow ${status || ""}`);
  a.append(el("span", "stage-arrow-line"), el("small", "", label));
  return a;
}

function renderHandoff(trace) {
  const box = $("#handoff");
  const a = trace.a2a;
  if (!a || a.outcome === "error") {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.replaceChildren();
  const head = el("div", "handoff-head");
  head.append(
    el("h2", "", "From prompt to agent: the handoffs"),
    el("p", "", "What leaves the Intent Agent, what the IBAC engine takes in and gives back, and what an Agent Gateway in front of an agent would do with it. The values are this prompt's."),
  );
  box.append(head);
  const lane = el("div", "lane");
  const rec = trace.audit?.record;
  const payload = a.payload || {};
  const ib = trace.ibac;
  const n = (payload.evaluations || []).length;

  // 1. Intent Agent: its last word is the artifact of the task.
  if (a.outcome === "success") {
    lane.append(stage("agent", "Intent Agent · out", `intent with ${n} evaluation${n === 1 ? "" : "s"}`, [
      ["artifact", "{ prompt, evaluations[] } in the task's first part"],
      ["task", a.task_state || "—"],
      ["bound subject", `${trace.persona.type} ${trace.persona.id}, from message.metadata`],
      ["vocabulary", rec ? `schema ${short(rec.schema_version)} · policies ${short(rec.policy_version)}` : "schema + policies of the project"],
      ["audit", rec ? `${rec.outcome} · ${rec.latency_ms} ms` : "record pending"],
    ], "ok"));
    lane.append(arrow("the intent, as JSON", "ok"));
  } else {
    lane.append(stage("agent", "Intent Agent · out", "no intent: task failed", [
      ["artifact", "{ prompt, error, reason, obligation }"],
      ["error", payload.error || "Unable to extract intent from prompt."],
      ["obligation", payload.obligation || "—"],
      ["audit", rec ? `${rec.outcome}` : "record pending"],
    ], "fail"));
    lane.append(arrow("nothing to hand over", "skip"));
  }

  // 2. IBAC engine, in.
  if (ib) {
    const params = ib.params || {};
    const named = [...new Set((params.evaluations || []).flatMap((e) => Object.keys(e.parameters || {})))];
    lane.append(stage("ibac", "IBAC engine · in", "three inputs", [
      ["intent", `${n} evaluation${n === 1 ? "" : "s"}: subject type, action, resource type each`],
      ["cards", `${ib.summary.agents} agent cards, ${ib.discovery.reduce((s, d) => s + d.skill_ids.length, 0)} skills with their x-coaz-mappings`],
      ["params", `token.sub = ${params.token?.sub || "—"}${named.length ? ` · parameters: ${named.join(", ")}` : " · no parameters in the intent"}`],
      ["never", "the prompt text, the token signature, the graph", "muted"],
    ], "ok"));
    lane.append(arrow("discovery, then usage", "ok"));
    // 3. IBAC engine, out.
    const matched = ib.summary.matched_agents;
    const searches = ib.usage.filter((u) => u.reason_code === "intent_match_search").length;
    const generated = ib.usage.flatMap((u) => u.plans || []);
    const allow = generated.filter((p) => p.result?.decision).length;
    const deny = generated.filter((p) => p.result && !p.result.decision).length;
    lane.append(stage("ibac", "IBAC engine · out", matched.length ? `${matched.length} of ${ib.summary.agents} cards may serve it` : "no card may serve it", [
      ["discovery", matched.length ? `AUTHORIZED: ${matched.join(", ")}` : "NOT_AUTHORIZED on every card"],
      ["usage", ib.usage.length ? ib.usage.map((u) => `${u.skill} → ${u.reason_code}`).join(" · ") : "not run: no skill matched"],
      ["generated", ib.summary.requests ? `${ib.summary.requests} AuthZEN request${ib.summary.requests === 1 ? "" : "s"}${generated.length ? ` · ${allow} ALLOW, ${deny} DENY` : ""}` : searches ? `none: ${searches} search match${searches === 1 ? "" : "es"}, the intent's own evaluation decides` : "none"],
      ["audit", `${ib.records.length} records: ${ib.summary.agents} IBAC_DISCOVERY, ${ib.usage.length} IBAC_USAGE`],
    ], matched.length ? "ok" : "fail"));
    lane.append(arrow(matched.length ? "decision + requests" : "refusal", matched.length ? "ok" : "fail"));
  } else {
    lane.append(stage("ibac", "IBAC engine", "not asked", [["why", "a prompt without an intent never reaches the engine", "muted"]], "skip"));
    lane.append(arrow("", "skip"));
    lane.append(stage("ibac", "IBAC engine · out", "—", [["decision", "none", "muted"]], "skip"));
    lane.append(arrow("", "skip"));
  }

  // 4. Agent Gateway: what it would do with the message.
  let gwTitle;
  let gwRows;
  let gwStatus;
  if (!ib) {
    gwTitle = "answers the caller itself";
    gwStatus = "fail";
    gwRows = [
      ["reply", "an A2A message with the Intent Agent's error and obligation, the error object under metadata.intent_error"],
      ["forwarded", "nothing: no agent is called", "muted"],
      ["audit", "INTENT record: INTENT_FAILED"],
    ];
  } else if (!ib.summary.matched_agents.length) {
    gwTitle = "refuses the message";
    gwStatus = "fail";
    gwRows = [
      ["reason", "the agent behind this gateway declares no skill for the intent (intent_mismatch)"],
      ["forwarded", "nothing: the caller is told, no agent is called", "muted"],
      ["audit", "INTENT_FOUND, then IBAC_DISCOVERY NOT_AUTHORIZED"],
    ];
  } else {
    const first = ib.usage[0];
    const agentName = ib.discovery.find((d) => d.agent === first.agent)?.capability || first.agent;
    const generated = ib.usage.flatMap((u) => u.plans || []);
    const deny = generated.filter((p) => p.result && !p.result.decision).length;
    const needsAuthzen = ib.usage.some((u) => u.reason_code === "intent_match_authzen_required");
    gwTitle = `forwards to ${agentName}`;
    gwStatus = deny ? "warn" : "ok";
    gwRows = [
      ["skill", `${first.skill}${ib.usage.length > 1 ? ` (+${ib.usage.length - 1} other matched skill${ib.usage.length === 2 ? "" : "s"})` : ""}`],
      ["condition", needsAuthzen ? `the generated request${ib.summary.requests === 1 ? "" : "s"} must be allowed by AuthZEN${generated.length ? deny ? ` · ${deny} DENY here: the message would not go through` : " · allowed here" : ""}` : "search match: the agent is reached, what it may return is decided by the intent's own evaluation"],
      ["carries", "Authorization: the caller's token · X-IK-Token: the delegated token (the Token Service can write the intent into its act element)"],
      ["audit", "INTENT_FOUND · IBAC_DISCOVERY · IBAC_USAGE · AUTHORIZED"],
    ];
  }
  lane.append(stage("gateway", "Agent Gateway · in front of the agent", gwTitle, gwRows, gwStatus));
  box.append(lane);

  const foot = el("div", "handoff-foot");
  foot.append(
    el("span", "", "In production one gateway protects one agent and reads that agent's card only: it resolves the caller (whoami), asks the Intent Agent, runs the engine on its own card, checks admission with AuthZEN and forwards or answers. This console runs the engine over the whole fleet so the routing is visible in one place."),
  );
  box.append(foot);
}

// ---------- actor strip: who talked to whom for this prompt ----------

const ACTORS = [
  ["console", "console", "demo issuer · A2A client"],
  ["ts", "token-service", "IndyKite Token Service"],
  ["agent", "intent-agent", "extracts the intent"],
  ["ibac", "ibac-engine", "discovery · usage"],
  ["platform", "platform", "IndyKite AuthZEN · schema"],
  ["gemini", "gemini", "extraction model"],
];

function renderActors(trace) {
  const wrap = $("#actors");
  wrap.replaceChildren();
  const t = trace.tokens;
  const a = trace.a2a;
  const rec = trace.audit?.record;
  const mode = trace.auth_mode;
  const ms = (v) => (v == null ? "—" : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${v} ms`);

  const header = el("div", "actors-head");
  for (const [key, name, role] of ACTORS) {
    const chip = el("div", `actor actor-${key}`);
    chip.append(el("b", "", name), el("small", "", role));
    header.append(chip);
  }
  wrap.append(header);

  const hops = [];
  // 1. exchange
  if (t.exchange?.claims) hops.push(["console", "ts", "/oauth2/token · RFC 8693 exchange", `${ms(t.exchange.latency_ms)} · aud ${t.exchange.audience}`, mode === "wrong_audience" ? "warn" : "ok"]);
  else if (t.exchange?.error) hops.push(["console", "ts", "/oauth2/token · RFC 8693 exchange", `refused ${t.exchange.error.status ?? ""}`, "fail"]);
  else hops.push(["console", "ts", "/oauth2/token · RFC 8693 exchange", mode === "none" ? "skipped: no token" : "skipped: raw subject token sent instead", "skip"]);
  // 2. send message
  if (!a) hops.push(["console", "agent", "SendMessage (A2A JSON-RPC)", "not sent", "skip"]);
  else if (a.transport_error) hops.push(["console", "agent", "SendMessage (A2A JSON-RPC)", "unreachable", "fail"]);
  else hops.push(["console", "agent", "SendMessage (A2A JSON-RPC)", `HTTP ${a.http_status} · ${ms(a.latency_ms)}`, a.http_status < 400 ? "ok" : "fail"]);
  // 3. introspection
  const intro = t.token_audit.find((r) => r.action === "TOKEN_INTROSPECT");
  if (mode === "none") hops.push(["agent", "ts", "/oauth2/introspect · RFC 7662", "skipped: nothing to introspect", "skip"]);
  else if (intro) hops.push(["agent", "ts", "/oauth2/introspect · RFC 7662", `${intro.decision} · ${intro.subject || "no subject"}`, intro.decision === "INTROSPECTED" ? (mode === "wrong_audience" ? "warn" : "ok") : "fail"]);
  else if (a && a.http_status === 401) hops.push(["agent", "ts", "/oauth2/introspect · RFC 7662", "token rejected", "fail"]);
  else hops.push(["agent", "ts", "/oauth2/introspect · RFC 7662", a && a.http_status < 400 ? "accepted (record pending)" : "—", a && a.http_status < 400 ? "ok" : "skip"]);
  // 4. vocabulary
  const reached = a && !a.transport_error && a.http_status < 400 && !a.rpc_error;
  if (!reached) hops.push(["agent", "platform", "data schema + caller's policies", "not reached", "skip"]);
  else if (rec) hops.push(["agent", "platform", "data schema + caller's policies", `schema ${short(rec.schema_version)} · policies ${short(rec.policy_version)}${rec.policy_fetch_failed ? " · stale" : ""}`, rec.policy_fetch_failed ? "warn" : "ok"]);
  else hops.push(["agent", "platform", "data schema + caller's policies", /schema|polic/i.test(a.payload?.reason || "") ? a.payload.reason : "cached", /schema|polic/i.test(a.payload?.reason || "") ? "fail" : "ok"]);
  // 5. extraction
  if (!reached) hops.push(["agent", "gemini", "structured extraction", "not reached", "skip"]);
  else hops.push(["agent", "gemini", "structured extraction", `${rec?.llm_model || state.config.gemini_model || "gemini"}${rec ? ` · ${rec.validation_retries} retr${rec.validation_retries === 1 ? "y" : "ies"}` : ""}`, a.outcome === "success" ? "ok" : "warn"]);
  // 6. audit
  hops.push(["agent", "console", "audit record (webhook)", rec ? `${rec.outcome} · ${ms(rec.latency_ms)} inside the agent` : a ? "not received yet" : "—", rec ? "ok" : "warn"]);
  // 7-9. IBAC: discovery over the fleet, usage on the matched skills, the generated requests decided
  const ib = trace.ibac;
  if (!ib) {
    hops.push(["console", "ibac", "discovery over the fleet", "no intent to match", "skip"]);
  } else {
    const matched = ib.summary.matched_agents.length;
    hops.push(["console", "ibac", "discovery over the fleet", `${ib.summary.agents} cards · ${matched} matched${matched ? `: ${ib.summary.matched_agents.join(", ")}` : ""}`, matched ? "ok" : "fail"]);
    if (ib.usage.length) {
      const codes = [...new Set(ib.usage.map((u) => u.reason_code))].join(", ");
      hops.push(["console", "ibac", "usage on the matched skills", `${ib.usage.map((u) => u.skill).join(", ")} · ${codes}`, "ok"]);
    } else {
      hops.push(["console", "ibac", "usage on the matched skills", "no skill matched", "skip"]);
    }
    const generated = ib.usage.flatMap((u) => u.plans || []);
    if (generated.length) {
      const allow = generated.filter((p) => p.result?.decision).length;
      const deny = generated.filter((p) => p.result && !p.result.decision).length;
      const errors = generated.filter((p) => p.error).length;
      hops.push(["ibac", "platform", "AuthZEN for the generated requests", `${generated.length} request${generated.length === 1 ? "" : "s"} · ${allow} ALLOW, ${deny} DENY${errors ? `, ${errors} error` : ""}`, errors ? "warn" : "ok"]);
    } else {
      hops.push(["ibac", "platform", "AuthZEN for the generated requests", ib.summary.requests ? "decision step off" : "nothing generated (search matches decide from the intent)", "skip"]);
    }
  }
  // 10. decisions
  const plans = trace.decisions?.plans || [];
  if (plans.length) {
    const allow = plans.filter((p) => p.kind === "evaluation" && p.result?.decision).length;
    const deny = plans.filter((p) => p.kind === "evaluation" && p.result && !p.result.decision).length;
    const searches = plans.filter((p) => p.kind.startsWith("search_")).length;
    const errors = plans.filter((p) => p.error).length;
    const parts = [];
    if (allow) parts.push(`${allow} ALLOW`);
    if (deny) parts.push(`${deny} DENY`);
    if (searches) parts.push(`${searches} search${searches === 1 ? "" : "es"}`);
    if (errors) parts.push(`${errors} error${errors === 1 ? "" : "s"}`);
    hops.push(["console", "platform", "AuthZEN evaluations / search", `${plans.length} call${plans.length === 1 ? "" : "s"} · ${parts.join(", ")}`, errors ? "warn" : "ok"]);
  } else {
    hops.push(["console", "platform", "AuthZEN evaluations / search", trace.decisions?.error || "nothing to decide", trace.decisions?.error ? "fail" : "skip"]);
  }

  const list = el("div", "hops");
  hops.forEach(([from, to, what, detail, status], i) => {
    const row = el("div", `hop ${status}`);
    row.append(
      el("span", "hop-n", String(i + 1)),
      el("span", `hop-actor actor-${from}`, ACTORS.find((x) => x[0] === from)[1]),
      el("span", "hop-arrow", "→"),
      el("span", `hop-actor actor-${to}`, ACTORS.find((x) => x[0] === to)[1]),
      el("span", "hop-what", what),
      el("span", "hop-detail", detail),
    );
    list.append(row);
  });
  wrap.append(list);
}

// ---------- token chain ----------

const shortId = (v) => (typeof v === "string" && v.length > 14 ? `${v.slice(0, 8)}…` : v || "—");
const ttl = (c) => (c && c.exp && c.iat ? `${c.exp - c.iat} s` : "—");
function audList(aud) {
  return Array.isArray(aud) ? aud.join(", ") : aud || "—";
}

function actChain(act) {
  const parts = [];
  for (let a = act; a && typeof a === "object"; a = a.act) parts.push(`${a.sub}${a.type ? ` (${a.type})` : ""}`);
  return parts.join(" ← ") || "—";
}

function tokenCard(title, kind, status, rows) {
  const card = el("div", `tok ${status}`);
  const head = el("div", "tok-head");
  head.append(el("b", "", title), el("span", "tok-kind", kind));
  card.append(head);
  for (const [k, v] of rows) {
    const row = el("div", "tok-row");
    row.append(el("span", "k", k), el("span", "v", v));
    card.append(row);
  }
  return card;
}

function renderChain(trace) {
  const wrap = $("#chain");
  wrap.replaceChildren();
  const t = trace.tokens;
  const mode = trace.auth_mode;
  const subj = t.subject;
  const actor = t.actor;
  wrap.append(
    tokenCard("subject token", "demo issuer", "ok", [
      ["iss", subj.iss],
      ["sub", subj.sub],
      ["aud", audList(subj.aud)],
      ["ttl", ttl(subj)],
    ]),
    el("div", "tok-arrow", mode === "raw_subject" || mode === "none" ? "·" : "→"),
    tokenCard("actor token", "demo issuer", mode === "raw_subject" || mode === "none" ? "dim" : "ok", [
      ["sub", actor.sub],
      ["aud", audList(actor.aud)],
      ["ttl", ttl(actor)],
    ]),
    el("div", "tok-arrow", "→"),
  );
  const ex = t.exchange;
  if (ex && ex.claims) {
    const c = ex.claims;
    wrap.append(
      tokenCard("delegation token", "Token Service · RFC 8693", mode === "wrong_audience" ? "warn" : "ok", [
        ["iss", c.iss],
        ["sub", c.sub],
        ["act", actChain(c.act)],
        ["aud", audList(c.aud)],
        ["ttl", ex.expires_in != null ? `${ex.expires_in} s` : ttl(c)],
        ["jti", shortId(c.jti)],
      ]),
    );
  } else if (ex && ex.error) {
    wrap.append(tokenCard("delegation token", "Token Service", "fail", [["refused", `${ex.error.status ?? "no answer"}`], ["error", typeof ex.error.body === "object" ? ex.error.body.error || "" : String(ex.error.body)]]));
  } else {
    wrap.append(
      tokenCard("delegation token", "Token Service", "dim", [["status", mode === "none" ? "no token sent" : "not exchanged"], ["bearer", t.bearer_kind || "—"]]),
    );
  }
  wrap.append(el("div", "tok-arrow", "→"));
  const audit = tokenCard("Token Service audit", "what it recorded", t.token_audit.length ? "ok" : "dim", []);
  if (!t.token_audit.length) audit.append(el("div", "tok-row", mode === "none" ? "nothing to record" : "no record yet"));
  for (const r of t.token_audit) {
    const row = el("div", "tok-row tok-audit");
    const good = ["TOKEN_EXCHANGED", "INTROSPECTED"].includes(r.decision);
    row.append(el("span", `k ${good ? "good" : "bad"}`, r.decision), el("span", "v", `${r.action || ""} · ${r.subject || "—"} via ${(r.actorsChain || []).join(" ← ") || r.actor || "—"}`));
    audit.append(row);
  }
  wrap.append(audit);
}

// ---------- agents ----------

async function renderAgents() {
  const [card, its] = await Promise.all([
    fetch("/api/agent-card").then((r) => (r.ok ? r.json() : r.json().then((e) => ({ error: e.detail })))),
    fetch("/api/token-service").then((r) => (r.ok ? r.json() : r.json().then((e) => ({ error: e.detail })))),
  ]);
  const cfg = state.config;

  const a = $("#agentCardPanel");
  a.replaceChildren(headline("Intent Agent", "agent card · /.well-known/agent-card.json"));
  if (card.error) a.append(el("div", "empty", String(card.error)));
  else {
    a.append(el("p", "agent-desc", card.description || ""));
    const iface = (card.supportedInterfaces || [])[0] || {};
    a.append(
      kv([
        ["name", card.name],
        ["version", card.version],
        ["provider", card.provider?.organization],
        ["interface", `${iface.url || "—"} · ${iface.protocolBinding || ""} ${iface.protocolVersion || ""}`],
        ["capabilities", Object.entries(card.capabilities || {}).filter(([, v]) => v).map(([k]) => k).join(", ") || "none (SendMessage only)"],
        ["security", Object.values(card.securitySchemes || {}).map((s) => Object.values(s)[0]?.description || "").join(" ") || "—"],
      ]),
    );
    for (const s of card.skills || []) {
      const sk = el("div", "skill");
      const h = el("div", "skill-head");
      h.append(el("b", "", s.name), el("span", "mono muted", s.id));
      (s.tags || []).forEach((tg) => h.append(tag(tg)));
      sk.append(h, el("p", "", s.description || ""));
      if (s.examples?.length) {
        const ex = el("ul", "skill-ex");
        s.examples.forEach((x) => ex.append(el("li", "", x)));
        sk.append(ex);
      }
      a.append(sk);
    }
  }

  const tsp = $("#tokenServicePanel");
  tsp.replaceChildren(headline("Token Service", "discovery · /.well-known/openid-configuration"));
  if (its.error) tsp.append(el("div", "empty", String(its.error)));
  else {
    const c = its.configuration;
    tsp.append(
      kv([
        ["issuer", c.issuer],
        ["exchange", its.exchange_url],
        ["introspect", its.introspect_url],
        ["grant types", (c.grant_types_supported || []).join(", ")],
        ["client auth", (c.token_endpoint_auth_methods_supported || []).join(", ")],
        ["client id", its.client_id],
        ["signing keys", its.keys.map((k) => `${k.kid} (${k.alg})`).join(", ") || "—"],
      ]),
    );
  }

  const cp = $("#consolePanel");
  cp.replaceChildren(headline("Console", "the actor in every delegation"));
  cp.append(
    kv([
      ["actor sub", cfg.console_client_id],
      ["issuer", cfg.demo_idp_issuer],
      ["subject aud", cfg.demo_idp_subject_audience],
      ["requests aud", cfg.intent_agent_audience],
      ["jwks", `${location.origin}/.well-known/jwks.json`],
    ]),
  );
}

function headline(title, sub) {
  const h = el("h2", "", title + " ");
  h.append(el("span", "muted", `→ ${sub}`));
  return h;
}

function kv(rows) {
  const box = el("div", "kv");
  for (const [k, v] of rows) {
    const row = el("div", "kv-row");
    row.append(el("span", "k", k), el("span", "v", v || "—"));
    box.append(row);
  }
  return box;
}

function renderTickets(evaluations, decisions) {
  const tpl = $("#ticketTpl");
  const wrap = $("#tickets");
  const note = el("p", "decide-note");
  note.append(
    el("b", "", "Left: what the Intent Agent returned. Right: what the platform answered. "),
    document.createTextNode(
      "The agent only produces the AuthZEN evaluation or search request; it never executes it. The console sends each one to the platform's AuthZEN API and shows the live decision or search result here.",
    ),
  );
  wrap.append(note);
  evaluations.forEach((ev, i) => {
    const node = tpl.content.firstElementChild.cloneNode(true);
    node.querySelector(".ticket-index").textContent = String(i + 1).padStart(2, "0");
    const s = ev.subject || {};
    const r = ev.resource || {};
    const act = ev.action?.name;
    node.querySelector(".subject .val").textContent = s.id ? `${s.type} · ${s.id}` : `${s.type} · any`;
    node.querySelector(".action .val").textContent = act || "any";
    node.querySelector(".resource .val").textContent = r.id ? `${r.type} · ${r.id}` : `${r.type} · any`;

    const tags = node.querySelector(".ticket-tags");
    const ctx = ev.context || {};
    const caller = state.trace.persona;
    if (!s.id) tags.append(tag("subject search", "cool"));
    else if (s.id !== caller.id || s.type !== caller.type) tags.append(tag("second subject", "hot"));
    if (!act) tags.append(tag("action search", "cool"));
    if (!r.id) tags.append(tag("resource search", "cool"));
    if (r.id === caller.id && r.type === caller.type) tags.append(tag("resource = caller", "leaf"));
    if (i === 0 && evaluations.length > 1 && evaluations.some((e) => e.subject?.id && e.subject.id !== caller.id)) tags.append(tag("gate", "hot"));
    if (ctx.parameters?.policy_fetch_failed) tags.append(tag("stale policies", "hot"));

    const c = node.querySelector(".ticket-context");
    if (ctx.filters) c.append(ctxRow("filters (CEL)", ctx.filters));
    const params = Object.entries(ctx.parameters || {}).filter(([k]) => k !== "policy_fetch_failed");
    if (params.length) c.append(ctxRow("parameters", params.map(([k, v]) => `${k} = ${JSON.stringify(v)}`).join("   ")));
    if (ctx.qualifiers && Object.keys(ctx.qualifiers).length) c.append(ctxRow("qualifiers", Object.entries(ctx.qualifiers).map(([k, v]) => `${k} = ${JSON.stringify(v)}`).join("   ")));

    node.querySelector(".ticket-decision").append(decisionBox(decisions?.plans?.[i], decisions?.error));
    wrap.append(node);
  });
}

function tag(text, kind) {
  return el("span", `tag ${kind || ""}`, text);
}
function ctxRow(k, v) {
  const row = el("div", "ctx");
  row.append(el("span", "k", k), el("span", "v", v));
  return row;
}

function decisionBox(plan, error) {
  const box = el("div", "decision");
  if (error) {
    box.classList.add("na");
    box.append(el("b", "", "no decision"), el("small", "", error));
    return box;
  }
  if (!plan) {
    box.classList.add("pending");
    box.append(el("b", "", "—"), el("small", "", "decision step off"));
    return box;
  }
  if (plan.skipped) {
    box.classList.add("na");
    box.append(el("b", "", "not sent"), el("small", "", plan.skipped));
  } else if (plan.error) {
    box.classList.add("na");
    box.append(el("b", "", "platform error"), el("small", "", plan.error));
  } else if (plan.kind === "evaluation") {
    const d = plan.result?.decision;
    box.classList.add(d ? "allow" : "deny");
    box.append(el("b", "", d ? "ALLOW" : "DENY"), el("small", "", "/access/v1/evaluations"));
  } else if (plan.kind.startsWith("search_")) {
    const results = plan.result?.results || [];
    box.classList.add("search");
    box.append(el("b", "", `${results.length} result${results.length === 1 ? "" : "s"}`));
    if (results.length) {
      const chips = el("div", "results");
      results.forEach((x) => chips.append(el("span", "", x.id || x.name)));
      box.append(chips);
    }
    box.append(el("small", "", plan.endpoint));
  } else {
    box.classList.add("na");
    box.append(el("b", "", "not decidable"), el("small", "", plan.notes.join(" ")));
  }
  const carried = Object.keys(plan.carried || {});
  if (carried.length) box.append(el("small", "", `carried, not enforced: ${carried.join(", ")}`));
  if (Object.keys(plan.input_params || {}).length) box.append(el("small", "", `input_params: ${Object.keys(plan.input_params).join(", ")}`));
  return box;
}

function renderWire() {
  const t = state.trace;
  if (!t) return;
  const a = t.a2a || {};
  const tabs = {
    request: a.request ? `POST ${a.url}\n${Object.entries(a.request_headers || {}).map(([k, v]) => `${k}: ${v}`).join("\n")}\n\n${pretty(a.request)}` : "no request was sent",
    envelope: a.envelope ? pretty(a.envelope) : a.body_text || "—",
    payload: a.payload ? pretty(a.payload) : "no artifact (the request was refused before extraction)",
    ibac: t.ibac ? pretty(t.ibac) : "no intent reached the IBAC engine",
    tokens: pretty({
      bearer: t.tokens.bearer_kind,
      subject_token_claims: t.tokens.subject,
      actor_token_claims: t.tokens.actor,
      exchange: t.tokens.exchange,
    }),
    audit: t.audit ? pretty(t.audit.record) : "no record arrived within 5 s (check the Audit view later)",
    decisions: t.decisions ? pretty(t.decisions) : "no evaluations to decide on",
  };
  $("#wireBody").textContent = tabs[state.wireTab];
}

// ---------- vocabulary ----------

async function renderVocabulary() {
  const type = state.vocabSubject;
  $("#vocabInfo").textContent = "loading…";
  try {
    const [schema, pol] = await Promise.all([
      state.schema ? Promise.resolve(state.schema) : fetch("/api/schema").then((r) => (r.ok ? r.json() : Promise.reject(r))),
      state.policies[type] ? Promise.resolve(state.policies[type]) : fetch(`/api/policies?subject_type=${type}`).then((r) => (r.ok ? r.json() : Promise.reject(r))),
    ]);
    state.schema = schema;
    state.policies[type] = pol;
  } catch (err) {
    if (type !== state.vocabSubject) return;
    $("#vocabInfo").textContent = `platform unavailable (${err.status || err})`;
    return;
  }
  if (type !== state.vocabSubject) return; // the selection moved on while this was loading
  const pol = state.policies[type];
  $("#vocabInfo").textContent = `${pol.policies.length} active polic${pol.policies.length === 1 ? "y" : "ies"} for ${type} · ${state.schema.nodes.length} node types · ${state.schema.relationships.length} relationship kinds`;

  const list = $("#policyList");
  list.replaceChildren();
  for (const p of pol.policies) {
    const row = el("div", "policy");
    const head = el("div", "head");
    head.append(el("span", "mono muted", p.subject_type), el("span", "arrow", "→"));
    p.actions.forEach((a) => head.append(el("span", "act", a)));
    head.append(el("span", "arrow", "→"), el("span", "res", p.resource_type));
    p.params.forEach((q) => head.append(tag(`$${q}`, "hot")));
    if (p.version) head.append(tag(p.version));
    row.append(head, el("div", "cypher", p.cypher));
    list.append(row);
  }
  if (!pol.policies.length) list.append(el("div", "empty", `No ACTIVE policy for ${type}: the agent would refuse before calling the model.`));

  const nodes = $("#schemaNodes");
  nodes.replaceChildren();
  for (const n of state.schema.nodes) {
    const chip = el("div", `node-chip${n.identity ? " identity" : ""}`);
    chip.append(el("div", "nt", n.type), el("div", "nc", `${n.count} node${n.count === 1 ? "" : "s"}${n.identity ? " · identity" : ""}`));
    if (n.properties.length) chip.append(el("div", "np", n.properties.map((p) => `${p.name}:${(p.types[0] || "?").toUpperCase()}`).join("  ")));
    nodes.append(chip);
  }
  const relationships = $("#schemaRelationships");
  relationships.replaceChildren();
  for (const r of state.schema.relationships) {
    const row = el("div");
    row.append(document.createTextNode(`${r.source} `), el("span", "rel", r.relation), document.createTextNode(` ${r.target}`));
    relationships.append(row);
  }
}

// ---------- audit ----------

async function loadAuditRecent() {
  try {
    const data = await fetch("/api/audit/recent?limit=200").then((r) => r.json());
    state.auditEntries = data.entries;
    updateAuditCount();
    if (state.view === "audit") renderAudit();
  } catch {
    /* the stream will fill it in */
  }
}

function openAuditStream() {
  const es = new EventSource("/api/audit/stream");
  es.addEventListener("audit", (e) => {
    const entry = JSON.parse(e.data);
    state.auditEntries.push(entry);
    if (state.auditEntries.length > 500) state.auditEntries.shift();
    updateAuditCount();
    if (state.view === "audit") prependAudit(entry);
  });
  es.onerror = () => {
    /* EventSource reconnects on its own */
  };
}

function updateAuditCount() {
  $("#auditCount").textContent = state.auditEntries.length ? String(state.auditEntries.length) : "";
}

function renderAudit() {
  const list = $("#auditList");
  list.replaceChildren();
  if (!state.auditEntries.length) {
    list.append(el("div", "empty", "No records yet. Send a prompt: the Token Service audits the exchange, the Intent Agent audits the request."));
    return;
  }
  [...state.auditEntries].reverse().forEach((entry) => list.append(auditNode(entry)));
}

function prependAudit(entry) {
  const list = $("#auditList");
  const empty = list.querySelector(".empty");
  if (empty) empty.remove();
  list.prepend(auditNode(entry));
  while (list.children.length > 500) list.lastElementChild.remove();
}

function auditNode(entry) {
  const rec = entry.record;
  const d = el("details", "audit-entry");
  const s = el("summary");
  const outcome = rec.outcome || rec.decision || "";
  let msg;
  if (entry.source === "intent-agent") msg = `${rec.subject_type || ""} ${rec.subject_id || ""} — ${rec.prompt || rec.message || ""}`;
  else if (entry.source === "ibac-engine") {
    const what = rec.action === "IBAC_USAGE"
      ? `${rec.skillID || "no single skill"} → ${rec.reasonCode}`
      : (rec.evaluations || []).map((e) => `${e.action || "any"} ${e.resourceType} → ${e.matchedSkills.length ? e.matchedSkills.join("+") : "∅"}`).join(" · ");
    msg = `${rec.capabilityName} · ${rec.action === "IBAC_USAGE" ? "usage" : "discovery"} — ${what || rec.reason || ""}`;
  } else msg = `${rec.subject || ""} via ${rec.actor || ""} — ${rec.reason || ""}`;
  s.append(
    el("span", `src ${entry.source}`, entry.source),
    el("span", `out ${outcome}`, outcome || "—"),
    el("span", "msg", msg),
    el("span", "ts", (rec.timestamp || entry.received_at || "").replace("T", " ").slice(0, 23)),
  );
  d.append(s, el("pre", "", pretty(rec)));
  return d;
}

// ---------- IBAC: the intent against the fleet ----------

const REASON_TEXT = {
  intent_match_authzen_required: "matched - the mapping's ids resolved, these requests go to the platform",
  intent_match_search: "matched as a search - an id stays empty, so nothing is generated; the intent's own evaluation decides",
  intent_mismatch: "no mapping of this skill agrees with any evaluation on subject type, resource type and action",
  missing_coaz_mapping: "the card publishes no x-coaz-mappings - no intent can ever match it",
  invalid_intent: "the intent itself is not valid",
  invalid_capability: "the card is not valid",
  incorrect_num_skills: "usage needs exactly one skill",
};

const pick = (agent, skill) => `${agent}/${skill}`;

async function ensureFleet() {
  if (state.fleet) return state.fleet;
  try {
    state.fleet = (await fetch("/api/fleet").then((r) => (r.ok ? r.json() : Promise.reject(r)))).agents;
  } catch {
    state.fleet = null;
  }
  return state.fleet;
}

const skillMappings = (agentId, skillId) =>
  (state.fleet || []).find((a) => a.id === agentId)?.capability.skills.find((s) => s.id === skillId)?.mappings || [];

// One plain-English line per card: what made it match, or the nearest reason it did not.
function verdictFor(d, evaluations) {
  if (d.reason_code === "missing_coaz_mapping") return "publishes no mappings, so no intent can ever match it";
  if (d.decision) {
    const ev = d.evaluations.find((e) => e.matched_skills.length);
    const e = evaluations[ev.index];
    return `declares ${e.action?.name || "any action"} on ${e.resource?.type} for a ${e.subject?.type}, in ${ev.matched_skills.join(" and ")}`;
  }
  const mappings = d.skill_ids.flatMap((s) => skillMappings(d.agent, s));
  if (!mappings.length) return "no mapping to compare with";
  const e = evaluations[0] || {};
  const subjectTypes = new Set(mappings.flatMap((m) => m.subject.map((x) => x.type)));
  const sameResource = mappings.filter((m) => m.resource.some((x) => x.type === e.resource?.type));
  if (!subjectTypes.has(e.subject?.type)) return `serves ${[...subjectTypes].join(" and ")} callers, not ${e.subject?.type}`;
  if (sameResource.length) {
    const actions = [...new Set(sameResource.flatMap((m) => m.action.map((a) => a.name)))];
    return `knows ${e.resource?.type} but only for ${actions.join(", ")}, not ${e.action?.name || "an unnamed action"}`;
  }
  return `has no skill for ${e.resource?.type}`;
}

// The three discovery questions, answered per evaluation and per mapping of one skill.
function whyFold(agentId, skillId, evaluations) {
  const mappings = skillMappings(agentId, skillId);
  const fold = el("details", "why");
  fold.append(el("summary", "", "why?"));
  if (!mappings.length) {
    fold.append(el("div", "why-row none", "this skill publishes no mappings"));
    return fold;
  }
  evaluations.forEach((e, i) => {
    mappings.forEach((m, j) => {
      const sOk = m.subject.some((x) => x.type === e.subject?.type);
      const rOk = m.resource.some((x) => x.type === e.resource?.type);
      const aOk = !m.action.length || m.action.some((a) => a.name === (e.action?.name || ""));
      const row = el("div", `why-row ${sOk && rOk && aOk ? "hit" : ""}`);
      row.append(
        el("span", "why-n", `ev ${String(i + 1).padStart(2, "0")} · map ${j + 1}`),
        el("span", `why-q ${sOk ? "ok" : "no"}`, `subject ${e.subject?.type} ${sOk ? "=" : "≠"} ${m.subject.map((x) => x.type).join("|")}`),
        el("span", `why-q ${rOk ? "ok" : "no"}`, `resource ${e.resource?.type} ${rOk ? "=" : "≠"} ${m.resource.map((x) => x.type).join("|")}`),
        el("span", `why-q ${aOk ? "ok" : "no"}`, `action ${e.action?.name || "any"} ${aOk ? "∈" : "∉"} {${m.action.map((a) => a.name).join(", ") || "any"}}`),
      );
      fold.append(row);
    });
  });
  return fold;
}

// The three discovery questions for one evaluation against one skill, taking the best mapping.
function fieldMatch(e, mappings) {
  let best = { s: false, r: false, a: false, score: -1, mapping: null };
  for (const m of mappings) {
    const s = m.subject.some((x) => x.type === e.subject?.type);
    const r = m.resource.some((x) => x.type === e.resource?.type);
    const a = !m.action.length || m.action.some((x) => x.name === (e.action?.name || ""));
    const score = s + r + a;
    if (score > best.score) best = { s, r, a, score, mapping: m };
  }
  return best;
}

// Skills × evaluations: one cell per pair, three squares for subject type, resource type and action.
function renderMatrix(ib, evaluations) {
  const wrap = $("#ibacMatrix");
  wrap.replaceChildren();
  const table = el("table", "matrix");
  const head = el("tr");
  head.append(el("th", "", "agent · skill"));
  evaluations.forEach((e, i) => {
    const th = el("th");
    th.append(el("b", "", String(i + 1).padStart(2, "0")), el("small", "", `${e.subject?.type} · ${e.action?.name || "any"} · ${e.resource?.type}`));
    head.append(th);
  });
  table.append(head);
  for (const d of ib.discovery) {
    for (const skillId of d.skill_ids) {
      const mappings = skillMappings(d.agent, skillId);
      const tr = el("tr", d.matched_skills.includes(skillId) ? "hit" : "");
      const name = el("td", "matrix-name");
      name.append(el("span", `dotc dotc-${d.accent}`), el("span", "", d.capability), el("span", "mono muted", ` ${skillId}`));
      tr.append(name);
      evaluations.forEach((e) => {
        const td = el("td");
        if (!mappings.length) {
          td.append(el("span", "cell none", "no mappings"));
        } else {
          const f = fieldMatch(e, mappings);
          const cell = el("span", `cell${f.score === 3 ? " all" : ""}`);
          cell.append(el("i", `sq ${f.s ? "ok" : "no"}`, "S"), el("i", `sq ${f.r ? "ok" : "no"}`, "R"), el("i", `sq ${f.a ? "ok" : "no"}`, "A"));
          cell.title = `subject type ${f.s ? "matches" : "differs"} · resource type ${f.r ? "matches" : "differs"} · action ${f.a ? "allowed" : "not listed"}`;
          td.append(cell);
        }
        tr.append(td);
      });
      table.append(tr);
    }
  }
  wrap.append(table);
  const legend = el("div", "matrix-legend");
  legend.append(
    el("span", "", "S subject type · R resource type · A action;"),
    el("i", "sq ok", "✓"), el("span", "", "agrees"),
    el("i", "sq no", "✗"), el("span", "", "differs; a cell is a match only when all three agree, on one mapping."),
  );
  wrap.append(legend);
}

async function renderIbac(trace) {
  const box = $("#ibac");
  const ib = trace.ibac;
  state.ibacWhatIf = new Map();
  if (!ib) {
    // Nothing refused ever reaches the engine: say so instead of hiding the section.
    const a = trace.a2a;
    if (!a || a.outcome === "error") {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    $("#ibacLede").textContent = "The IBAC engine was not asked: the Intent Agent produced no intent for this prompt, so there is nothing to compare with the fleet. A gateway would answer the caller here, before any agent is involved.";
    $("#ibacSummary").replaceChildren(stat("—", "no intent"));
    $("#ibacBoard").replaceChildren();
    $("#ibacBoard").hidden = true;
    $("#ibacMatrixWrap").hidden = true;
    $("#ibacUsage").replaceChildren();
    $("#ibacUsage").hidden = true;
    return;
  }
  await ensureFleet();
  if (state.trace !== trace) return;
  box.hidden = false;
  $("#ibacBoard").hidden = false;
  $("#ibacUsage").hidden = false;
  $("#ibacMatrixWrap").hidden = !state.fleet;
  const evaluations = trace.a2a.payload.evaluations;
  if (state.fleet) renderMatrix(ib, evaluations);
  const matched = ib.summary.matched_agents.length;
  const n = trace.a2a.payload.evaluations.length;
  $("#ibacLede").textContent = matched
    ? `The engine compared the ${n} evaluation${n === 1 ? "" : "s"} above with every skill of ${ib.summary.agents} agent cards. ${matched} agent${matched === 1 ? "" : "s"} declare${matched === 1 ? "s" : ""} a skill for it; usage then resolved each matched skill's ids. Click a skill for its usage.`
    : `The engine compared the ${n} evaluation${n === 1 ? "" : "s"} above with every skill of ${ib.summary.agents} agent cards and none declares one for it: a gateway in front of any of these agents would stop here. Click a skill to see why.`;
  const summary = $("#ibacSummary");
  summary.replaceChildren(
    stat(String(matched), `of ${ib.summary.agents} agents`, matched ? "ok" : "fail"),
    stat(String(ib.usage.length), "skills matched"),
    stat(String(ib.summary.requests), "requests generated"),
  );

  const board = $("#ibacBoard");
  board.replaceChildren();
  for (const d of ib.discovery) {
    const card = el("article", `fa fa-${d.accent} ${d.decision ? "matched" : d.reason_code === "missing_coaz_mapping" ? "nomap" : "unmatched"}`);
    const head = el("div", "fa-head");
    head.append(el("b", "", d.capability), el("span", `pill ${d.decision ? "yes" : "no"}`, d.decision ? "AUTHORIZED" : "NOT_AUTHORIZED"));
    card.append(head, el("div", "fa-reason mono", d.reason_code), el("div", "fa-verdict", verdictFor(d, evaluations)));
    const skills = el("div", "fa-skills");
    for (const skillId of d.skill_ids) {
      const hit = d.matched_skills.includes(skillId);
      const wrap = el("div", "fa-skill-wrap");
      const b = el("button", `fa-skill${hit ? " hit" : ""}`);
      b.append(el("span", "fa-skill-id", skillId));
      const evs = d.evaluations.filter((e) => e.matched_skills.includes(skillId)).map((e) => String(e.index + 1).padStart(2, "0"));
      if (evs.length) b.append(el("span", "fa-evs", evs.join(" ")));
      b.dataset.pick = pick(d.agent, skillId);
      b.addEventListener("click", () => openUsage(d.agent, skillId));
      wrap.append(b);
      if (state.fleet) wrap.append(whyFold(d.agent, skillId, evaluations));
      skills.append(wrap);
    }
    card.append(skills);
    board.append(card);
  }
  const first = ib.usage[0];
  if (first) openUsage(first.agent, first.skill);
  else {
    state.ibacPick = null;
    $("#ibacUsage").replaceChildren(el("div", "empty", "No skill matched: no usage ran. Click any skill above to run it anyway and read the reason code."));
  }
}

function stat(big, small, kind) {
  const s = el("div", `stat ${kind || ""}`);
  s.append(el("b", "", big), el("small", "", small));
  return s;
}

async function openUsage(agent, skill) {
  const trace = state.trace;
  if (!trace?.ibac) return;
  state.ibacPick = pick(agent, skill);
  document.querySelectorAll(".fa-skill").forEach((b) => b.classList.toggle("open", b.dataset.pick === state.ibacPick));
  let usage = trace.ibac.usage.find((u) => u.agent === agent && u.skill === skill);
  let whatIf = false;
  if (!usage) {
    whatIf = true;
    usage = state.ibacWhatIf.get(state.ibacPick);
    if (!usage) {
      $("#ibacUsage").replaceChildren(el("div", "empty", "running usage…"));
      try {
        const res = await fetch("/api/ibac/usage", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            persona: trace.persona.id,
            agent,
            skill,
            intent: trace.a2a.payload,
            token_claims: trace.tokens.exchange?.claims || {},
            decide: true,
          }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(JSON.stringify(data.detail || data));
        usage = data.usage;
        state.ibacWhatIf.set(state.ibacPick, usage);
      } catch (err) {
        $("#ibacUsage").replaceChildren(el("div", "empty", `usage failed: ${err}`));
        return;
      }
      if (state.ibacPick !== pick(agent, skill)) return;
    }
  }
  renderUsage(usage, trace.ibac.params, whatIf);
}

function renderUsage(u, params, whatIf) {
  const wrap = $("#ibacUsage");
  wrap.replaceChildren();
  const head = el("div", "usage-head");
  const title = el("h3", "");
  title.append(el("span", "muted", "usage · "), document.createTextNode(`${u.capability} / `), el("span", "mono", u.skill));
  head.append(title, el("span", `pill ${u.decision ? "yes" : "no"}`, u.decision ? "AUTHORIZED" : "NOT_AUTHORIZED"), el("span", "mono reason", u.reason_code));
  if (whatIf) head.append(el("span", "tag", "what if: discovery did not pick this skill"));
  wrap.append(head, el("p", "usage-why", REASON_TEXT[u.reason_code] || u.reason || ""));
  wrap.append(mappingComparison(u, params));

  if (u.requests.length) {
    const list = el("div", "reqs");
    u.requests.forEach((r, i) => {
      const row = el("div", "req");
      const triple = el("div", "triple");
      triple.append(
        part("subject", `${r.subject.type} · ${r.subject.id}`, "subject"),
        el("div", "arrow", "→"),
        part("action", r.action?.name || "any", "action"),
        el("div", "arrow", "→"),
        part("resource", `${r.resource.type} · ${r.resource.id}`, "resource"),
      );
      const body = el("div", "req-body");
      body.append(el("div", "req-n", `generated request ${String(i + 1).padStart(2, "0")}`), triple);
      body.append(el("div", "req-note mono", `context.input_params = params (${Object.keys(r.context?.input_params || {}).join(", ")})`));
      // The engine takes the subject from the mapping ($token.sub), not from the evaluation: an
      // evaluation the Intent Agent emitted as a second subject comes back bound to the caller.
      const named = (state.trace?.a2a?.payload?.evaluations || []).find(
        (e) => e.action?.name === r.action?.name && e.resource?.type === r.resource.type && e.subject?.id && e.subject.id !== r.subject.id,
      );
      if (named) body.append(el("span", "req-rebound", `the intent's evaluation names ${named.subject.type} ${named.subject.id}; the mapping binds $token.sub = ${r.subject.id}`));
      row.append(body, decisionBox(u.plans?.[i], u.plans ? null : "decision step off"));
      list.append(row);
    });
    wrap.append(list);
  }

  const p = el("div", "params");
  p.append(el("h4", "", "what the $references resolved from"));
  const rows = [
    ["$token.sub", params.token?.sub],
    ["$token.aud", audList(params.token?.aud)],
    ["$token.act.sub", params.token?.act?.sub],
    ["$subject", `${params.subject?.type} · ${params.subject?.id}`],
  ];
  let anyNamed = false;
  (params.evaluations || []).forEach((e, i) => {
    Object.entries(e.parameters || {}).forEach(([k, v]) => {
      anyNamed = true;
      rows.push([`$parameters.${k}`, `${JSON.stringify(v)}  (evaluation ${String(i + 1).padStart(2, "0")})`]);
    });
  });
  if (!anyNamed) rows.push(["$parameters.*", "none in this intent"]);
  p.append(kv(rows));
  wrap.append(p);
}

// Resolve a mapping id the way the engine does, for display: literal, $reference from params, or empty.
function resolveRef(id, params) {
  if (!id) return { text: "any → search", kind: "search" };
  if (!id.startsWith("$")) return { text: id, kind: "lit" };
  let value = params;
  for (const key of id.slice(1).split(".")) value = value && typeof value === "object" ? value[key] : undefined;
  const ok = typeof value === "string" && value !== "";
  return { text: ok ? `${id} ⇒ ${value}` : `${id} ⇒ unresolved`, kind: ok ? "ref" : "miss" };
}

function entityChips(list, params, hitType) {
  const span = el("span", "cmp-ents");
  for (const x of list) {
    const r = resolveRef(x.id, params);
    const chip = el("span", `ent${x.type === hitType ? " hit" : ""}`);
    chip.append(el("b", "", x.type), el("i", `id ${r.kind}`, r.text));
    span.append(chip);
  }
  return span;
}

// The evaluation's three fields drawn over each mapping of the skill, with the ids resolved as usage saw them.
// The params map of one evaluation, as the engine saw it: shared token and subject, that evaluation's parameters.
const paramsFor = (params, i) => ({ ...params, parameters: params?.evaluations?.[i]?.parameters || {} });

function mappingComparison(u, params) {
  const box = el("div", "cmp");
  const mappings = skillMappings(u.agent, u.skill);
  const evaluations = state.trace?.a2a?.payload?.evaluations || [];
  box.append(el("h4", "", `the skill's mapping${mappings.length === 1 ? "" : "s"}, against each evaluation`));
  if (!mappings.length) {
    box.append(el("div", "cmp-none", "this skill publishes no x-coaz-mappings: there is nothing to compare, the engine answers missing_coaz_mapping"));
    return box;
  }
  evaluations.forEach((e, i) => {
    const block = el("div", "cmp-block");
    const evRow = el("div", "cmp-row ev");
    evRow.append(
      el("span", "cmp-k", `evaluation ${String(i + 1).padStart(2, "0")}`),
      el("span", "cmp-ents"), // spacer keeps the columns aligned with the mapping rows
    );
    const evTriple = el("span", "cmp-triple");
    evTriple.append(
      el("span", "cmp-f subject", `${e.subject?.type}${e.subject?.id ? ` · ${e.subject.id}` : " · any"}`),
      el("span", "cmp-arrow", "→"),
      el("span", "cmp-f action", e.action?.name || "any"),
      el("span", "cmp-arrow", "→"),
      el("span", "cmp-f resource", `${e.resource?.type}${e.resource?.id ? ` · ${e.resource.id}` : " · any"}`),
    );
    evRow.replaceChildren(el("span", "cmp-k", `evaluation ${String(i + 1).padStart(2, "0")}`), evTriple);
    block.append(evRow);
    mappings.forEach((m, j) => {
      const s = m.subject.some((x) => x.type === e.subject?.type);
      const r = m.resource.some((x) => x.type === e.resource?.type);
      const a = !m.action.length || m.action.some((x) => x.name === (e.action?.name || ""));
      const row = el("div", `cmp-row map${s && r && a ? " hit" : ""}`);
      const triple = el("span", "cmp-triple");
      const acts = el("span", `cmp-acts ${a ? "ok" : "no"}`);
      if (m.action.length) m.action.forEach((x) => acts.append(el("span", `act${x.name === (e.action?.name || "") ? " hit" : ""}`, x.name)));
      else acts.append(el("span", "act any", "any action"));
      triple.append(
        el("i", `mark ${s ? "ok" : "no"}`, s ? "✓" : "✗"),
        entityChips(m.subject, paramsFor(params, i), s ? e.subject?.type : null),
        el("span", "cmp-arrow", "→"),
        el("i", `mark ${a ? "ok" : "no"}`, a ? "✓" : "✗"),
        acts,
        el("span", "cmp-arrow", "→"),
        el("i", `mark ${r ? "ok" : "no"}`, r ? "✓" : "✗"),
        entityChips(m.resource, paramsFor(params, i), r ? e.resource?.type : null),
      );
      row.append(el("span", "cmp-k", `mapping ${j + 1}${s && r && a ? " · match" : ""}`), triple);
      block.append(row);
    });
    box.append(block);
  });
  box.append(el("p", "cmp-note", "Discovery compares the types and the action only. Usage then resolves the ids as shown after ⇒; an unresolved reference drops that entity, an empty id makes the match a search."));
  return box;
}

function part(label, value, cls) {
  const d = el("div", `triple-part ${cls}`);
  d.append(el("span", "lbl", label), el("span", "val", value));
  return d;
}

// ---------- fleet view ----------

async function renderFleet() {
  const wrap = $("#fleetCards");
  if (!(await ensureFleet())) {
    wrap.replaceChildren(el("div", "empty", "fleet unavailable: the console could not serve /api/fleet"));
    $("#coazCoverage").replaceChildren(el("div", "empty", "fleet unavailable"));
    return;
  }
  renderCoverage(state.fleet);
  wrap.replaceChildren();
  for (const a of state.fleet) {
    const card = a.card;
    const node = el("article", `fleet-card fa-${a.accent}${a.capability.has_mappings ? "" : " nomap"}`);
    const counts = a.capability.skills.reduce(
      (acc, s) => {
        acc.mappings += s.mappings.length;
        s.mappings.forEach((m) => {
          acc.actions += m.action.length;
          [...m.subject, ...m.resource].forEach((x) => { acc[!x.id ? "search" : x.id.startsWith("$") ? "ref" : "lit"] += 1; });
        });
        return acc;
      },
      { mappings: 0, actions: 0, lit: 0, ref: 0, search: 0 },
    );
    const strip = el("div", "fleet-counts");
    strip.append(
      el("span", "", `${a.capability.skills.length} skill${a.capability.skills.length === 1 ? "" : "s"}`),
      el("span", "", `${counts.mappings} mapping${counts.mappings === 1 ? "" : "s"}`),
      el("span", "", `${counts.actions} action${counts.actions === 1 ? "" : "s"}`),
      el("span", "ids", ""),
    );
    const ids = strip.lastChild;
    ids.append(el("i", "id lit", `${counts.lit} literal`), el("i", "id ref", `${counts.ref} reference`), el("i", "id search", `${counts.search} search`));
    node.append(strip);
    const head = el("div", "fleet-head");
    const title = el("div");
    title.append(el("h2", "", card.name), el("p", "muted", card.description || ""));
    head.append(title, el("span", "mono muted", `v${card.version || "?"}`));
    node.append(head);
    const iface = (card.supportedInterfaces || [])[0] || {};
    node.append(kv([
      ["id", a.id],
      ["interface", `${iface.url || "—"} · ${iface.protocolBinding || ""} ${iface.protocolVersion || ""}`],
      ["provider", card.provider?.organization],
      ["capabilities", Object.entries(card.capabilities || {}).filter(([, v]) => v).map(([k]) => k).join(", ") || "none"],
    ]));
    for (const s of a.capability.skills) {
      const raw = (card.skills || []).find((x) => x.id === s.id) || {};
      const sk = el("div", "skill");
      const h = el("div", "skill-head");
      h.append(el("b", "", s.name), el("span", "mono muted", s.id));
      (raw.tags || []).forEach((tg) => h.append(tag(tg)));
      sk.append(h, el("p", "", raw.description || ""));
      if (raw.examples?.length) {
        const ex = el("ul", "skill-ex");
        raw.examples.forEach((x) => ex.append(el("li", "", x)));
        sk.append(ex);
      }
      if (!s.mappings.length) {
        sk.append(el("div", "map-none", "no x-coaz-mappings → missing_coaz_mapping for every intent"));
      } else {
        const maps = el("div", "maps");
        maps.append(el("div", "maps-key mono", "x-coaz-mappings"));
        for (const m of s.mappings) {
          const row = el("div", "map");
          row.append(entities(m.subject, "subject"), el("span", "arrow", "→"));
          const acts = el("span", "map-acts");
          if (m.action.length) m.action.forEach((x) => acts.append(el("span", "act", x.name)));
          else acts.append(el("span", "act any", "any action"));
          row.append(acts, el("span", "arrow", "→"), entities(m.resource, "resource"));
          maps.append(row);
        }
        const json = el("details", "json");
        json.append(el("summary", "", "as published on the card"));
        json.append(el("pre", "", JSON.stringify({ id: s.id, name: s.name, "x-coaz-mappings": raw["x-coaz-mappings"] || [] }, null, 2)));
        maps.append(json);
        sk.append(maps);
      }
      node.append(sk);
    }
    wrap.append(node);
  }
}

// Coverage: subject type × resource type, each cell the actions declared and the skills declaring them.
function renderCoverage(fleet) {
  const wrap = $("#coazCoverage");
  const cells = new Map(); // "S|R" -> Map(action -> Set(skill label))
  const subjects = new Set();
  const resources = new Set();
  for (const a of fleet) {
    for (const s of a.capability.skills) {
      for (const m of s.mappings) {
        for (const sub of m.subject) {
          for (const res of m.resource) {
            subjects.add(sub.type);
            resources.add(res.type);
            const key = `${sub.type}|${res.type}`;
            if (!cells.has(key)) cells.set(key, new Map());
            const actions = m.action.length ? m.action.map((x) => x.name) : ["(any)"];
            for (const act of actions) {
              if (!cells.get(key).has(act)) cells.get(key).set(act, new Map());
              cells.get(key).get(act).set(`${a.id}/${s.id}`, { accent: a.accent, skill: s.id, agent: a.capability.name });
            }
          }
        }
      }
    }
  }
  const subjectList = [...subjects].sort();
  const resourceList = [...resources].sort();
  const table = el("table", "coverage-table");
  const head = el("tr");
  head.append(el("th", "", "resource \\ subject"));
  subjectList.forEach((s) => head.append(el("th", "subj", s)));
  table.append(head);
  for (const r of resourceList) {
    const tr = el("tr");
    tr.append(el("th", "", r));
    for (const s of subjectList) {
      const td = el("td");
      const cell = cells.get(`${s}|${r}`);
      if (!cell) td.append(el("span", "cov-empty", "no skill declares it"));
      else {
        for (const [act, skills] of cell) {
          const line = el("div", "cov-act");
          line.append(el("span", "act", act));
          for (const info of skills.values()) {
            const chip = el("span", `cov-skill dotc-${info.accent}`, `${info.agent} · ${info.skill}`);
            line.append(chip);
          }
          td.append(line);
        }
      }
      tr.append(td);
    }
    table.append(tr);
  }
  wrap.replaceChildren(table);
}

function entities(list, cls) {
  const span = el("span", `map-ents ${cls}`);
  for (const e of list) {
    const chip = el("span", "ent");
    chip.append(el("b", "", e.type));
    if (!e.id) chip.append(el("i", "id search", "any → search"));
    else if (e.id.startsWith("$")) chip.append(el("i", "id ref", e.id));
    else chip.append(el("i", "id lit", e.id));
    span.append(chip);
  }
  return span;
}

boot();


// ---------- graph ----------
// Two live graphs drawn with Cytoscape (vendored, loaded on first open):
//   schema - one node per node type from the data schema, edges = relationship kinds,
//            optionally the policies as dashed action edges (the agent's whole vocabulary);
//   reach  - one persona in the middle, one edge per granted action to each resource it reaches now.

const GRAPH_SCRIPTS = ["layout-base.js", "cose-base.js", "cytoscape.min.js", "cytoscape-fcose.js"];

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const tag = document.createElement("script");
    tag.src = src;
    tag.onload = resolve;
    tag.onerror = () => reject(new Error(`failed to load ${src}`));
    document.head.append(tag);
  });
}

async function ensureGraphLib() {
  if (state.graph.loaded) return;
  for (const file of GRAPH_SCRIPTS) await loadScript(`/static/vendor/${file}`);
  if (window.cytoscape && window.cytoscapeFcose) window.cytoscape.use(window.cytoscapeFcose);
  state.graph.loaded = true;
}

const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function graphStyle() {
  const t = {
    text: token("--text"), sub: token("--sub"), line: token("--line-strong"), card: token("--card"),
    blue: token("--blue"), blueSoft: token("--blue-soft"), teal: token("--teal"), tealSoft: token("--teal-soft"),
    orange: token("--orange"), orangeInk: token("--orange-ink"),
  };
  return [
    { selector: "node", style: {
      shape: "ellipse", width: "data(size)", height: "data(size)",
      "background-color": t.tealSoft, "border-width": 2, "border-color": t.teal,
      label: "data(label)", "font-family": "Inter, Arial, sans-serif", "font-size": 14, "font-weight": 500, color: t.text,
      "text-valign": "bottom", "text-margin-y": 7, "text-wrap": "ellipsis", "text-max-width": 160,
      "text-background-color": t.card, "text-background-opacity": 0.85, "text-background-padding": 2, "text-background-shape": "round-rectangle",
      "transition-property": "border-width, opacity", "transition-duration": "0.15s",
    } },
    { selector: "node[?identity]", style: { "background-color": t.blueSoft, "border-color": t.blue, "outline-width": 1, "outline-color": t.blue, "outline-offset": 3, "outline-opacity": 0.45 } },
    { selector: "node[kind = 'persona']", style: { "background-color": t.blue, "border-color": t.blue, color: t.text, "font-weight": 600, "font-size": 15, "text-max-width": 220 } },
    { selector: "edge", style: {
      "curve-style": "bezier", width: 1.2, "line-color": t.line, "target-arrow-shape": "triangle", "target-arrow-color": t.line, "arrow-scale": 0.8,
      label: "data(label)", "font-family": "JetBrains Mono, monospace", "font-size": 11, color: t.sub, "text-rotation": "autorotate",
      "text-background-color": t.card, "text-background-opacity": 0.9, "text-background-padding": 2, "text-background-shape": "round-rectangle",
    } },
    // Policy edges are dense around the identity types: their labels appear for the node you click.
    { selector: "edge[kind = 'policy']", style: { "line-style": "dashed", "line-color": t.orange, "target-arrow-color": t.orange, color: t.orangeInk, width: 1.6, "font-weight": 600, label: "" } },
    { selector: "edge[kind = 'policy'].show", style: { label: "data(label)" } },
    { selector: "edge[kind = 'reach']", style: { "line-color": t.orange, "target-arrow-color": t.orange, color: t.orangeInk, width: 1.4 } },
    { selector: "node:selected", style: { "border-width": 4 } },
    { selector: ".dim", style: { opacity: 0.18 } },
  ];
}

// Type nodes are large enough to read from the back of a room: 44 px for a single instance, up to 96 px.
const nodeSize = (count) => Math.max(44, Math.min(96, 36 + 9 * Math.sqrt(count || 1)));

async function schemaElements(withPolicies) {
  const schema = state.schema || (await fetch("/api/schema").then((r) => (r.ok ? r.json() : Promise.reject(r))));
  state.schema = schema;
  const nodes = schema.nodes
    .filter((n) => !n.type.startsWith("_"))
    .map((n) => ({ data: { id: n.type, label: n.type, kind: "type", size: nodeSize(n.count), count: n.count, identity: n.identity, properties: n.properties } }));
  const known = new Set(nodes.map((n) => n.data.id));
  const edges = schema.relationships
    .filter((r) => known.has(r.source) && known.has(r.target))
    .map((r, i) => ({ data: { id: `r${i}`, source: r.source, target: r.target, label: r.relation, kind: "relationship", count: r.count } }));
  if (withPolicies) {
    const listings = await Promise.all(["User", "Customer"].map((type) => fetch(`/api/policies?subject_type=${type}`).then((r) => (r.ok ? r.json() : Promise.reject(r)))));
    listings.forEach((listing) => {
      (listing.policies || []).forEach((pol, i) => {
        if (!known.has(pol.subject_type) || !known.has(pol.resource_type)) return;
        pol.actions.forEach((action, j) => edges.push({ data: { id: `p${pol.subject_type}${i}${j}`, source: pol.subject_type, target: pol.resource_type, label: action, kind: "policy", cypher: pol.cypher, params: pol.params } }));
      });
    });
    state.policies.User = listings[0];
    state.policies.Customer = listings[1];
  }
  return { nodes, edges };
}

async function reachElements(persona) {
  const data = await fetch(`/api/persona-facts?persona=${encodeURIComponent(persona.id)}`).then((r) => (r.ok ? r.json() : Promise.reject(r)));
  const nodes = [{ data: { id: `persona:${persona.id}`, label: persona.name, kind: "persona", size: 54, type: persona.type, externalId: persona.id, identity: true } }];
  const edges = [];
  const seen = new Set();
  for (const fact of data.facts) {
    for (const id of fact.ids) {
      const nid = `${fact.resource_type}:${id}`;
      if (!seen.has(nid)) {
        seen.add(nid);
        nodes.push({ data: { id: nid, label: id, kind: "resource", size: 30, type: fact.resource_type, externalId: id, identity: ["User", "Customer"].includes(fact.resource_type) } });
      }
      edges.push({ data: { id: `${fact.action}->${nid}`, source: `persona:${persona.id}`, target: nid, label: fact.action, kind: "reach" } });
    }
  }
  return { nodes, edges, facts: data.facts };
}

function graphSidePanel(node) {
  const side = $("#graphSide");
  side.replaceChildren();
  if (!node) {
    side.append(el("div", "empty", "Click a node, or pick one in the toolbar, for its details."));
    return;
  }
  const d = node.data();
  side.append(el("h2", "", d.label));
  if (d.kind === "type") {
    side.append(el("div", "kind", `${d.identity ? "identity " : ""}node type · ${d.count} instance${d.count === 1 ? "" : "s"}`));
    if (d.properties?.length) {
      side.append(el("h3", "", "properties"));
      d.properties.forEach((p) => {
        const row = el("div", "row");
        row.append(el("span", "k", p.name), el("span", "v", (p.types || []).join(", ")));
        side.append(row);
      });
    }
    const rels = node.connectedEdges("[kind = 'relationship']");
    if (rels.length) {
      side.append(el("h3", "", "relationships"));
      rels.forEach((e) => {
        const row = el("div", "row");
        row.append(el("span", "k", e.data("label")), el("span", "v", `${e.data("source")} → ${e.data("target")}`));
        side.append(row);
      });
    }
    const pols = [];
    ["User", "Customer"].forEach((type) => (state.policies[type]?.policies || []).forEach((pol) => {
      if (pol.subject_type === d.id || pol.resource_type === d.id) pols.push(pol);
    }));
    if (pols.length) {
      side.append(el("h3", "", "policies naming this type"));
      pols.forEach((pol) => {
        const row = el("div", "pol");
        row.append(el("i", "", pol.subject_type), document.createTextNode(" → "), el("b", "", pol.actions.join(", ")), document.createTextNode(" → "), el("i", "", pol.resource_type), el("small", "", pol.cypher));
        side.append(row);
      });
    }
  } else {
    side.append(el("div", "kind", d.kind === "persona" ? `${d.type} · the caller` : `${d.type}${d.identity ? " · identity" : ""}`));
    const row = el("div", "row");
    row.append(el("span", "k", "external_id"), el("span", "v", d.externalId));
    side.append(row);
    const edges = d.kind === "persona" ? node.connectedEdges() : node.connectedEdges("[kind = 'reach']");
    if (edges.length) {
      side.append(el("h3", "", d.kind === "persona" ? "reaches" : "reached through"));
      edges.forEach((e) => {
        const r = el("div", "row");
        r.append(el("span", "k", e.data("label")), el("span", "v", d.kind === "persona" ? e.target().data("label") : "from the caller"));
        side.append(r);
      });
    }
  }
}

function bindGraphControls() {
  if (bindGraphControls.done) return;
  bindGraphControls.done = true;
  $("#graphMode").addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    state.graph.mode = btn.dataset.mode;
    [...$("#graphMode").children].forEach((b) => b.classList.toggle("active", b === btn));
    renderGraph();
  });
  $("#graphPolicies").addEventListener("change", (e) => {
    state.graph.policies = e.target.checked;
    renderGraph();
  });
  const select = $("#graphPersona");
  state.config.personas.forEach((p) => {
    const opt = el("option", "", `${p.name} (${p.type})`);
    opt.value = p.id;
    select.append(opt);
  });
  select.addEventListener("change", () => {
    state.graph.persona = state.config.personas.find((p) => p.id === select.value);
    renderGraph();
  });
  $("#graphRelayout").addEventListener("click", () => state.graph.cy && runLayout(state.graph.cy));
  // Keyboard route into the canvas: the picker opens the same details panel a tap on a node does.
  $("#graphNodePick").addEventListener("change", (e) => {
    const cy = state.graph.cy;
    if (!cy) return;
    const node = e.target.value ? cy.getElementById(e.target.value) : null;
    focusGraphNode(cy, node);
    if (node?.length) cy.animate({ center: { eles: node }, duration: 250 });
  });
}

function focusGraphNode(cy, node) {
  if (!node?.length) {
    cy.elements().removeClass("dim").removeClass("show");
    graphSidePanel(null);
    return;
  }
  cy.elements().addClass("dim").removeClass("show");
  node.closedNeighborhood().removeClass("dim");
  node.connectedEdges().addClass("show");
  graphSidePanel(node);
}

function fillNodePicker(nodes) {
  const pick = $("#graphNodePick");
  pick.replaceChildren(el("option", "", "inspect node…"));
  pick.firstChild.value = "";
  [...nodes]
    .sort((a, b) => a.data.label.localeCompare(b.data.label))
    .forEach((n) => {
      const opt = el("option", "", n.data.kind === "resource" ? `${n.data.type} ${n.data.label}` : n.data.label);
      opt.value = n.data.id;
      pick.append(opt);
    });
}

const graphLayout = () => (window.cytoscapeFcose
  ? { name: "fcose", animate: false, fit: true, padding: 24, idealEdgeLength: 150, nodeRepulsion: 14000, quality: "default", randomize: true }
  : { name: "cose", animate: false, fit: true, padding: 24 });

// Opening zoom: fit, then never start below 1:1 so the type names are readable at once;
// a graph that does not fit at 1:1 is centred and left to pan.
const MIN_OPENING_ZOOM = 1.0;

function runLayout(cy) {
  cy.layout(graphLayout()).run();
  cy.fit(undefined, 24);
  if (cy.zoom() < MIN_OPENING_ZOOM) {
    cy.zoom({ level: MIN_OPENING_ZOOM, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
    cy.center();
  }
}

async function renderGraph() {
  bindGraphControls();
  const g = state.graph;
  const reach = g.mode === "reach";
  $("#graphPolicyOpt").hidden = reach;
  $("#graphPersona").hidden = !reach;
  if (reach && !g.persona) {
    g.persona = state.persona;
    $("#graphPersona").value = g.persona.id;
  }
  $("#graphLede").textContent = reach
    ? "Reach: the chosen identity in the middle, one relationship per granted action to every resource it reaches right now, answered by the platform's AuthZEN search for each of its policies. The same facts as the chips on the Playground, drawn."
    : "Schema: one node per type, sized by how many instances the project holds, linked by the relationship kinds the platform observed. Switch on the policies to see the only relationships the agent may ever name as actions.";
  $("#graphInfo").textContent = "loading…";
  graphSidePanel(null);
  // Only the newest render may touch the canvas: a slower earlier request must not overwrite it.
  const render = (g.render = (g.render || 0) + 1);
  const persona = g.persona;
  const policies = g.policies;
  let elements;
  try {
    await ensureGraphLib();
    if (render !== g.render) return;
    elements = reach ? await reachElements(persona) : await schemaElements(policies);
  } catch (err) {
    if (render !== g.render) return;
    $("#graphInfo").textContent = `unavailable (${err.status || err.message || err})`;
    return;
  }
  if (render !== g.render) return;
  if (g.cy) g.cy.destroy();
  g.cy = window.cytoscape({ container: $("#graphCanvas"), elements: [...elements.nodes, ...elements.edges], style: graphStyle(), wheelSensitivity: 0.2 });
  runLayout(g.cy);
  fillNodePicker(elements.nodes);
  g.cy.on("tap", "node", (e) => {
    focusGraphNode(g.cy, e.target);
    $("#graphNodePick").value = e.target.id();
  });
  g.cy.on("tap", (e) => {
    if (e.target === g.cy) {
      focusGraphNode(g.cy, null);
      $("#graphNodePick").value = "";
    }
  });
  const polCount = elements.edges.filter((e) => e.data.kind === "policy").length;
  // A fact whose AuthZEN search failed has no ids; name it so an outage never reads as "nothing reached".
  const failed = (elements.facts || []).filter((f) => f.error).map((f) => `${f.action} unavailable`);
  $("#graphInfo").textContent = reach
    ? [`${elements.nodes.length - 1} resources reached through ${elements.edges.length} grants`, ...failed].join(" · ")
    : `${elements.nodes.length} node types · ${elements.edges.length - polCount} relationship kinds${policies ? ` · ${polCount} policy relationships` : ""}`;
}
