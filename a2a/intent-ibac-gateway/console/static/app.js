// Intent + IBAC demo console - front end. Plain JS, no build step.
// Everything shown comes from /api/*; nothing is inferred client-side that the
// backend did not return.

const state = {
  config: null,
  gallery: null,
  // The logged-in person (from /api/me) and the persona view of them: the gallery's entry when it is one of the demo's people.
  me: null,
  persona: null,
  agent: null,
  authMode: "delegation",
  unmapped: "allow", // the gateway's setting for a skill without a mapping, as the preview applies it
  category: null,
  view: "playground",
  trace: null,
  wireTab: "request",
  auditEntries: [],
  suggested: "",
  vocabSubject: "Person",
  graph: { mode: "schema", persona: null, policies: false, cy: null, loaded: false },
  schema: null,
  policies: {},
  fleet: null,
  // The (agent, skill) whose usage is open on the IBAC board, and the what-if answers fetched for unmatched skills.
  ibacPick: null,
  ibacWhatIf: new Map(),
  // The answers of the other gateways to the current prompt, once "send through the other gateways" ran.
  compare: null,
  // The cards as the logged-in person sees them: /api/my-cards, one extended-card request per gateway.
  myCards: null,
  myCardsLoading: false,
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
  const [config, gallery, me] = await Promise.all([
    fetch("/api/config").then((r) => r.json()),
    fetch("/api/prompts").then((r) => r.json()),
    fetch("/api/me").then((r) => r.json()),
  ]);
  state.config = config;
  state.gallery = gallery.categories;
  setMe(me);
  state.agent = config.fleet[0]?.id || null;
  fillAgentPick();
  // Deep links for a talk track: ?view=fleet, or ?agent=<id>&prompt=...&send=1 to replay a beat (as whoever is logged in).
  const link = new URLSearchParams(location.search);
  if (config.fleet.some((a) => a.id === link.get("agent"))) state.agent = link.get("agent");
  state.loginError = link.get("login_error") || "";
  // A card clicked before a login: back in the composer once its person is logged in.
  const pending = JSON.parse(sessionStorage.getItem("pendingCard") || "null");
  if (pending && state.me && pending.persona === state.me.sub) {
    sessionStorage.removeItem("pendingCard");
    state.view = pending.view || "playground";
    state.category = pending.category || null;
    if (config.fleet.some((a) => a.id === pending.agent)) state.agent = pending.agent;
    state.authMode = pending.auth_mode || "delegation";
    state.suggested = pending.text;
  } else if (pending && state.me) {
    sessionStorage.removeItem("pendingCard");
  }
  renderPersonas();
  renderPersonaFacts();
  suggestPrompt();
  selectView(["playground", "guardrails", "howibac", "fleet", "vocabulary", "audit", "agents", "graph"].includes(link.get("view")) ? link.get("view") : state.view || "playground");
  if (pending && state.me && pending.persona === state.me.sub) {
    $("#promptText").value = pending.text;
    setAuthMode(state.authMode);
    setAgent(state.agent);
  }
  bindEvents();
  refreshHealth();
  setInterval(refreshHealth, 15000);
  loadMyCards();
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

// The person behind the session cookie, as /api/me reports them.
function setMe(me) {
  state.me = me && me.logged_in ? me : null;
  state.myCards = null;
  if (!state.me) {
    state.persona = null;
    return;
  }
  const known = state.config.personas.find((p) => p.id === state.me.sub);
  state.persona = known || { id: state.me.sub, type: "Person", name: state.me.name, role: "a Person of the tenant, not one of the demo's people", email: state.me.email, facts: [] };
}

const initials = (name) => (name || "?").split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();

// ---------- the cards as the person sees them ----------

// Ask every gateway for its extended card with the person's token; each answers with the skills they may use.
async function loadMyCards(force) {
  if (!state.me) {
    state.myCards = null;
    return null;
  }
  if (state.myCards && !force) return state.myCards;
  if (state.myCardsLoading) return state.myCards;
  state.myCardsLoading = true;
  renderFleetYou();
  try {
    const res = await fetch("/api/my-cards");
    if (!res.ok) throw new Error(res.statusText);
    state.myCards = await res.json();
  } catch (err) {
    state.myCards = { error: String(err), cards: [] };
  } finally {
    state.myCardsLoading = false;
  }
  renderFleetYou();
  if (state.view === "fleet") renderFleet();
  if (state.trace) refreshBoardMarks();
  return state.myCards;
}

const myCard = (agentId) => (state.myCards?.cards || []).find((c) => c.agent === agentId);

// "yes" when the person's card from that gateway lists the skill, "no" when it hid it, "unknown" otherwise.
function useMark(agentId, skillId) {
  const c = myCard(agentId);
  if (!c || c.kind !== "card") return "unknown";
  return c.kept.includes(skillId) ? "yes" : "no";
}

function markNode(kind, agentId) {
  const c = myCard(agentId);
  const text = kind === "yes" ? "you may use it" : kind === "no" ? "not for you" : c && c.kind === "refused" ? "gateway refused you" : "unknown";
  return el("span", `use-mark ${kind}`, text);
}

// The strip above the fleet: who is looking, how many skills they got, a refresh.
function renderFleetYou() {
  const box = $("#fleetYou");
  if (!box) return;
  if (!state.me) {
    box.hidden = false;
    box.className = "fleet-you";
    box.replaceChildren(el("span", "", "Log in to see each card as that person: the gateways answer the authenticated card with the skills the person may use."));
    return;
  }
  box.hidden = false;
  box.className = `fleet-you${state.myCardsLoading ? " loading" : ""}`;
  const mc = state.myCards;
  box.replaceChildren();
  const who = el("span");
  who.append(el("b", "", state.persona.name), document.createTextNode(" sees the cards as the gateways filter them for them"));
  box.append(who);
  if (state.myCardsLoading) box.append(el("span", "you-count", "asking the six gateways…"));
  else if (mc?.error) box.append(el("span", "use-mark no", `could not ask the gateways: ${mc.error}`));
  else if (mc) {
    const kept = mc.cards.reduce((n, c) => n + (c.kept?.length || 0), 0);
    const listed = mc.cards.reduce((n, c) => n + (c.listed?.length || 0), 0);
    const refused = mc.cards.filter((c) => c.kind === "refused").length;
    box.append(el("span", "you-count", `${kept} of ${listed} skills across ${mc.cards.length} cards`));
    if (refused) box.append(el("span", "use-mark no", `${refused} gateway${refused === 1 ? "" : "s"} refused the person`));
    box.append(el("span", "", "read from the DISCOVERY record of each gateway (action CAN_USE, type Skill)"));
  }
  const btn = el("button", "you-refresh", "ask again");
  btn.addEventListener("click", () => loadMyCards(true));
  box.append(btn);
}

// After the cards arrive, redraw the marks on the board without re-running the preview.
function refreshBoardMarks() {
  for (const chip of document.querySelectorAll(".fa-skill[data-pick]")) {
    const [agentId, skillId] = chip.dataset.pick.split("/");
    const kind = useMark(agentId, skillId);
    chip.classList.toggle("locked", kind === "no");
    chip.querySelector(".lock")?.remove();
    if (kind === "no") chip.append(el("span", "lock", "not for you"));
  }
  for (const line of document.querySelectorAll(".fa-you")) {
    const c = myCard(line.dataset.agent);
    line.replaceChildren(document.createTextNode(youLine(line.dataset.agent, c)));
    line.className = `fa-you${!c ? " off" : c.kind !== "card" ? " none" : c.kept.length ? "" : " none"}`;
  }
}

function youLine(agentId, c) {
  if (!state.me) return "log in to see which skills this gateway shows you";
  if (!c) return state.myCardsLoading ? "asking the gateway which skills you may use…" : "skills you may use: not asked yet";
  if (c.kind === "refused") return `this gateway refuses you (HTTP ${c.http_status}): no card`;
  if (c.kind !== "card") return `the gateway could not answer the card: ${c.error || "error"}`;
  return c.kept.length ? `skills you may use: ${c.kept.join(", ")}${c.hidden.length ? ` · hidden: ${c.hidden.join(", ")}` : ""}` : "skills you may use: none, the gateway hides every skill";
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
  $("#mapMode").addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    state.unmapped = btn.dataset.mode;
    $("#mapMode").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
  });
  $("#sendBtn").addEventListener("click", () => sendPrompt($("#promptText").value));
  $("#agentPick").addEventListener("change", (e) => {
    state.agent = e.target.value;
  });
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
        : "Log in as one of the people, send a sentence. The gateway checks who you are and asks the Intent Agent what you mean; the platform decides whether you may reach the agent; the preview shows which cards declare the skill.";
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
    const gws = Object.values(h.gateways || {});
    const up = gws.filter(Boolean).length;
    $("#dotGateways").className = `dot ${up === gws.length && gws.length ? "up" : "down"}`;
    $("#railFleet").textContent = `· ${up}/${gws.length} up`;
    const m = h.metrics;
    $("#railMetrics").textContent = m
      ? `${m.requests_total} requests · avg ${m.avg_latency_ms ?? "—"} ms`
      : "metrics unavailable";
  } catch {
    $("#dotAgent").className = "dot down";
    $("#dotIts").className = "dot down";
    $("#dotGateways").className = "dot down";
  }
}

