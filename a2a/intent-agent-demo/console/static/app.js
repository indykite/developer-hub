// Intent Agent demo console - front end. Plain JS, no build step.
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
  renderPersonas();
  renderPersonaFacts();
  suggestPrompt();
  selectView("playground");
  bindEvents();
  refreshHealth();
  setInterval(refreshHealth, 15000);
  loadAuditRecent();
  openAuditStream();
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
  if (playground) {
    // The failure modes belong to Guardrails; the Playground always sends a proper delegation token.
    document.querySelector(".auth-wrap").classList.toggle("guardrails", view === "guardrails");
    if (view !== "guardrails") setAuthMode("delegation");
    $("#viewTitle").textContent = view === "guardrails" ? "Try to break it." : "Ask as someone. Watch the intent appear.";
    $("#viewLede").textContent =
      view === "guardrails"
        ? "Instructions, encoded payloads, overrides, SQL, oversized filters, the wrong token: every one is refused with the same fixed shape, and every refusal is audited."
        : "Pick an identity, send a sentence. The Intent Agent turns it into AuthZEN evaluations it can prove against the graph's policies - nothing more, nothing invented.";
    renderGallery();
  }
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
    card.append(
      el("div", "who", `${persona?.name ?? p.persona} · ${persona?.type ?? ""}${p.auth_mode !== "delegation" ? ` · ${p.auth_mode.replace("_", " ")}` : ""}`),
      el("div", "text", p.text),
      el("div", "expect", p.expect ? `→ ${p.expect}` : ""),
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

const STEPS = ["issue", "exchange", "introspect", "bind", "vocab", "extract", "validate", "audit"];

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
    STEPS.slice(3, 7).forEach((s) => setStep(s, "skip"));
    setStep("audit", trace.audit ? "done" : "skip", trace.audit ? "rejection audited" : "");
    return;
  }
  setStep("introspect", "done", `${a.latency_ms} ms total`);
  if (a.rpc_error) {
    setStep("bind", "fail", `rpc ${a.rpc_error.code}`);
    STEPS.slice(4, 7).forEach((s) => setStep(s, "skip"));
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
  setStep("audit", trace.audit ? "done" : "warn", trace.audit ? `${rec.outcome} · ${rec.latency_ms} ms` : "no record yet");
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
  bits.push(`req ${trace.request_id.slice(0, 8)}`);
  meta.textContent = bits.filter(Boolean).join("  ·  ");
  renderActors(trace);
  renderChain(trace);
  renderWire();
}

// ---------- actor strip: who talked to whom for this prompt ----------

const ACTORS = [
  ["console", "console", "demo issuer · A2A client"],
  ["ts", "token-service", "IndyKite Token Service"],
  ["agent", "intent-agent", "the product"],
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
  // 7. decisions
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
const audList = (aud) => (Array.isArray(aud) ? aud.join(", ") : aud || "—");

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
  const msg =
    entry.source === "intent-agent"
      ? `${rec.subject_type || ""} ${rec.subject_id || ""} — ${rec.prompt || rec.message || ""}`
      : `${rec.subject || ""} via ${rec.actor || ""} — ${rec.reason || ""}`;
  s.append(
    el("span", `src ${entry.source}`, entry.source),
    el("span", `out ${outcome}`, outcome || "—"),
    el("span", "msg", msg),
    el("span", "ts", (rec.timestamp || entry.received_at || "").replace("T", " ").slice(0, 23)),
  );
  d.append(s, el("pre", "", pretty(rec)));
  return d;
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
      label: "data(label)", "font-family": "Inter, Arial, sans-serif", "font-size": 11, color: t.text,
      "text-valign": "bottom", "text-margin-y": 6, "text-wrap": "ellipsis", "text-max-width": 120,
      "text-background-color": t.card, "text-background-opacity": 0.85, "text-background-padding": 2, "text-background-shape": "round-rectangle",
      "transition-property": "border-width, opacity", "transition-duration": "0.15s",
    } },
    { selector: "node[?identity]", style: { "background-color": t.blueSoft, "border-color": t.blue, "outline-width": 1, "outline-color": t.blue, "outline-offset": 3, "outline-opacity": 0.45 } },
    { selector: "node[kind = 'persona']", style: { "background-color": t.blue, "border-color": t.blue, color: t.text, "font-weight": 600, "font-size": 12, "text-max-width": 220 } },
    { selector: "edge", style: {
      "curve-style": "bezier", width: 1.2, "line-color": t.line, "target-arrow-shape": "triangle", "target-arrow-color": t.line, "arrow-scale": 0.8,
      label: "data(label)", "font-family": "JetBrains Mono, monospace", "font-size": 9, color: t.sub, "text-rotation": "autorotate",
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

const nodeSize = (count) => Math.max(26, Math.min(70, 22 + 7 * Math.sqrt(count || 1)));

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
  $("#graphRelayout").addEventListener("click", () => state.graph.cy?.layout(graphLayout()).run());
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
  ? { name: "fcose", animate: false, fit: true, padding: 40, idealEdgeLength: 110, nodeRepulsion: 9000, quality: "default", randomize: true }
  : { name: "cose", animate: false, fit: true, padding: 40 });

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
  g.cy.layout(graphLayout()).run();
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
