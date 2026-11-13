// Control-room console: one page, three live streams (the wire, the missions,
// the audit records) and the graph they act on.
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const SVG = 'http://www.w3.org/2000/svg';
  const state = {
    messages: [], audit: [], runs: [], escalations: [], heartbeat: {}, identity: {}, fed: [], catalog: [],
    graph: { nodes: [], edges: [] }, agents: {}, selectedRun: null, filter: 'all', drift: 0,
    known: new Set(), focus: null, pin: null, drawn: new Set(), once: new Map(), booting: true, bridge: {}, auditById: new Map(),
    layer: { open: false, filter: 'all', query: '', expanded: new Set() },
  };

  // ------------------------------------------------------------- helpers
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === false || v == null) continue;
      if (k === 'class') el.className = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v);
    }
    for (const child of children.flat()) {
      if (child == null || child === false) continue;
      el.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return el;
  }
  function s(tag, attrs, ...children) {
    const el = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v != null && v !== false) el.setAttribute(k, v);
    for (const child of children.flat()) if (child != null) el.append(child.nodeType ? child : document.createTextNode(String(child)));
    return el;
  }
  // ' fresh' the first time a row or card is drawn, so re-renders do not replay its entry animation.
  function fresh(key) {
    if (state.drawn.has(key)) return '';
    state.drawn.add(key);
    return state.booting ? '' : ' fresh';
  }
  // One-shot effects on the wall (the trigger's ping, a refusal's jolt). The wall is redrawn on every
  // record, so an effect is given its elapsed time as a negative delay and carries on instead of restarting.
  function effect(key, seconds) {
    if (state.booting) return null;
    if (!state.once.has(key)) state.once.set(key, performance.now());
    const elapsed = (performance.now() - state.once.get(key)) / 1000;
    return elapsed < seconds ? `--fx-delay:-${elapsed.toFixed(2)}s` : null;
  }
  const clock = (t) => new Date((t || 0) * 1000).toLocaleTimeString([], { hour12: false });
  const short = (v, n = 22) => (v && v.length > n ? `${v.slice(0, n - 1)}…` : v || '–');
  const DENIED = new Set(['not_authorized', 'deny', 'denied', 'error']);
  function tone(record) {
    const d = record.decision || '';
    if (d === 'token_exchanged') return 'token';
    if (DENIED.has(d) || /refus|fail|invalid/.test(d)) return 'deny';
    if (/authorized|allow|ok|success|exchanged|introspected|found/.test(d)) return 'ok';
    return 'info';
  }
  function toast(text, bad) {
    const el = $('toast');
    el.textContent = text;
    el.className = `toast${bad ? ' bad' : ''}`;
    el.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { el.hidden = true; }, 4200);
  }
  async function post(url) {
    // The header marks the request as the page's own action; another site cannot send it without a preflight.
    const resp = await fetch(url, { method: 'POST', headers: { 'X-Console-Action': '1' } });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(body.detail || `${resp.status}`);
    return body;
  }
  function openDrawer(title, sections) {
    $('drawerTitle').textContent = title;
    const body = $('drawerBody');
    body.replaceChildren();
    for (const [name, value] of sections) {
      if (value == null || value === '') continue;
      body.append(h('h4', null, name), h('pre', null, typeof value === 'string' ? value : JSON.stringify(value, null, 2)));
    }
    $('drawer').hidden = false;
  }

  // -------------------------------------------------------------- header
  function renderHeader() {
    const hb = state.heartbeat || {};
    const id = state.identity || {};
    $('factSub').textContent = short(state.agents[id.sub] || id.sub, 26);
    $('factSub').title = id.sub || '';
    $('factAud').textContent = Array.isArray(id.aud) ? id.aud.join(' ') : id.aud || '–';
    $('factPlanner').textContent = hb.planner || '–';
    $('factWire').textContent = hb.subscription ? `pub/sub · ${hb.subscription}` : 'direct feed';
    const b = state.bridge || {};
    $('factTokens').textContent = b.issued == null ? 'not reported by the bridge' : `${b.issued} issued by Auth0 · ${b.reused} reused`;
    $('agentCard').classList.toggle('busy', !!hb.busy || state.runs.some((r) => r.status === 'running'));
  }
  function tick() {
    const hb = state.heartbeat || {};
    const running = state.runs.find((r) => r.status === 'running');
    const line = $('agentLine');
    if (hb.error) line.textContent = `wire error: ${short(hb.error, 60)}`;
    else if (running) line.textContent = `on a mission: ${running.label || ''}`;
    else if (hb.next_wake) {
      const left = Math.max(0, hb.next_wake - (Date.now() / 1000 + state.drift));
      line.textContent = left > 0.5 ? `asleep · wakes in ${Math.ceil(left)}s` : 'awake · pulling the wire';
      const frac = Math.min(1, left / (hb.interval || 20));
      $('ringArc').style.strokeDashoffset = String(113.1 * frac);
    }
  }

  // ---------------------------------------------------------------- feed
  function renderFeed() {
    $('feed').replaceChildren(...state.catalog.map((event) => h('button', {
      class: `feed-item${state.fed.includes(event.id) ? ' fed' : ''}`, type: 'button', title: `Expected: the Sentinel ${event.expect.join('; ')}`,
      onclick: async (e) => {
        const item = e.currentTarget;
        item.classList.add('sending');
        try {
          const res = await post(`/api/feed/${event.id}`);
          if (res.delivery === 'blocked') toast(`Written to the graph, but the Sentinel cannot read its subscription: ${res.error}`, true);
          else toast(res.delivery === 'pubsub'
            ? 'Written to the graph. The platform publishes it; the Sentinel picks it up on its next pull.'
            : 'Written to the graph. No subscription configured: handed to the Sentinel directly.');
        } catch (err) { toast(`Feed failed: ${err.message}`, true); }
        item.classList.remove('sending');
      },
    }, h('strong', null, event.title), h('span', null, event.blurb),
    h('i', { class: 'go' }, state.fed.includes(event.id) ? 'written' : 'write'))));
  }

  // ---------------------------------------------------------------- wire
  function describe(m) {
    if (m.node) return `${m.node.type} ${m.node.external_id}`;
    const d = (m.body && m.body.data) || {};
    return d.decision != null ? `decision ${d.decision}` : '';
  }
  function renderWire() {
    const list = [...state.messages].reverse();
    $('wireCount').textContent = `${list.length} message${list.length === 1 ? '' : 's'}`;
    $('wireEmpty').hidden = list.length > 0;
    $('wire').replaceChildren(...list.map((m) => h('li', {
      class: `${m.trigger ? 'trigger' : ''}${m.stale ? ' stale' : ''}${fresh(`m:${m.id}`)}`,
      onclick: () => openDrawer(m.short, [['Event type', m.event_type], ['Published', m.time], ['Arrived by', m.source], ['Body', m.body]]),
    }, h('time', null, clock(m.at)),
    h('span', { class: 'type' }, m.short, m.trigger ? h('span', { class: 'tag' }, 'starts a mission') : null,
      m.stale ? h('span', { class: 'tag' }, 'old, ignored') : null),
    h('span', { class: 'what' }, describe(m)))));
  }

  // ---------------------------------------------------------------- wall
  const FLEET = {
    'Agent:sentinel': [70, 96], 'Workflow:wf-wall-control': [236, 62], 'Workflow:wf-deal-room': [236, 130],
    'Agent:orchestrator': [420, 96], 'Agent:retriever': [600, 62], 'Agent:analyst': [600, 130], 'Agent:mcp': [770, 96],
  };
  const DESKS = {
    'Desk:advisory': [58, 364], 'Desk:equities-trading': [940, 282], 'Desk:credit-trading': [940, 378],
    'Desk:compliance-control': [940, 470],
  };
  const GLYPH = { Agent: 'A', Company: 'C', Mandate: 'M', DealDocument: 'D', Restriction: 'R', TradeOrder: 'O', Hold: 'H', Desk: 'K' };
  const HIDDEN_EDGES = new Set(['COVERS', 'BOOKED_IN', 'RAISED', 'HAS']);

  function layout(graph) {
    const pos = { ...FLEET, ...DESKS };
    const by = (type) => graph.nodes.filter((n) => n.type === type);
    const out = (id, type) => graph.edges.filter((e) => e.source === id && e.type === type).map((e) => e.target);
    const rowOf = {};
    by('Company').forEach((c, i) => { pos[c.id] = [500, 250 + i * 76]; rowOf[c.id] = 250 + i * 76; });
    const taken = {};
    const place = (id, x, y) => {
      const key = `${x}:${y}`;
      const k = taken[key] || 0;
      taken[key] = k + 1;
      pos[id] = [x, y + (k === 0 ? 0 : Math.ceil(k / 2) * 30 * (k % 2 ? 1 : -1))];
    };
    by('Mandate').forEach((m) => place(m.id, 268, rowOf[out(m.id, 'CONCERNS')[0]] || 520));
    by('DealDocument').forEach((d) => { const m = out(d.id, 'PART_OF')[0]; place(d.id, 140, pos[m] ? pos[m][1] : 520); });
    by('Restriction').forEach((r) => place(r.id, 388, rowOf[out(r.id, 'RESTRICTS')[0]] || 520));
    by('TradeOrder').forEach((o) => place(o.id, 700, rowOf[out(o.id, 'IN_SECURITY_OF')[0]] || 520));
    by('Hold').forEach((x) => { const o = out(x.id, 'HOLDS')[0]; place(x.id, 604, pos[o] ? pos[o][1] : 520); });
    return pos;
  }

  // The task shown on the wall: the one pinned by a click on its card, else the latest of the selected mission.
  function tracedStep() {
    const run = state.runs.find((r) => r.id === state.selectedRun);
    if (!run || !run.steps.length) return null;
    const pinned = state.pin && state.pin.run === run.id ? run.steps[state.pin.step] : null;
    return { run, step: pinned || run.steps[run.steps.length - 1] };
  }
  // One task's trace: the route its hops took through the fleet (from the gateways' records), the boundaries
  // it passed or hit, and the graph nodes it named or got back - with the desks of the orders among them.
  function traceOf(step) {
    const nodes = {};
    const edges = {};
    const order = [];
    const bound = { admission: '', capability: '', intent: '', graph: '' };
    const mark = (name, verdict) => { if (bound[name] !== 'hit') bound[name] = verdict === 'deny' ? 'hit' : 'pass'; };
    for (const id of step.records || []) {
      const r = state.auditById.get(id);
      if (!r || r.layer !== 'gateway') continue;
      const verdict = tone(r);
      if (verdict !== 'ok' && verdict !== 'deny') continue;
      const agent = `Agent:${r.service.replace(/-iag$/, '')}`;
      const action = (r.action || '').toUpperCase();
      // Reading the intent is not an authorization decision: it never stops a hop on the route.
      if (action === 'INTENT') continue;
      if (action === 'IBAC_USAGE' || action === 'RESOLVED_EVALUATIONS') {
        // A tool the intent check skipped (it publishes no mapping) says nothing about the intent boundary.
        if (verdict === 'deny' || action === 'RESOLVED_EVALUATIONS' || String(r.reason_code).startsWith('intent_match')) mark('intent', verdict);
      } else mark(action === 'ADMISSION' || !action ? 'admission' : 'capability', verdict);
      if (nodes[agent] === 'deny') continue;
      if (!nodes[agent]) order.push(agent);
      nodes[agent] = verdict;
      const chain = (r.chain || []).map((a) => `Agent:${a}`);
      const prev = chain.length > 1 ? chain[chain.length - 2] : null;
      if (prev) edges[`${prev}|${agent}`] = verdict;
      else if (agent === 'Agent:orchestrator') {
        edges['Agent:sentinel|Workflow:wf-wall-control'] = verdict;
        edges['Workflow:wf-wall-control|Agent:orchestrator'] = verdict;
        nodes['Workflow:wf-wall-control'] = verdict;
      }
      if (agent === 'Agent:analyst' && verdict === 'deny') nodes['Workflow:wf-deal-room'] = 'deny';
    }
    const touched = new Set();
    const reached = nodes['Agent:mcp'] === 'ok';
    if (reached) {
      const text = ` ${step.instruction || ''} ${step.answer || ''} `.toLowerCase();
      for (const n of state.graph.nodes) {
        if (['Agent', 'Workflow', 'Region', 'Desk'].includes(n.type)) continue;
        const ext = n.id.split(':').slice(1).join(':').toLowerCase();
        const ticker = n.props && n.props.ticker;
        if ((ext.length >= 5 && text.includes(ext)) || (n.type === 'Company' && text.includes(n.label.toLowerCase()))
          || (ticker && new RegExp(`\\b${ticker}\\b`, 'i').test(text))) touched.add(n.id);
      }
      for (const e of state.graph.edges) if (e.type === 'PLACED_BY' && touched.has(e.source)) touched.add(e.target);
      for (const e of state.graph.edges) {
        if (!HIDDEN_EDGES.has(e.type) && touched.has(e.source) && touched.has(e.target)) edges[`${e.source}|${e.target}`] = 'touch';
      }
      if (step.outcome === 'empty') bound.graph = 'hit';
      else if (step.outcome === 'done') bound.graph = 'pass';
    }
    const feel = { done: 't-done', empty: 't-empty', unmapped: 't-empty', running: 't-run' }[step.outcome] || 't-fail';
    return { nodes, edges, order, touched, bound, feel, reached };
  }

  function renderWall() {
    const graph = state.graph;
    const svg = $('wall');
    const pos = layout(graph);
    const region = {};
    const covered = new Set();
    for (const e of graph.edges) {
      if (e.type === 'BOOKED_IN') region[e.source] = e.target;
      if (e.type === 'COVERS' && e.source === 'Agent:sentinel') covered.add(e.target);
    }
    const shown = tracedStep();
    const trace = shown ? traceOf(shown.step) : { nodes: {}, edges: {}, order: [], touched: new Set(), bound: {}, feel: '', reached: false };
    for (const b of document.querySelectorAll('.boundary')) {
      b.classList.toggle('hit', trace.bound[b.dataset.b] === 'hit');
      b.classList.toggle('pass', trace.bound[b.dataset.b] === 'pass');
    }
    const kids = [
      s('text', { class: 'band-label', x: 16, y: 22 }, 'The fleet · every hop behind an Agent Gateway'),
      s('line', { class: 'plane-rule', x1: 16, y1: 178, x2: 984, y2: 178 }),
      s('text', { class: 'band-label', x: 16, y: 200 }, 'The graph · what the fleet reads and writes'),
      s('text', { class: 'side-label', x: 40, y: 548 }, 'private side'),
      s('text', { class: 'side-label', x: 800, y: 548 }, 'public side'),
      s('line', { class: 'barrier', x1: 500, y1: 212, x2: 500, y2: 512 }),
      s('text', { class: 'barrier-cap', x: 500, y: 206, 'text-anchor': 'middle' }, 'INFORMATION BARRIER'),
    ];
    if (shown) {
      // The route in words, under the fleet.
      const route = ['sentinel', ...trace.order.map((a) => a.split(':')[1])].join('  ›  ');
      const stop = Object.entries(trace.nodes).find(([id, v]) => v === 'deny' && id.startsWith('Agent:'));
      kids.push(s('text', { class: 'trace-cap', x: 16, y: 168 },
        s('tspan', { class: 'trace-n' }, `TASK ${shown.step.index + 1}`), `  ${short(shown.step.title, 30)}   `,
        s('tspan', { class: stop ? 'trace-bad' : 'trace-ok' }, stop ? `${route}  ✕ refused` : route),
        shown.step.outcome === 'unmapped' ? s('tspan', { class: 'trace-n' }, '   · no intent read, not passed on') : null));
    }
    if (trace.reached && pos['Agent:mcp']) {
      // From the MCP gateway down into the graph, to each node the task touched.
      const [mx, my] = pos['Agent:mcp'];
      kids.push(s('path', { class: `reach stem ${trace.feel}`, d: `M${mx},${my + 18} L${mx},178` }));
      for (const id of trace.touched) {
        if (!pos[id] || id.startsWith('Desk:')) continue;
        const [x, y] = pos[id];
        kids.push(s('path', { class: `reach ${trace.feel}`, d: `M${mx},178 Q${mx},${(178 + y) / 2} ${x},${y}` }));
      }
    }
    const caps = (graph.capabilities || {})['wf-wall-control'];
    if (caps) {
      // What the MCP gateway lets this workflow use, and what it holds back from it.
      kids.push(s('text', { class: 'caps head', x: 838, y: 34 }, 'TOOLS GRANTED'));
      caps.tools.forEach((tool, i) => kids.push(s('text', { class: 'caps', x: 838, y: 49 + i * 13 }, tool)));
      const base = 49 + caps.tools.length * 13 + 10;
      kids.push(s('text', { class: 'caps head off-head', x: 838, y: base }, 'TOOLS WITHHELD'));
      caps.withheld.forEach((tool, i) => kids.push(s('text', { class: 'caps off', x: 838, y: base + 15 + i * 13 }, tool,
        s('title', null, 'This tool exists on the MCP server, but the Sentinel\'s workflow does not include it: the gateway hides it and refuses a call to it.'))));
    }
    for (const e of graph.edges) {
      if (HIDDEN_EDGES.has(e.type) || !pos[e.source] || !pos[e.target]) continue;
      const [x1, y1] = pos[e.source];
      const [x2, y2] = pos[e.target];
      const id = `${e.source}|${e.target}`;
      const mark = trace.edges[id] === 'touch' ? `touch ${trace.feel}` : trace.edges[id] || '';
      const mx = (x1 + x2) / 2;
      kids.push(s('path', {
        class: `edge ${e.type === 'INVOKES' || e.type === 'CAN_TRIGGER' ? 'wf' : ''} ${e.origin} ${mark}`,
        d: Math.abs(y1 - y2) < 4 ? `M${x1},${y1} L${x2},${y2}` : `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`,
      }, s('title', null, `${e.type}${e.workflow ? ` · ${e.workflow}` : ''}`)));
    }
    for (const n of graph.nodes) {
      const p = pos[n.id];
      if (!p) continue;
      const isNew = state.known.size && !state.known.has(n.id);
      const ping = state.focus === n.id ? effect(`focus:${n.id}`, 3.6) : null;
      const jolt = trace.nodes[n.id] === 'deny' ? effect(`jolt:${shown.run.id}:${shown.step.index}:${n.id}`, 0.8) : null;
      const cls = ['node', n.type, n.origin, trace.nodes[n.id] || '', trace.touched.has(n.id) ? `touched ${trace.feel}` : '',
        n.id === 'Agent:sentinel' ? 'subject' : '',
        ping ? 'focus' : '', isNew ? 'enter' : '', jolt ? 'jolt' : '',
        region[n.id] && !covered.has(region[n.id]) ? 'outside' : ''].join(' ');
      const tag = region[n.id] ? region[n.id].split(':')[1].toUpperCase() : (n.props && n.props.ticker) || '';
      const g = s('g', { class: cls, style: ping || jolt, transform: `translate(${p[0]},${p[1]})` }, s('title', null, `${n.type} ${n.id.split(':').slice(1).join(':')}${n.props && n.props.reason ? `\n${n.props.reason}` : ''}`));
      const ext = n.id.split(':').slice(1).join(':');
      if (n.type === 'Workflow') {
        g.append(s('rect', { x: -84, y: -16, width: 168, height: 32, rx: 2 }),
          s('text', { x: 0, y: 4, 'text-anchor': 'middle' }, short(n.label, 24)));
      } else {
        const big = n.type === 'Agent' || n.type === 'Company';
        const r = big ? 18 : 14;
        g.append(s('circle', { r }), s('text', { class: 'glyph' }, GLYPH[n.type] || '·'));
        const names = { Restriction: ext, Hold: ext, DealDocument: (n.props && n.props.kind) || 'document', Agent: n.label.replace(/-agent$|control-room-/g, '') };
        const label = names[n.type] || n.label;
        const sub = { Mandate: [ext, tag], TradeOrder: [ext, tag], Company: [tag], DealDocument: [ext.replace(/^doc-|-termsheet$/g, '')], Desk: [(n.props && n.props.side) ? `${n.props.side} side` : ''] }[n.type] || [];
        if (n.type === 'TradeOrder') {
          // Name above and id below the line that runs from the order to its desk.
          g.append(s('text', { x: r + 7, y: -8 }, short(label, 22)), s('text', { class: 'sub', x: r + 7, y: 17 }, sub.filter(Boolean).join(' · ')));
        } else {
          g.append(s('text', { x: 0, y: r + 15, 'text-anchor': 'middle' }, short(label, 20)));
          if (sub.filter(Boolean).length) g.append(s('text', { class: 'sub', x: 0, y: r + 27, 'text-anchor': 'middle' }, sub.filter(Boolean).join(' · ')));
        }
      }
      const hop = trace.order.indexOf(n.id);
      if (hop >= 0) {
        g.append(s('g', { class: `hop-badge ${trace.nodes[n.id]}`, transform: 'translate(17,-17)' },
          s('circle', { r: 8 }), s('text', null, hop + 1)));
      }
      kids.push(g);
    }
    const regions = graph.nodes.filter((n) => n.type === 'Region');
    kids.push(s('text', { class: 'band-label', x: 408, y: 544, 'text-anchor': 'end' }, 'Sentinel covers'));
    regions.forEach((r, i) => kids.push(s('g', { class: `region-chip ${covered.has(r.id) ? 'covered' : ''}`, transform: `translate(${452 + i * 80},540)` },
      s('rect', { x: -36, y: -11, width: 72, height: 22 }), s('text', null, r.label))));
    svg.replaceChildren(...kids);
    state.known = new Set(graph.nodes.map((n) => n.id));
    $('liveTag').textContent = graph.live ? 'graph read-back live' : 'graph read-back off';
    $('liveTag').classList.toggle('on', !!graph.live);
  }

  // ------------------------------------------------------------ missions
  // How a gateway record's action reads on a hop chip.
  const HOP_WORDS = { ADMISSION: 'admission', DISCOVERY: 'discovery', INVOCATION: 'tool allowed', INTENT: 'intent read', IBAC_USAGE: 'fits the intent', RESOLVED_EVALUATIONS: 'platform decision' };
  function hopChips(step) {
    const chips = [h('span', { class: 'hop subject' }, 'sentinel')];
    const seen = new Set();
    for (const id of step.records || []) {
      const r = state.auditById.get(id);
      if (!r || r.layer === 'agent' && r.decision !== 'token_exchanged') continue;
      const t = tone(r);
      let label;
      if (t === 'token') label = `token ${[r.subject, ...(r.chain || [])].map((x) => short(state.agents[x] || x, 14)).join(' › ')}`;
      else label = `${r.service}${r.action ? ` · ${HOP_WORDS[r.action] || r.action.toLowerCase()}` : ''}${r.cached ? ' · cached' : ''}`;
      const key = `${label}|${t}`;
      if (seen.has(key)) continue;
      seen.add(key);
      chips.push(h('span', { class: 'arrow' }, '›'), h('span', { class: `hop ${t}`, onclick: (e) => { e.stopPropagation(); showRecord(r); } }, label));
    }
    return h('div', { class: 'hops' }, chips);
  }
  const STAMP = { done: 'executed', refused: 'refused', empty: 'graph said no', failed: 'failed', running: 'working', unmapped: 'no intent' };
  // The intent the gateway read from the task and carried in the delegation token, with the console's own
  // decision on each evaluation; or the reason no intent could be read.
  function intentBlock(step) {
    const who = (s) => state.agents[s.id] || s.id || 'any';
    if (step.intent_error) {
      const e = step.intent_error;
      return h('div', { class: 'intent refusal' }, h('b', null, 'No intent in this task'),
        h('span', null, [e.reason, e.obligation].filter(Boolean).join(' ') || e.error || ''));
    }
    if (!(step.intent || []).length && !(step.checked || []).length) return null;
    const checked = step.checked || [];
    // What the MCP gateway asked the platform about the tool calls of this task: the product's own decisions.
    const byGateway = checked.length ? h('div', { class: `intent checked${checked.some((c) => c.allowed === false) ? ' denied' : ''}` },
      h('b', null, 'Checked at the MCP gateway', h('i', null, 'what the tool call named · decided by the platform')),
      checked.map((c) => h('div', { class: 'intent-row' },
        h('span', { class: 'who' }, state.agents[c.subjectID] || c.subjectID || c.subjectType || ''),
        h('span', { class: 'act' }, c.action || ''),
        h('span', { class: 'what' }, `${c.resourceType || ''} ${c.resourceID || ''}`),
        h('span', { class: `verdict ${c.allowed ? 'permitted' : 'denied'}` }, c.allowed ? 'allowed' : 'denied')))) : null;
    if (!(step.intent || []).length) return byGateway;
    return h('div', null, h('div', { class: 'intent' },
      h('b', null, 'Intent', h('i', null, 'carried in the delegation token · decisions asked by this console')),
      step.intent.map((entry) => {
        const ev = entry.evaluation || {};
        const res = ev.resource || {};
        return h('div', { class: 'intent-row', title: entry.detail || '' },
          h('span', { class: 'who' }, who(ev.subject || {})),
          h('span', { class: 'act' }, (ev.action || {}).name || 'any action'),
          h('span', { class: 'what' }, `${res.type || ''} ${res.id || '(which ones?)'}`,
            !res.id && entry.detail && entry.verdict === 'permitted' ? h('small', null, entry.detail) : null),
          h('span', { class: `verdict ${entry.verdict}` }, entry.verdict));
      })), byGateway);
  }
  function renderMissions() {
    const runs = state.runs;
    $('missionEmpty').hidden = runs.length > 0;
    if (!runs.find((r) => r.id === state.selectedRun)) state.selectedRun = runs.length ? runs[runs.length - 1].id : null;
    $('runTabs').replaceChildren(...runs.slice(-6).map((r) => h('button', {
      type: 'button', class: r.id === state.selectedRun ? 'on' : '', onclick: () => { state.selectedRun = r.id; state.pin = null; renderMissions(); renderWall(); },
    }, `${clock(r.at)} ${short(r.label, 18)}`)));
    $('escalations').replaceChildren(...state.escalations.slice(-2).map((e) => h('div', { class: `escalation${fresh(`e:${e.id}`)}` },
      h('b', null, 'Escalated to a person'), h('time', { class: 'hint' }, clock(e.at)), h('span', null, e.text))));
    const run = runs.find((r) => r.id === state.selectedRun);
    const box = $('mission');
    if (!run) { box.replaceChildren(); return; }
    const t = run.trigger || {};
    const onWall = tracedStep();
    const pinStep = (index) => {
      const same = state.pin && state.pin.run === run.id && state.pin.step === index;
      state.pin = same ? null : { run: run.id, step: index };
      renderMissions();
      renderWall();
    };
    box.replaceChildren(...[
      h('div', { class: 'run-head' }, h('h3', null, run.label || t.external_id),
        h('span', { class: `stamp ${run.status}` }, run.status),
        h('span', { class: 'meta' }, `${t.type} ${t.external_id} · started ${clock(run.at)} · mind ${run.planner}`)),
      ...(run.thoughts || []).map((x) => h('p', { class: 'thought' }, x)),
      h('div', { class: 'steps' }, run.steps.map((step) => h('article', {
        class: `step ${step.outcome}${onWall && onWall.step === step ? ' traced' : ''}${fresh(`s:${run.id}:${step.index}`)}`,
        title: 'Click to show this task on the wall', onclick: () => pinStep(step.index),
      },
        h('span', { class: 'num' }, step.index + 1),
        h('div', { class: 'top' }, h('h4', null, step.title, onWall && onWall.step === step ? h('i', { class: 'on-wall' }, 'on the wall') : null),
          h('span', { class: `stamp ${step.outcome}` }, STAMP[step.outcome] || step.outcome)),
        step.reason ? h('p', { class: 'why' }, step.reason) : null,
        h('p', { class: 'task' }, step.instruction),
        hopChips(step),
        intentBlock(step),
        step.observed ? h('p', { class: 'observed' }, step.observed) : null,
        step.answer ? h('pre', { class: 'answer', title: 'Click to expand', onclick: (e) => { e.stopPropagation(); e.currentTarget.classList.toggle('open'); } }, step.answer) : null))),
      run.status !== 'running' ? h('p', { class: 'run-foot' }, h('em', null, run.summary || ''),
        run.counts ? `  ${run.counts.done} executed · ${run.counts.refused} refused · ${run.counts.empty} stopped by the graph${run.counts.unmapped ? ` · ${run.counts.unmapped} without an intent` : ''}` : '') : null,
    ].filter(Boolean));
  }

  // --------------------------------------------------------------- audit
  function showRecord(r) {
    openDrawer(`${r.service} · ${r.decision || 'record'}`, [
      ['Decision', r.decision], ['Action', r.action], ['Subject', r.subject], ['Delegation chain', (r.chain || []).join('  ›  ')],
      ['Reason', r.reason], ['Permitted set', r.permitted_set], ['Discovery', r.discovery], ['Capabilities', r.capabilities],
      ['Reason code', r.reason_code], ['Resolved evaluations', r.resolved], ['Intent checked', r.current_intent], ['Intent of the first hop', r.original_intent], ['Token claims', r.claims], ['Token', r.token], ['Trace', r.trace_id],
    ]);
  }
  function recordSections(r) {
    return [
      ['Reason', r.reason], ['Prompt', r.prompt], ['Duration (ms)', r.duration_ms], ['Delegation chain', (r.chain || []).join('  ›  ')], ['Permitted set', r.permitted_set],
      ['Discovery', r.discovery], ['Capabilities', r.capabilities], ['Reason code', r.reason_code], ['Resolved evaluations', r.resolved], ['Intent checked', r.current_intent], ['Intent of the first hop', r.original_intent], ['Token claims', r.claims], ['Token', r.token],
      ['Trace', r.trace_id],
    ].filter(([, v]) => v != null && v !== '' && !(Array.isArray(v) && !v.length));
  }
  function matches(r, filter, query) {
    if (filter === 'denied' ? tone(r) !== 'deny' : filter !== 'all' && r.layer !== filter) return false;
    if (!query) return true;
    const hay = [r.service, r.decision, r.action, r.subject, state.agents[r.subject], (r.chain || []).join(' '), r.reason,
      JSON.stringify(r.permitted_set || ''), JSON.stringify(r.discovery || '')].join(' ').toLowerCase();
    return query.toLowerCase().split(/\s+/).every((word) => hay.includes(word));
  }
  // The full audit, in front of the page: every record, untruncated, newest first; a click opens a record in place.
  function renderLayer() {
    const L = state.layer;
    if (!L.open) return;
    const list = [...state.audit].reverse().filter((r) => matches(r, L.filter, L.query));
    $('layerCount').textContent = `${list.length} of ${state.audit.length} record${state.audit.length === 1 ? '' : 's'} · newest first`;
    $('layerEmpty').hidden = list.length > 0;
    const runLabel = (r) => {
      const run = state.runs.find((x) => x.id === r.run_id);
      return run ? `${short(run.label, 16)} · task ${r.step + 1}` : '';
    };
    $('layerRows').replaceChildren(...list.map((r) => {
      const t = tone(r);
      const open = L.expanded.has(r.id);
      return h('li', { class: t === 'deny' ? 'deny' : '' },
        h('div', { class: 'layer-row', onclick: () => { if (open) L.expanded.delete(r.id); else L.expanded.add(r.id); renderLayer(); } },
          h('time', null, clock(r.at)),
          h('span', { class: 'svc' }, r.service, h('small', null, r.layer)),
          h('span', null, h('span', { class: `badge ${t}` }, t === 'token' ? 'delegation' : (r.decision || '?').replace(/_/g, ' '))),
          h('span', { class: 'act' }, r.action || '–'),
          h('span', { class: 'chain' }, h('b', { title: r.subject }, state.agents[r.subject] || r.subject || '–'),
            (r.chain || []).length ? ` › ${r.chain.join(' › ')}` : ''),
          h('span', { class: 'why' }, t === 'token' ? 'delegation token received by the agent' : (r.reason || (r.discovery ? `listed ${r.discovery.listed}, kept ${r.discovery.kept}` : ''))),
          h('span', { class: 'run' }, runLabel(r))),
        open ? h('div', { class: 'layer-detail' }, recordSections(r).map(([name, value]) => h('div', null, h('h4', null, name),
          h('pre', null, typeof value === 'string' ? value : JSON.stringify(value, null, 2))))) : null);
    }));
  }
  function openLayer(open) {
    state.layer.open = open;
    $('auditLayer').hidden = !open;
    if (open) { renderLayer(); $('layerSearch').focus(); }
  }
  function renderAudit() {
    $('auditCount').textContent = state.audit.length;
    const f = state.filter;
    const list = [...state.audit].reverse().filter((r) => f === 'all' || (f === 'denied' ? tone(r) === 'deny' : r.layer === f)).slice(0, 250);
    $('auditEmpty').hidden = state.audit.length > 0;
    $('audit').replaceChildren(...list.map((r) => {
      const t = tone(r);
      return h('li', { class: `${t === 'deny' ? 'deny' : ''}${fresh(`a:${r.id}`)}`, onclick: () => showRecord(r) },
        h('time', null, clock(r.at)), h('span', { class: 'svc' }, r.service),
        h('span', { class: `badge ${t}` }, t === 'token' ? 'delegation' : (r.decision || '?').replace(/_/g, ' ')),
        h('span', { class: 'act' }, r.action || ''),
        h('span', { class: 'chain' }, h('b', null, short(state.agents[r.subject] || r.subject, 20)),
          (r.chain || []).length ? ` › ${r.chain.join(' › ')}` : ''),
        h('span', { class: 'why' }, t === 'token' ? '' : (r.reason || (r.discovery ? JSON.stringify(r.discovery) : ''))));
    }));
  }

  // ---------------------------------------------------------------- data
  // Records arrive in bursts of hundreds during a mission. Each one only marks the parts of the page it
  // changes; the page redraws those parts once per frame, so it stays responsive whatever the burst.
  const dirty = new Set();
  let frame = 0;
  function redraw() {
    frame = 0;
    const parts = new Set(dirty);
    dirty.clear();
    // Missions first: drawing them selects the mission the wall then traces.
    const order = [['header', renderHeader], ['feed', renderFeed], ['wire', renderWire], ['missions', renderMissions],
      ['wall', renderWall], ['audit', renderAudit], ['layer', renderLayer]];
    for (const [name, draw] of order) if (parts.has(name)) draw();
  }
  function invalidate(...parts) {
    for (const part of parts) dirty.add(part);
    if (!frame) frame = window.requestAnimationFrame(redraw);
  }
  // The page keeps as many records as the console does.
  function keep(list, item, limit) {
    list.push(item);
    if (list.length > limit) list.splice(0, list.length - limit);
  }
  function upsertRun(run) {
    const i = state.runs.findIndex((r) => r.id === run.id);
    if (i < 0) {
      state.runs.push(run);
      state.selectedRun = run.id;
      state.pin = null;
      state.focus = run.trigger ? `${run.trigger.type}:${run.trigger.external_id}` : null;
    } else {
      if (run.steps.length > state.runs[i].steps.length && state.selectedRun === run.id) state.pin = null;
      state.runs[i] = run;
    }
    invalidate('missions', 'wall', 'header');
  }
  function load(snapshot) {
    Object.assign(state, {
      messages: snapshot.messages, audit: snapshot.audit, runs: snapshot.runs, escalations: snapshot.escalations,
      heartbeat: snapshot.heartbeat, identity: snapshot.identity, bridge: snapshot.bridge || {}, fed: snapshot.fed, catalog: snapshot.catalog,
      graph: snapshot.graph, agents: snapshot.agents, drift: snapshot.now - Date.now() / 1000,
    });
    state.known = new Set();
    state.pin = null;
    state.auditById = new Map(state.audit.map((r) => [r.id, r]));
    dirty.clear();
    $('orgName').textContent = snapshot.org.name;
    if (snapshot.org.tagline) $('orgTagline').textContent = snapshot.org.tagline;
    document.title = `${snapshot.org.name} · Control Room`;
    state.drawn = new Set(); state.once = new Map(); state.booting = true;
    renderHeader(); renderFeed(); renderWire(); renderMissions(); renderWall(); renderAudit(); renderLayer();
    state.booting = false;
  }

  // Read the whole state again: after a reset, and whenever the live stream comes back after a break.
  async function resync() {
    const snapshot = await (await fetch('/api/state', { cache: 'no-store' })).json();
    load(snapshot);
    tick();
  }
  function connect() {
    const es = new EventSource('/api/stream');
    let broken = false;
    es.onopen = () => { if (broken) { broken = false; resync().catch(() => {}); } };
    const on = (name, fn) => es.addEventListener(name, (e) => fn(JSON.parse(e.data)));
    on('heartbeat', (d) => { state.heartbeat = d; invalidate('header'); });
    on('identity', (d) => { state.identity = d; invalidate('header'); });
    on('message', (d) => { keep(state.messages, d, 300); invalidate('wire'); });
    on('audit', (d) => {
      keep(state.audit, d, 800);
      state.auditById.set(d.id, d);
      // Only a record of the task in progress changes the missions and the wall.
      invalidate('audit', 'layer', ...(d.run_id ? ['missions', 'wall'] : []));
    });
    on('bridge', (d) => { state.bridge = d; invalidate('header'); });
    on('run', upsertRun);
    on('escalation', (d) => { state.escalations.push(d); invalidate('missions'); });
    on('graph', (d) => { state.graph = d; invalidate('wall'); });
    on('fed', (d) => { if (!state.fed.includes(d.id)) state.fed.push(d.id); state.focus = `${d.kind}:${d.focus}`; state.once.delete(`focus:${state.focus}`); invalidate('feed', 'wall'); });
    on('reset', load);
    es.onerror = () => { broken = true; $('agentLine').textContent = 'stream interrupted · reconnecting'; };
  }

  // A script error would otherwise leave the page silently unresponsive: say it on the page.
  window.addEventListener('error', (e) => toast(`Page error: ${e.message} (${(e.filename || '').split('/').pop()}:${e.lineno})`, true));
  window.addEventListener('unhandledrejection', (e) => toast(`Page error: ${(e.reason && e.reason.message) || e.reason}`, true));

  $('wakeBtn').addEventListener('click', async () => {
    try { await post('/api/wake'); toast('The Sentinel is pulling the wire now.'); } catch (err) { toast(`Wake failed: ${err.message}`, true); }
  });
  $('resetBtn').addEventListener('click', async (e) => {
    if (!window.confirm('Delete from the graph what the feed and the Sentinel wrote, and clear this page?')) return;
    const button = $('resetBtn');
    button.disabled = true;
    try {
      const r = await post('/api/reset');
      // Take the cleared state straight from the console; the live stream may lag or have dropped.
      await resync();
      toast(`Board reset: ${r.deleted} nodes removed from the graph.`);
    } catch (err) { toast(`Reset failed: ${err.message}`, true); }
    button.disabled = false;
  });
  $('filters').addEventListener('click', (e) => {
    const f = e.target.dataset.f;
    if (!f) return;
    state.filter = f;
    document.querySelectorAll('#filters button').forEach((b) => b.classList.toggle('on', b.dataset.f === f));
    renderAudit();
  });
  $('drawerClose').addEventListener('click', () => { $('drawer').hidden = true; });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!$('drawer').hidden) $('drawer').hidden = true;
    else if (!$('aboutLayer').hidden) $('aboutLayer').hidden = true;
    else openLayer(false);
  });
  $('aboutOpen').addEventListener('click', () => { $('aboutLayer').hidden = false; });
  $('aboutClose').addEventListener('click', () => { $('aboutLayer').hidden = true; });
  if (window.location.hash === '#about') $('aboutLayer').hidden = false;
  $('auditOpen').addEventListener('click', () => openLayer(true));
  $('layerClose').addEventListener('click', () => openLayer(false));
  $('layerSearch').addEventListener('input', (e) => { state.layer.query = e.target.value.trim(); renderLayer(); });
  $('layerFilters').addEventListener('click', (e) => {
    const f = e.target.dataset.f;
    if (!f) return;
    state.layer.filter = f;
    document.querySelectorAll('#layerFilters button').forEach((b) => b.classList.toggle('on', b.dataset.f === f));
    renderLayer();
  });
  $('agentCard').addEventListener('click', () => {
    const id = state.identity || {};
    if (id.sub) openDrawer('The Sentinel\'s own token', [['Subject', id.sub], ['Audience', id.aud], ['Issuer', id.issuer || id.iss], ['Grant', id.grant], ['Access token', id.token]]);
  });

  resync().then(() => { connect(); setInterval(tick, 500); if (window.location.hash === '#audit') openLayer(true); });
})();