// ---------- personas and gallery ----------

function renderPersonas() {
  const wrap = $("#personas");
  wrap.replaceChildren();
  const who = el("div", "who");
  if (state.me) {
    const p = state.persona;
    who.append(el("span", "avatar", initials(p.name)));
    const txt = el("span");
    txt.append(el("div", "who-name", p.name), el("div", "who-sub", `${p.type} · ${p.id}${state.me.email ? ` · ${state.me.email}` : ""}`));
    who.append(txt, el("span", "who-role", p.role));
    const act = el("span", "who-act");
    const out = el("a", "btn-link", "log out");
    out.href = "/auth/logout";
    act.append(el("span", "who-hint", "every prompt carries this person's Auth0 token"), out);
    who.append(act);
  } else {
    who.append(el("span", "avatar", "?"));
    const txt = el("span");
    txt.append(el("div", "who-name", "Nobody is logged in"), el("div", "who-hint", "the gateways accept only a person's Auth0 token: log in to send a prompt"));
    who.append(txt);
    const act = el("span", "who-act");
    if (state.loginError) act.append(el("span", "who-err", `login failed: ${state.loginError}`));
    const btn = el("a", "login-btn", "Log in with Auth0");
    btn.href = "/auth/login";
    act.append(btn);
    who.append(act);
  }
  wrap.append(who);
  wrap.append(el("div", "personas-title", state.me ? "the demo's people · click one to log in as them" : "the demo's people · click one to log in as them"));
  const chips = el("div", "personas");
  for (const p of state.config.personas) {
    const mine = state.me && p.id === state.me.sub;
    const b = el("a", `persona ${mine ? "me" : "other"}`);
    b.href = mine ? "#" : `/auth/login?hint=${encodeURIComponent(p.email || "")}`;
    b.title = mine ? "you" : `log in as ${p.name} (${p.email || p.id})`;
    const av = el("span", "avatar", initials(p.name));
    const txt = el("span");
    const type = el("div", "type", `${p.type} · `);
    type.append(el("small", "", p.email || p.id));
    txt.append(el("div", "name", p.name), type);
    b.append(av, txt);
    if (mine) b.addEventListener("click", (e) => e.preventDefault());
    chips.append(b);
  }
  wrap.append(chips);
}

// Prefill the composer with a real, sendable prompt for the selected identity.
// Only replaces text the console put there itself, never something the user typed.
function suggestPrompt() {
  const box = $("#promptText");
  if (box.value.trim() && box.value !== state.suggested) return;
  const prompts = state.gallery.filter((c) => c.kind !== "guardrail").flatMap((c) => c.prompts);
  const first = (state.persona && prompts.find((p) => p.persona === state.persona.id)) || prompts[0];
  state.suggested = first ? first.text : "";
  box.value = state.suggested;
}

// Facts come from the graph: one AuthZEN search/resource per policy of the persona's type.
// The hand-written facts from prompts.json are shown only while loading or if the platform is down.
const factsCache = new Map();

async function renderPersonaFacts() {
  const wrap = $("#personaFacts");
  const persona = state.persona;
  if (!persona) {
    wrap.replaceChildren(el("span", "loading", "log in to see what you reach"));
    return;
  }
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
  const me = state.me?.sub;
  const tabs = $("#galleryTabs");
  tabs.replaceChildren();
  for (const c of cats) {
    const mine = me ? c.prompts.filter((p) => p.persona === me).length : 0;
    const b = el("button", `${c.id === state.category ? "active" : ""}${guard ? " guard" : ""}`, c.title);
    if (me) b.append(el("small", "count", String(mine)));
    b.title = me ? `${mine} card${mine === 1 ? "" : "s"} written for you` : "";
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
  const prompts = cat?.prompts ?? [];
  const mine = me ? prompts.filter((p) => p.persona === me) : [];
  const others = prompts.filter((p) => !me || p.persona !== me);
  if (!me) {
    // Nobody logged in: the plain grid, every card a way in (it logs you in as its person).
    for (const p of prompts) cards.append(galleryCard(p, false));
    return;
  }
  for (const p of mine) cards.append(galleryCard(p, true));
  if (!mine.length) {
    cards.append(el("div", "empty gallery-empty", `No card of this category is written for ${state.persona.name}. The ones below are other people's: open one to log in as them, or type your own question above.`));
  }
  if (others.length) {
    const fold = el("details", "gallery-others");
    fold.append(el("summary", "", `${others.length} card${others.length === 1 ? "" : "s"} written for other people · open one to log in as them`));
    const grid = el("div", "gallery-cards");
    for (const p of others) grid.append(galleryCard(p, false));
    fold.append(grid);
    cards.append(fold);
  }
}

// One card: the logged-in person's cards send; other people's cards log you in as them first.
function galleryCard(p, mine) {
  const persona = state.config.personas.find((x) => x.id === p.persona);
  const card = el("button", `card${mine ? "" : " other"}`);
  const text = el("div", "text", p.text);
  text.title = p.text;
  const expect = el("div", "expect", p.expect ? `→ ${p.expect}` : "");
  if (p.expect) expect.title = p.expect;
  const through = state.config.fleet.find((a) => a.id === p.agent);
  const mode = p.auth_mode !== "delegation" ? ` · ${p.auth_mode.replace("_", " ")}` : "";
  card.append(
    el("div", `who${mine ? " mine" : ""}`, `${mine ? "you" : `${persona?.name ?? p.persona} · log in as them`}${through ? ` → ${through.name}` : ""}${mode}`),
    text,
    expect,
  );
  card.addEventListener("click", () => {
    if (!mine) {
      // Remember the card, log in as its person; boot() puts it back in the composer after the callback.
      sessionStorage.setItem("pendingCard", JSON.stringify({ persona: p.persona, text: p.text, agent: p.agent, auth_mode: p.auth_mode, view: state.view, category: state.category }));
      location.href = `/auth/login?hint=${encodeURIComponent(persona?.email || "")}`;
      return;
    }
    setAuthMode(p.auth_mode);
    if (p.agent) setAgent(p.agent);
    $("#promptText").value = p.text;
    sendPrompt(p.text);
  });
  return card;
}

function fillAgentPick() {
  const pick = $("#agentPick");
  pick.replaceChildren();
  for (const a of state.config.fleet) {
    const opt = el("option", "", a.name);
    opt.value = a.id;
    pick.append(opt);
  }
  pick.value = state.agent;
}

function setAgent(agentId) {
  state.agent = agentId;
  $("#agentPick").value = agentId;
}

const agentName = (id) => state.config?.fleet.find((a) => a.id === id)?.name || id;

function setAuthMode(mode) {
  state.authMode = mode;
  [...$("#authMode").children].forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
}

// ---------- the round trip ----------

const STEPS = ["issue", "introspect", "exchange", "whoami", "admission", "intent", "forward", "discover", "confirm", "audit"];

function setStep(step, status, note) {
  const node = document.querySelector(`.pipe-node[data-step="${step}"]`);
  node.className = `pipe-node ${status}`;
  node.querySelector("em").textContent = note ?? "";
}

function resetPipeline() {
  for (const s of STEPS) setStep(s, "");
}

async function sendPrompt(text, agentOverride) {
  const prompt = (text || "").trim();
  if (!prompt) return;
  if (agentOverride) setAgent(agentOverride);
  const btn = $("#sendBtn");
  if (btn.disabled) return; // a request is already running; Enter and the cards share this guard
  if (!state.me) {
    $("#result").hidden = false;
    $("#verdict").className = "result-verdict error";
    $("#verdict").textContent = "Log in first: the gateways accept only a person's Auth0 token.";
    $("#resultMeta").textContent = "";
    return;
  }
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
      body: JSON.stringify({ prompt, auth_mode: state.authMode, decide: true, agent: state.agent, unmapped: state.unmapped }),
    });
    const trace = await res.json();
    clearInterval(ticker);
    if (res.status === 401) {
      setMe(null);
      renderPersonas();
      renderPersonaFacts();
    }
    if (!res.ok) throw new Error(trace.detail ? JSON.stringify(trace.detail) : res.statusText);
    state.trace = trace;
    state.compare = null;
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

// Records of this prompt, by who wrote them.
const gwRecords = (trace) => trace.gateway?.records || [];
const intentRecord = (trace) => gwRecords(trace).find((r) => r.action === "INTENT");
const admissionRecord = (trace) => gwRecords(trace).find((r) => /reach/.test(r.reason || "") || ["AUTHORIZED", "NOT_AUTHORIZED"].includes(r.decision) && r.action !== "INTENT");
const exchangeRecord = (trace) => {
  // The Token Service names the actor by its Auth0 subject (<client_id>@clients), which the delegated token's act.sub repeats.
  const all = trace.tokens.token_audit || [];
  const actor = trace.tokens?.delegated?.act?.sub;
  return (actor && all.find((r) => r.decision === "TOKEN_EXCHANGED" && r.actor === actor)) || all.find((r) => r.decision === "TOKEN_EXCHANGED");
};

// The pipeline notes name the persona by first name: an Auth0 subject does not read at that size.
const firstName = (trace) => (trace.persona?.name || trace.persona?.id || "").split(" ")[0];

function paintPipeline(trace) {
  const t = trace.tokens;
  const gw = trace.gateway || {};
  const who = firstName(trace);
  setStep("issue", t.subject ? "done" : "skip", t.subject ? `${who} · aud ${audList(t.subject.aud)}` : "no token sent");
  if (gw.transport_error) {
    setStep("introspect", "fail", "gateway unreachable");
    STEPS.slice(2, 9).forEach((s) => setStep(s, "skip"));
    setStep("audit", "skip");
    return;
  }
  if (gw.kind === "refused" && gw.http_status === 401) {
    setStep("introspect", "fail", `HTTP 401 · ${gw.error_text || "refused at the bridge"}`);
    STEPS.slice(2, 9).forEach((s) => setStep(s, "skip"));
    setStep("audit", gwRecords(trace).length ? "done" : "warn", gwRecords(trace).length ? "refusal audited" : "no gateway record");
    return;
  }
  setStep("introspect", "done", "active at the bridge");
  const ex = exchangeRecord(trace);
  setStep("exchange", ex ? "done" : "warn", ex ? `${who} → act ${ex.actor}` : "no Token Service record");
  const adm = admissionRecord(trace);
  const intent = intentRecord(trace);
  setStep("whoami", adm || intent ? "done" : gw.kind === "refused" ? "fail" : "warn", adm || intent ? `${trace.persona.type} ${who}` : gw.error_text || "no record");
  if (adm) setStep("admission", adm.decision === "AUTHORIZED" ? "done" : "fail", adm.decision === "AUTHORIZED" ? "may reach the agent" : "may not reach the agent");
  else setStep("admission", gw.http_status === 403 ? "fail" : "warn", gw.http_status === 403 ? "HTTP 403" : "no record");
  if (intent) setStep("intent", intent.decision === "INTENT_FOUND" ? "done" : intent.decision === "INTENT_FAILED" ? "warn" : "fail", `${intent.decision.replace("INTENT_", "").toLowerCase()} · ${intent.durationMs ?? "—"} ms`);
  else setStep("intent", gw.kind === "intent_error" ? "warn" : "skip", gw.kind === "intent_error" ? "no intent" : "not asked");
  if (gw.kind === "task") setStep("forward", "done", `${gw.skill || "agent"} · ${gw.latency_ms} ms`);
  else if (gw.kind === "intent_error") setStep("forward", "skip", "gateway answered");
  else setStep("forward", "fail", gw.error_text || "not forwarded");
  paintIbacSteps(trace.ibac);
  const n = gwRecords(trace).length;
  const ibacRecords = trace.ibac ? trace.ibac.records.length : 0;
  setStep("audit", n || trace.audit ? "done" : "warn", `${n} gateway${trace.audit ? " · agent" : ""}${ibacRecords ? ` · +${ibacRecords} preview` : ""}`);
}

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
  const gw = trace.gateway || {};
  const verdict = $("#verdict");
  const meta = $("#resultMeta");
  $("#tickets").replaceChildren();
  $("#refusal").hidden = true;
  const through = agentName(trace.agent);

  if (gw.transport_error) {
    verdict.className = "result-verdict error";
    verdict.textContent = `The gateway of ${through} could not be reached.`;
  } else if (gw.kind === "refused") {
    verdict.className = "result-verdict error";
    verdict.textContent = gw.http_status === 403
      ? `Admission refused by the gateway of ${through}.`
      : `The gateway of ${through} refused the token.`;
    $("#refusal").hidden = false;
    $("#refusal").replaceChildren(el("b", "", `HTTP ${gw.http_status}`), el("div", "", gw.error_text || ""));
  } else if (gw.kind === "error") {
    verdict.className = "result-verdict error";
    verdict.textContent = `The gateway of ${through} answered an error.`;
    $("#refusal").hidden = false;
    $("#refusal").replaceChildren(el("b", "", `HTTP ${gw.http_status ?? "—"}`), el("div", "", gw.error_text || ""));
  } else if (gw.kind === "intent_error") {
    verdict.className = "result-verdict refused";
    verdict.textContent = `No intent: the gateway of ${through} answered the caller itself.`;
    const p = gw.intent_error || {};
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
    const n = a?.payload?.evaluations?.length ?? 0;
    verdict.className = "result-verdict ok";
    verdict.textContent = n
      ? `Forwarded to ${through}: ${n} evaluation${n === 1 ? "" : "s"} extracted.`
      : `Forwarded to ${through}.`;
    if (n) renderTickets(a.payload.evaluations, trace.decisions);
  }
  const bits = [];
  bits.push(`via iag-${trace.agent}`, `HTTP ${gw.http_status ?? "—"}`, `${gw.latency_ms ?? "—"} ms`);
  if (gw.kind) bits.push(gw.kind.replace("_", " "));
  if (trace.audit?.record) bits.push(`agent audit ${trace.audit.record.outcome}`);
  if (trace.ibac) bits.push(`preview ${trace.ibac.summary.matched_agents.length}/${trace.ibac.summary.agents} cards`);
  bits.push(`req ${trace.request_id.slice(0, 8)}`);
  meta.textContent = bits.filter(Boolean).join("  ·  ");
  renderActors(trace);
  renderChain(trace);
  // The second part exists once the gateway answered at all; an unreachable gateway ends at part 1.
  $("#zoneIbac").hidden = !!gw.transport_error;
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
  const gw = trace.gateway || {};
  const a = trace.a2a;
  if (gw.transport_error) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.replaceChildren();
  const head = el("div", "handoff-head");
  head.append(
    el("h2", "", "From prompt to agent: the handoffs"),
    el("p", "", "What the gateway did on the way in, what left the Intent Agent, what the console's mapping preview takes and gives back, and what the gateway did on the way out. The gateway stages are read from its own records; the values are this prompt's."),
  );
  box.append(head);
  const lane = el("div", "lane lane-5");
  const rec = trace.audit?.record;
  const payload = a?.payload || {};
  const ib = trace.ibac;
  const n = (payload.evaluations || []).length;
  const gwName = `iag-${trace.agent}`;
  const ex = exchangeRecord(trace);
  const adm = admissionRecord(trace);
  const intent = intentRecord(trace);

  // 1. the gateway, on the way in
  const inRows = [
    ["token", gw.http_status === 401 ? "refused at the bridge" : trace.auth_mode === "none" ? "none presented" : "active at the bridge (Auth0)"],
    ["exchange", ex ? `delegated token: ${ex.subject} acting through ${ex.actor}` : "no exchange"],
    ["whoami", adm || intent ? `${trace.persona.type} ${trace.persona.id}` : "not resolved"],
    ["admission", adm ? `${adm.decision}: ${adm.reason}` : gw.http_status === 403 ? "refused (403)" : "not recorded"],
    ["intent", intent ? `${intent.decision} · ${intent.durationMs ?? "—"} ms` : gw.kind === "intent_error" ? "no intent" : "not asked"],
  ];
  const inStatus = gw.kind === "refused" ? "fail" : gw.kind === "intent_error" ? "warn" : "ok";
  lane.append(stage("gateway", `${gwName} · in`, gw.kind === "refused" ? (gw.http_status === 403 ? "admission refused" : "token refused") : "caller admitted", inRows, inStatus));
  lane.append(arrow(gw.kind === "refused" ? "stopped here" : "the prompt, with the subject", gw.kind === "refused" ? "fail" : "ok"));

  // 2. the Intent Agent, out
  if (a?.outcome === "success") {
    lane.append(stage("agent", "Intent Agent · out", `intent with ${n} evaluation${n === 1 ? "" : "s"}`, [
      ["artifact", "{ prompt, evaluations[] }, read from the agent's audit record"],
      ["bound subject", `${trace.persona.type} ${trace.persona.id}, set by the gateway from whoami`],
      ["vocabulary", rec ? `schema ${short(rec.schema_version)} · policies ${short(rec.policy_version)}` : "schema + policies of the project"],
      ["audit", rec ? `${rec.outcome} · ${rec.latency_ms} ms` : "record pending"],
    ], "ok"));
    lane.append(arrow("the intent, as JSON", "ok"));
  } else if (gw.kind === "intent_error") {
    lane.append(stage("agent", "Intent Agent · out", "no intent: task failed", [
      ["artifact", "{ prompt, error, reason, obligation }"],
      ["error", payload.error || "Unable to extract intent from prompt."],
      ["obligation", payload.obligation || "—"],
    ], "fail"));
    lane.append(arrow("nothing to hand over", "skip"));
  } else {
    lane.append(stage("agent", "Intent Agent", "not reached", [["why", gw.kind === "refused" ? "the gateway stopped before asking" : "no record of a call", "muted"]], "skip"));
    lane.append(arrow("", "skip"));
  }

  // 3-4. the console's mapping preview
  if (ib) {
    const params = ib.params || {};
    const named = [...new Set((params.evaluations || []).flatMap((e) => Object.keys(e.parameters || {})))];
    const matched = ib.summary.matched_agents;
    const searches = ib.usage.filter((u) => u.reason_code === "intent_match_search").length;
    lane.append(stage("ibac", "mapping preview · in", "three inputs", [
      ["intent", `${n} evaluation${n === 1 ? "" : "s"}: subject type, action, resource type each`],
      ["cards", `${ib.summary.agents} agent cards, ${ib.discovery.reduce((s, d) => s + d.skill_ids.length, 0)} skills with their x-coaz-mappings`],
      ["params", `token.sub = ${params.token?.sub || "—"}${named.length ? ` · parameters: ${named.join(", ")}` : " · no parameters in the intent"}`],
    ], "ok"));
    lane.append(arrow("discovery, then usage", "ok"));
    const skipped = ib.summary.skipped_agents || [];
    const thisAgent = skipped.includes(trace.agent)
      ? [`${agentName(trace.agent)} publishes no mapping: check skipped (allow), KBAC alone decides`, ""]
      : matched.includes(trace.agent)
        ? [`${agentName(trace.agent)} declares a skill for it`, ""]
        : [`${agentName(trace.agent)} declares no skill for it`, "muted"];
    lane.append(stage("ibac", "mapping preview · out", matched.length ? `${matched.length} of ${ib.summary.agents} cards declare it` : "no card declares it", [
      ["discovery", `${matched.length ? `match: ${matched.join(", ")}` : "no match on any card"}${skipped.length ? ` · skipped: ${skipped.join(", ")}` : ""}`],
      ["usage", ib.usage.length ? `as a tool call: ${ib.usage.map((u) => `${u.skill} → ${u.reason_code}`).join(" · ")}` : "not run"],
      ["requests", ib.summary.requests ? `${ib.summary.requests} AuthZEN request${ib.summary.requests === 1 ? "" : "s"}` : searches ? `none: ${searches} search match${searches === 1 ? "" : "es"}` : "none"],
      ["this agent", ...thisAgent],
    ], matched.length ? "ok" : "fail"));
    lane.append(arrow("what the gateway would check", matched.length ? "ok" : "fail"));
  } else {
    lane.append(stage("ibac", "mapping preview", "not run", [["why", "no intent to compare with the cards", "muted"]], "skip"));
    lane.append(arrow("", "skip"));
    lane.append(stage("ibac", "mapping preview · out", "—", [["decision", "none", "muted"]], "skip"));
    lane.append(arrow("", "skip"));
  }

  // 5. the gateway, on the way out
  const recv = gw.received || {};
  let outTitle;
  let outRows;
  let outStatus;
  if (gw.kind === "task") {
    outTitle = `forwarded to ${agentName(trace.agent)}`;
    outStatus = "ok";
    outRows = [
      ["Authorization", recv.authorization ? "the caller's token, untouched" : "—"],
      ["X-IK-Token", recv.x_ik_token?.sub ? `${recv.x_ik_token.sub} ← ${actChain(recv.x_ik_token.act)}` : "—"],
      ["intent in token", recv.x_ik_token?.sub ? intentRow(recv.x_ik_token.act) : "—", intentInToken(recv.x_ik_token?.act) ? "" : "muted"],
      ["A2A-Extensions", recv.a2a_extensions || "not sent"],
      ["agent reply", gw.reply_text ? gw.reply_text.slice(0, 160) + (gw.reply_text.length > 160 ? "…" : "") : "—"],
      ["skill", gw.skill || "—"],
    ];
  } else if (gw.kind === "intent_error") {
    outTitle = "answered the caller itself";
    outStatus = "warn";
    outRows = [
      ["reply", gw.reply_text || "the Intent Agent's error and obligation"],
      ["metadata", "intent_error: prompt, error, reason, obligation"],
      ["forwarded", "nothing: the agent was not called", "muted"],
    ];
  } else {
    outTitle = gw.http_status === 403 ? "refused: admission" : "refused: token";
    outStatus = "fail";
    outRows = [
      ["answer", `HTTP ${gw.http_status ?? "—"} · ${gw.error_text || ""}`],
      ["forwarded", "nothing: the agent was not called", "muted"],
    ];
  }
  outRows.push(["audit", `${gwRecords(trace).length} gateway record${gwRecords(trace).length === 1 ? "" : "s"}: ${[...new Set(gwRecords(trace).map((r) => r.decision))].join(", ") || "none yet"}`]);
  lane.append(stage("gateway", `${gwName} · out`, outTitle, outRows, outStatus));
  box.append(lane);

  const foot = el("div", "handoff-foot");
  foot.append(el("span", "", "One gateway protects one agent: it validated the caller's Auth0 token at the bridge, exchanged it for a delegated token at the Token Service, resolved the caller with whoami, decided admission with one AuthZEN evaluation over the delegation chain, asked the Intent Agent about the prompt, and forwarded the message with the delegated token and its extension header. The mapping preview is the console's own check of this gateway's card; the released gateway does not read it yet. Nothing routes between gateways: to see what the others do with the same prompt, send it through them, on the board below."));
  box.append(foot);
}

// ---------- actor strip: who talked to whom for this prompt ----------

const ACTORS = [
  ["console", "console", "login · A2A client"],
  ["idp", "auth0-bridge", "introspection · client tokens → Auth0"],
  ["gateway", "gateway", "Agent Gateway"],
  ["ts", "token-service", "RFC 8693 exchange"],
  ["platform", "platform", "whoami · AuthZEN"],
  ["agent", "intent-agent", "extracts the intent"],
  ["target", "agent", "the protected agent"],
  ["ibac", "preview", "mapping check (console)"],
];

function renderActors(trace) {
  const wrap = $("#actors");
  wrap.replaceChildren();
  const t = trace.tokens;
  const gw = trace.gateway || {};
  const rec = trace.audit?.record;
  const ms = (v) => (v == null ? "—" : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${v} ms`);
  const gwName = `iag-${trace.agent}`;

  const header = el("div", "actors-head");
  for (const [key, name, role] of ACTORS) {
    const chip = el("div", `actor actor-${key}`);
    chip.append(el("b", "", key === "gateway" ? gwName : key === "target" ? agentName(trace.agent) : name), el("small", "", role));
    header.append(chip);
  }
  wrap.append(header);

  const hops = [];
  // 1. the message to the gateway
  if (gw.transport_error) hops.push(["console", "gateway", "SendMessage (A2A JSON-RPC)", "unreachable", "fail"]);
  else hops.push(["console", "gateway", "SendMessage (A2A JSON-RPC)", `HTTP ${gw.http_status} · ${ms(gw.latency_ms)} · ${t.bearer_kind}`, gw.http_status < 400 ? "ok" : "fail"]);
  const reached = !gw.transport_error;
  // 2. introspection of the caller's token at the bridge (verified against the tenant's keys)
  if (!reached) hops.push(["gateway", "idp", "/oauth/introspect · RFC 7662", "—", "skip"]);
  else if (gw.http_status === 401) hops.push(["gateway", "idp", "/oauth/introspect · RFC 7662", trace.auth_mode === "none" ? "no token to introspect" : `inactive or wrong audience · ${t.bearer_kind}`, "fail"]);
  else hops.push(["gateway", "idp", "/oauth/introspect · RFC 7662", `active · ${t.subject?.sub || "—"} · aud ${audList(t.subject?.aud)}`, "ok"]);
  // 3. client credentials for the gateway itself (actor) and for the Intent Agent: the bridge forwards them to Auth0
  hops.push(["gateway", "idp", "/oauth/token · client_credentials → Auth0", reached && gw.http_status !== 401 ? "the gateway's own M2M token (kept until it expires)" : "not reached", reached && gw.http_status !== 401 ? "ok" : "skip"]);
  // 4. exchange at the Token Service
  const ex = exchangeRecord(trace);
  hops.push(["gateway", "ts", "/oauth2/token · RFC 8693 exchange", ex ? `${ex.decision} · ${ex.subject} → act ${ex.actor}` : "no record", ex ? "ok" : "skip"]);
  // 5. whoami + 6. admission
  const adm = admissionRecord(trace);
  const intent = intentRecord(trace);
  hops.push(["gateway", "platform", "ContX IQ whoami", adm || intent ? `${trace.persona.type} ${trace.persona.id}` : gw.http_status === 401 ? "not reached" : "no record", adm || intent ? "ok" : "skip"]);
  hops.push(["gateway", "platform", "AuthZEN admission ($ik_token.act_chain)", adm ? `${adm.decision} · ${adm.reason}` : gw.http_status === 403 ? "refused" : "no record", adm ? (adm.decision === "AUTHORIZED" ? "ok" : "fail") : gw.http_status === 403 ? "fail" : "skip"]);
  // 7. the Intent Agent
  hops.push(["gateway", "agent", "SendMessage · the prompt (ibac.enabled)", intent ? `${intent.decision} · ${ms(intent.durationMs)}` : gw.kind === "intent_error" ? "no intent" : "not asked", intent ? (intent.decision === "INTENT_FOUND" ? "ok" : "warn") : "skip"]);
  hops.push(["agent", "console", "audit record (webhook)", rec ? `${rec.outcome} · ${rec.llm_model} · ${rec.validation_retries} retr${rec.validation_retries === 1 ? "y" : "ies"}` : intent ? "not received yet" : "—", rec ? "ok" : intent ? "warn" : "skip"]);
  // 8. the forward
  const recv = gw.received || {};
  if (gw.kind === "task") hops.push(["gateway", "target", "SendMessage · X-IK-Token · A2A-Extensions", `${gw.skill || "handled"} · ${recv.a2a_extensions ? "extension declared" : "no extension header"}`, "ok"]);
  else hops.push(["gateway", "target", "SendMessage · X-IK-Token · A2A-Extensions", gw.kind === "intent_error" ? "not forwarded: the gateway answered" : "not forwarded", gw.kind === "intent_error" ? "warn" : "skip"]);
  // 9. the preview and the decisions
  const ib = trace.ibac;
  if (ib) hops.push(["console", "ibac", "mapping preview: discovery + usage", `${ib.summary.agents} cards · ${ib.summary.matched_agents.length} matched${ib.summary.requests ? ` · ${ib.summary.requests} request${ib.summary.requests === 1 ? "" : "s"}` : ""}`, ib.summary.matched_agents.length ? "ok" : "warn"]);
  else hops.push(["console", "ibac", "mapping preview: discovery + usage", "no intent to match", "skip"]);
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
    const label = (k) => (k === "gateway" ? gwName : k === "target" ? agentName(trace.agent) : ACTORS.find((x) => x[0] === k)[1]);
    row.append(
      el("span", "hop-n", String(i + 1)),
      el("span", `hop-actor actor-${from}`, label(from)),
      el("span", "hop-arrow", "→"),
      el("span", `hop-actor actor-${to}`, label(to)),
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

// The Token Service writes the intent of a hop, when the exchange carries one, as act.intent.evaluations
// of that hop. The released gateway does not send it yet, so the row says so until it does.
function intentInToken(act) {
  for (let a = act; a && typeof a === "object"; a = a.act) {
    if (Array.isArray(a.intent?.evaluations)) return a.intent.evaluations;
  }
  return null;
}

function intentRow(act) {
  const evs = intentInToken(act);
  if (!evs) return "none: the evaluations stay out of the token";
  const what = evs.map((e) => `${e.action?.name || "any"} ${e.resource?.type || ""}`.trim()).join(" · ");
  return `${evs.length} evaluation${evs.length === 1 ? "" : "s"} in act.intent${what ? `: ${what}` : ""}`;
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
  const subj = t.subject;
  wrap.append(
    subj
      ? tokenCard("person's token", "Auth0", trace.auth_mode === "delegation" ? "ok" : "warn", [
          ["iss", subj.iss],
          ["sub", subj.sub],
          ["aud", audList(subj.aud)],
          ["ttl", ttl(subj)],
          ["sent as", t.bearer_kind],
        ])
      : tokenCard("person's token", "Auth0", "dim", [["status", "no token sent"]]),
    el("div", "tok-arrow", "→"),
  );
  const actor = t.delegated?.act?.sub;
  wrap.append(
    tokenCard("gateway client token", "Auth0 · M2M", actor ? "ok" : "dim", actor
      ? [["sub", actor], ["aud", "ibac-agents"], ["status", "obtained through the bridge; the console never sees it"]]
      : [["status", "the gateway's own token: not visible to the console"]]),
    el("div", "tok-arrow", "→"),
  );
  const d = t.delegated;
  if (d && d.sub) {
    wrap.append(
      tokenCard("delegated token", "Token Service", "ok", [
        ["iss", d.iss],
        ["sub", d.sub],
        ["act", actChain(d.act)],
        ["intent", intentRow(d.act)],
        ["aud", audList(d.aud)],
        ["ttl", ttl(d)],
        ["jti", shortId(d.jti)],
      ]),
    );
  } else {
    const ex = exchangeRecord(trace);
    wrap.append(tokenCard("delegated token", "Token Service", ex ? "warn" : "dim", ex
      ? [["exchange", `${ex.decision} · ${ex.subject} → act ${ex.actor}`], ["status", "minted, the agent did not report it"]]
      : [["status", "no exchange for this prompt"]]));
  }
  wrap.append(el("div", "tok-arrow", "→"));
  const audit = tokenCard("issuer records", "Token Service", t.token_audit.length ? "ok" : "dim", []);
  const rows = t.token_audit.map((r) => ({ ...r, who: "Token Service" }));
  if (!rows.length) audit.append(el("div", "tok-row", "no record yet"));
  for (const r of rows) {
    const row = el("div", "tok-row tok-audit");
    const good = ["TOKEN_EXCHANGED", "INTROSPECTED", "TOKEN_ISSUED"].includes(r.decision);
    row.append(el("span", `k ${good ? "good" : "bad"}`, r.decision), el("span", "v", `${r.who} · ${r.action || ""} · ${r.subject || "—"}${r.actor ? ` via ${r.actor}` : ""}${r.reason ? ` · ${r.reason}` : ""}`));
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
  cp.replaceChildren(headline("Console", "the login and the A2A client"));
  cp.append(
    kv([
      ["login", `Auth0 · ${cfg.auth0_issuer}`],
      ["application", cfg.console_idp_client_id],
      ["people's API", cfg.people_audience],
      ["Intent Agent aud", cfg.intent_agent_audience],
      ["preview actor", cfg.console_client_id],
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
  const gw = t.gateway || {};
  const a = t.a2a || {};
  const tabs = {
    request: gw.request ? `POST ${gw.url}\n${Object.entries(gw.request_headers || {}).map(([k, v]) => `${k}: ${v}`).join("\n")}\n\n${pretty(gw.request)}` : "no request was sent",
    envelope: gw.envelope ? pretty(gw.envelope) : gw.body_text || gw.error_text || "—",
    payload: a.payload ? pretty(a.payload) : "no intent (the gateway stopped before the Intent Agent, or the agent found none)",
    gateway: pretty({ kind: gw.kind, http_status: gw.http_status, latency_ms: gw.latency_ms, records: gw.records, received_by_agent: gw.received, agent_reply: gw.reply_text, intent_error: gw.intent_error }),
    ibac: t.ibac ? pretty(t.ibac) : "no intent reached the mapping preview",
    compare: state.compare ? pretty(state.compare) : "not compared yet: on the board, press \"send through the other gateways\" to ask each gateway for real",
    tokens: pretty({
      bearer: t.tokens.bearer_kind,
      person_token_claims: t.tokens.subject,
      delegated_token_claims_as_received_by_the_agent: t.tokens.delegated,
      token_service_records: t.tokens.token_audit,
    }),
    audit: t.audit ? pretty(t.audit.record) : "no Intent Agent record arrived within 5 s (check the Audit view later)",
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
  else if (entry.source === "agent-gateway") msg = `${rec.service} · ${rec.action === "noop" ? "admission" : rec.action} — ${rec.subject || "—"}${rec.actor ? ` via ${rec.actor}` : ""} · ${rec.reason || ""}${rec.durationMs ? ` · ${rec.durationMs} ms` : ""}${rec.prompt ? ` · "${rec.prompt}"` : ""}`;
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
  missing_coaz_mapping: "the card publishes no x-coaz-mappings - no intent can ever match it (the gateway asks the engine about such a card only under the deny setting)",
  ibac_skipped: "the card publishes no x-coaz-mappings and the setting is allow: the engine was not asked, KBAC alone decides",
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
  if (d.skipped) return "publishes no mappings: the check is skipped under the allow setting, KBAC alone decides for it";
  if (d.reason_code === "missing_coaz_mapping") return "publishes no mappings, so no intent can ever match it (deny setting)";
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
    // Nothing refused ever reaches the preview: say so instead of hiding the section.
    const a = trace.a2a;
    if (!a || a.outcome === "error") {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    $("#ibacLede").textContent = "The mapping preview was not run: the Intent Agent produced no intent for this prompt, so there is nothing to compare with the card. The gateway answered the caller itself, before its agent was involved.";
    $("#ibacSummary").replaceChildren(stat("—", "no intent"));
    $("#ibacPrimary").replaceChildren();
    $("#ibacPrimary").hidden = true;
    $(".ibac-compare-head").hidden = true;
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
  $("#ibacPrimary").hidden = false;
  $(".ibac-compare-head").hidden = false;
  $("#ibacBoard").hidden = false;
  $("#ibacUsage").hidden = false;
  $("#ibacMatrixWrap").hidden = !state.fleet;
  const evaluations = trace.a2a.payload.evaluations;
  if (state.fleet) renderMatrix(ib, evaluations);
  const n = evaluations.length;
  const mine = ib.discovery.find((d) => d.agent === trace.agent) || ib.discovery[0];
  const others = ib.discovery.filter((d) => d !== mine);
  const matched = ib.summary.matched_agents.length;
  const me = traceAnswer(trace);
  const through = agentName(trace.agent);

  const did = me.verdict === "forwarded"
    ? `The gateway of ${through} admitted the caller, got an intent with ${n} evaluation${n === 1 ? "" : "s"} and forwarded the message.`
    : me.verdict === "no intent"
      ? `The gateway of ${through} got no intent and answered the caller itself.`
      : `The gateway of ${through} refused the call (${me.verdict}).`;
  $("#ibacLede").textContent = mine.skipped
    ? `${did} Its card publishes no mappings. Under the gateway setting "no mapping: allow", the default, the mapping check is skipped for it and KBAC alone decides, which is what the admission above did. Switch the setting to deny and send again to see the preview refuse it.`
    : mine.decision
      ? `${did} The console's preview of its card says it declares a skill for this intent (${mine.matched_skills.join(", ")}); this is the check the gateway will run against its own card once it reads the mappings. Below, what a tool call on the matched skill would then be checked against.`
      : `${did} The console's preview of its card says it declares no skill for this intent: a gateway reading its mappings would refuse here. Today's release forwards on admission and intent alone. Click a skill to see why nothing matched.`;
  const skipped = (ib.summary.skipped_agents || []).length;
  $("#ibacSummary").replaceChildren(
    stat(me.verdict, "this gateway did", me.verdict === "forwarded" ? "ok" : me.verdict === "no intent" ? "" : "fail"),
    stat(mine.skipped ? "skipped" : mine.decision ? "declares" : "does not", mine.skipped ? "no mapping · allow" : "its card, in the preview", mine.decision ? "ok" : "fail"),
    stat(`${matched} / ${ib.summary.agents}`, "cards declare it (preview)", matched ? "ok" : "fail"),
  );
  if (skipped) $("#ibacSummary").append(stat(String(skipped), "unmapped · pass (allow)", ""));

  $("#ibacPrimary").replaceChildren(fleetCard(mine, trace, evaluations, me));
  const board = $("#ibacBoard");
  board.replaceChildren();
  for (const d of others) board.append(fleetCard(d, trace, evaluations, null));

  const btn = $("#compareBtn");
  btn.disabled = false;
  btn.textContent = `send through the other ${others.length} gateways`;
  btn.onclick = () => compareFleet(trace);
  $("#compareNote").textContent = `${others.length} more Intent Agent calls; each gateway answers for its own agent`;
  if (state.compare && state.compare.prompt === trace.a2a.payload.prompt && state.compare.persona.id === trace.persona.id) applyCompare(state.compare);

  const first = ib.usage.find((u) => u.agent === mine.agent) || ib.usage[0];
  if (first) openUsage(first.agent, first.skill);
  else {
    state.ibacPick = null;
    $("#ibacUsage").replaceChildren(el("div", "empty", "No skill matched: nothing to preview as a tool call. Click any skill above to run usage on it anyway and read the reason code."));
  }
}

// One card of the board: the preview's verdict on its mappings, what its gateway did (or a slot for it), its skills.
function fleetCard(d, trace, evaluations, answer) {
  const primary = answer !== null;
  const card = el("article", `fa fa-${d.accent} ${d.skipped || d.reason_code === "missing_coaz_mapping" ? "nomap" : d.decision ? "matched" : "unmatched"}${primary ? " primary" : ""}`);
  const head = el("div", "fa-head");
  const title = el("b", "", d.capability);
  title.append(el("span", "mono muted", ` · iag-${d.agent}`));
  head.append(title, el("span", `pill ${d.skipped ? "skip" : d.decision ? "yes" : "no"}`, d.skipped ? "NO MAPPING · PASSES" : d.decision ? "DECLARES IT" : "NOT DECLARED"));
  card.append(head);
  if (primary) card.append(gwBlock(answer, true));
  else {
    const slot = el("div", "fa-gw pending");
    slot.dataset.agent = d.agent;
    slot.append(el("b", "", "its gateway"), el("span", "", "not asked yet"), el("small", "", "the product answers per gateway: send the prompt through it to know"));
    card.append(slot);
  }
  const you = el("div", "fa-you");
  you.dataset.agent = d.agent;
  card.append(you);
  card.append(el("div", "fa-preview-tag", primary ? "mapping preview · this gateway's card" : "mapping preview · its card"), el("div", "fa-reason mono", d.reason_code), el("div", "fa-verdict", verdictFor(d, evaluations)));
  if (!primary) {
    const b = el("button", "fa-send", `make ${d.capability.replace(" Agent", "")} the gateway`);
    b.title = "Send the same prompt through this agent's gateway and read the full trace (one more Intent Agent call)";
    b.addEventListener("click", () => sendPrompt($("#promptText").value || trace.a2a?.payload?.prompt || "", d.agent));
    card.append(b);
  }
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
  queueMicrotask(refreshBoardMarks);
  return card;
}

// What the gateway of the current trace did, in the shape /api/compare returns for the others.
function traceAnswer(trace) {
  const gw = trace.gateway || {};
  const adm = admissionRecord(trace);
  const intent = intentRecord(trace);
  const delegated = trace.tokens?.delegated;
  const chain = [];
  if (delegated?.sub) {
    chain.push(delegated.sub);
    let act = delegated.act;
    while (act && act.sub) {
      chain.push(act.sub);
      act = act.act;
    }
  }
  const failure = gwRecords(trace).find((r) => r.decision === "ERROR");
  const verdict = gw.transport_error ? "unreachable" : failure ? "admission error" : gw.kind === "task" ? "forwarded" : gw.kind === "intent_error" ? "no intent" : gw.kind === "refused" ? (gw.http_status === 403 ? "admission refused" : "token refused") : "error";
  return {
    agent: trace.agent, verdict, kind: gw.kind, http_status: gw.http_status, latency_ms: gw.latency_ms, skill: gw.skill,
    error_text: failure?.reason || gw.error_text || gw.transport_error || "",
    admission: adm ? { decision: adm.decision, reason: adm.reason } : null,
    intent: intent ? { decision: intent.decision, duration_ms: intent.durationMs } : null,
    chain,
  };
}

// The product's answer for one card: verdict, then admission, intent and chain from the gateway's own records.
function gwBlock(a, primary) {
  const kind = a.verdict === "forwarded" ? "ok" : a.verdict === "no intent" ? "warn" : "fail";
  const block = el("div", `fa-gw ${kind}`);
  block.dataset.agent = a.agent;
  const line = a.verdict === "forwarded"
    ? `forwarded · ${a.skill || "agent"} · HTTP ${a.http_status}`
    : a.verdict === "no intent"
      ? "no intent · the gateway answered the caller"
      : `${a.verdict} · HTTP ${a.http_status ?? "—"}${a.error_text ? ` · ${a.error_text.slice(0, 60)}` : ""}`;
  block.append(el("b", "", primary ? "sent through this gateway" : "its gateway"), el("span", "", line));
  const bits = [];
  if (a.admission) bits.push(`admission ${a.admission.decision}`);
  if (a.intent) bits.push(`intent ${a.intent.decision.replace("INTENT_", "").toLowerCase()}`);
  if (a.chain?.length) bits.push(`chain ${a.chain.join(" ← ")}`);
  if (bits.length) block.append(el("small", "", bits.join(" · ")));
  return block;
}

// Ask every other gateway the same prompt and fill its card with what it did.
async function compareFleet(trace) {
  const btn = $("#compareBtn");
  if (btn.disabled || !trace.ibac) return;
  const others = trace.ibac.discovery.map((d) => d.agent).filter((a) => a !== trace.agent);
  btn.disabled = true;
  $("#compareNote").textContent = `sending through ${others.length} gateways…`;
  for (const slot of document.querySelectorAll("#ibacBoard .fa-gw")) {
    slot.className = "fa-gw pending";
    slot.replaceChildren(el("b", "", "its gateway"), el("span", "", "sending…"));
  }
  try {
    const res = await fetch("/api/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: trace.a2a.payload.prompt, auth_mode: trace.auth_mode, agents: others }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail ? JSON.stringify(data.detail) : res.statusText);
    if (state.trace !== trace) return;
    state.compare = data;
    applyCompare(data);
    if (state.wireTab === "compare") renderWire();
  } catch (err) {
    $("#compareNote").textContent = `comparison failed: ${err}`;
    for (const slot of document.querySelectorAll("#ibacBoard .fa-gw.pending")) slot.replaceChildren(el("b", "", "its gateway"), el("span", "", "not asked"));
  } finally {
    btn.disabled = false;
    refreshHealth();
  }
}

function applyCompare(data) {
  for (const a of data.answers) {
    const slot = document.querySelector(`#ibacBoard .fa-gw[data-agent="${a.agent}"]`);
    if (slot) slot.replaceWith(gwBlock(a, false));
  }
  const count = (v) => data.answers.filter((a) => a.verdict === v).length;
  const parts = [`${count("forwarded")} forwarded`];
  if (count("no intent")) parts.push(`${count("no intent")} no intent`);
  const refused = data.answers.filter((a) => /refused/.test(a.verdict)).length;
  if (refused) parts.push(`${refused} refused`);
  const rest = data.answers.length - count("forwarded") - count("no intent") - refused;
  if (rest) parts.push(`${rest} error`);
  $("#compareNote").textContent = `${data.answers.length} gateways asked · ${parts.join(" · ")}`;
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
            token_claims: trace.tokens.delegated || {},
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
  title.append(el("span", "muted", "usage, as a tool call · "), document.createTextNode(`${u.capability} / `), el("span", "mono", u.skill));
  head.append(title, el("span", `pill ${u.decision ? "yes" : "no"}`, u.decision ? "AUTHORIZED" : "NOT_AUTHORIZED"), el("span", "mono reason", u.reason_code));
  if (whatIf) head.append(el("span", "tag", "what if: discovery did not pick this skill"));
  wrap.append(head, el("p", "usage-why", REASON_TEXT[u.reason_code] || u.reason || ""));
  wrap.append(el("p", "usage-note", "An A2A message has no usage step: it names no capability and carries no arguments. This is what a tool call on this skill would be checked against, with the same intent; the call's arguments are stood in by the parameters the Intent Agent extracted, the token claims are the delegated token's."));
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
      body.append(el("div", "req-note mono", `context.input_params = the params map (${Object.keys(r.context?.input_params || {}).join(", ")})`));
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
  p.append(el("h4", "", "what the $references resolved from: request and token, nothing from the intent"));
  const rows = [
    ["$token.sub", params.token?.sub],
    ["$token.aud", audList(params.token?.aud)],
    ["$token.act.sub", params.token?.act?.sub],
  ];
  let anyNamed = false;
  (params.evaluations || []).forEach((e, i) => {
    Object.entries(e.parameters || {}).forEach(([k, v]) => {
      anyNamed = true;
      rows.push([`$request.arguments.${k}`, `${JSON.stringify(v)}  (stand-in: parameter of evaluation ${String(i + 1).padStart(2, "0")})`]);
    });
  });
  if (!anyNamed) rows.push(["$request.arguments.*", "none: the intent named no parameter to stand in for an argument"]);
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
// The params map of one evaluation, as the engine saw it: that evaluation's parameters as the call's arguments, the token.
const paramsFor = (params, i) => ({ request: { arguments: params?.evaluations?.[i]?.parameters || {} }, token: params?.token || {} });

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
  renderFleetYou();
  if (state.me && !state.myCards) loadMyCards();
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
      const kind = useMark(a.id, s.id);
      const sk = el("div", `skill${kind === "no" ? " hidden-for-you" : ""}`);
      const h = el("div", "skill-head");
      h.append(el("b", "", s.name), el("span", "mono muted", s.id));
      if (state.me) h.append(markNode(kind, a.id));
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
    const listings = await Promise.all(["Person"].map((type) => fetch(`/api/policies?subject_type=${type}`).then((r) => (r.ok ? r.json() : Promise.reject(r)))));
    listings.forEach((listing) => {
      (listing.policies || []).forEach((pol, i) => {
        if (!known.has(pol.subject_type) || !known.has(pol.resource_type)) return;
        pol.actions.forEach((action, j) => edges.push({ data: { id: `p${pol.subject_type}${i}${j}`, source: pol.subject_type, target: pol.resource_type, label: action, kind: "policy", cypher: pol.cypher, params: pol.params } }));
      });
    });
    state.policies.Person = listings[0];
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
        nodes.push({ data: { id: nid, label: id, kind: "resource", size: 30, type: fact.resource_type, externalId: id, identity: fact.resource_type === "Person" } });
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
    ["Person"].forEach((type) => (state.policies[type]?.policies || []).forEach((pol) => {
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
    g.persona = state.persona || state.config.personas[0];
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
