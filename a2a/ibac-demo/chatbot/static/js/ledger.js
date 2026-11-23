// Copyright (c) 2026 IndyKite
//
// The audit ledger of the console's right pane.
//
// index.html builds each audit card (renderAuditCard) and hands it to this
// module, which owns everything around the card:
//   - runs: the records of one request folded under one header (person,
//     prompt, outcome), newest run open;
//   - filters: free text, service, kind of question, denials only;
//   - card actions: the raw record, the claims of a token, the same question
//     asked again of the platform, the card's place in the request graph;
//   - the pane itself: follow / pause, collapse, export, clear, resize.
//
// A run is found without a shared trace id (the agents start a new trace per
// hop): the records of one person belong together until that person enters
// again. "Entering" is a prompt sent from this console (notePrompt), an
// admission whose delegation chain holds one agent only, or a new record of
// the Intent Agent. Records that name no person join the run their trace id
// already belongs to, else a "service records" group.
(function () {
    'use strict';

    const ENTRY_GAP_MS = 3000;      // two entry records closer than this are one request
    const PROMPT_WAIT_MS = 20000;   // how long a noted prompt waits for its first record
    const NEAR_BOTTOM_PX = 40;
    const WIDTH_KEY = 'ledgerWidth';
    const MIN_WIDTH = 280;

    let host = null;
    let ctx = {
        apiUrl: '', agentName: () => '', me: () => ({ subject: '', name: '' }), onClear: () => {}, onGraph: null,
    };
    const listeners = [];        // told when the runs or their records change (static/js/graphs.js)
    let notifyQueued = false;
    let recheckEnabled = false;

    const runs = [];             // oldest first
    const current = new Map();   // person subject -> that person's open run
    const byTrace = new Map();   // trace id -> run
    const records = [];          // every mounted record as received, for export
    const services = new Set();
    let seq = 0;
    let pendingPrompt = null;    // { text, at } when the sender's subject is unknown
    let following = true;
    let allCollapsed = false;
    const filters = { text: '', service: '', deniedOnly: false, kinds: new Set() };

    const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    };
    const byId = (id) => document.getElementById(id);

    // Tell the listeners once per frame, however many records arrived in it.
    function notify() {
        if (notifyQueued) return;
        notifyQueued = true;
        requestAnimationFrame(() => {
            notifyQueued = false;
            listeners.forEach((fn) => fn());
        });
    }

    // ---------------------------------------------------------------
    // Classifying a record
    // ---------------------------------------------------------------

    function question(event) {
        return (event.action || '').toUpperCase();
    }

    // The filter chip a record answers to.
    function kindOf(event, decision) {
        const q = question(event);
        if (decision.startsWith('intent_') || q === 'INTENT' || q.startsWith('IBAC_')) return 'intent';
        if (q === 'RESOLVED_EVALUATIONS') return 'data';
        if (q === 'DISCOVERY' || q === 'INVOCATION') return 'capability';
        if (decision === 'token_exchanged' || decision.startsWith('introspect') || decision.startsWith('exchange_')
            || /token/i.test(event.service || '')) return 'token';
        return 'admission';
    }

    function toneOf(decision) {
        if (decision === 'authorized' || decision === 'intent_found') return 'ok';
        if (decision === 'not_authorized' || decision.endsWith('_refused')
            || decision === 'introspected_inactive' || decision === 'intent_error') return 'deny';
        if (decision === 'error' || decision.startsWith('intent_')) return 'warn';
        return 'info';
    }

    // An admission at the first gateway of a request: the delegation chain
    // holds the agent being reached and nothing before it.
    function isEntry(event, decision) {
        const q = question(event);
        if (['DISCOVERY', 'INVOCATION', 'INTENT', 'NOOP', 'RESOLVED_EVALUATIONS'].includes(q) || q.startsWith('IBAC_')) {
            return false;
        }
        if (decision !== 'authorized' && decision !== 'not_authorized') return false;
        const chain = Array.isArray(event.actors_chain) ? event.actors_chain.filter(Boolean) : [];
        return chain.length <= 1;
    }

    // ---------------------------------------------------------------
    // Runs
    // ---------------------------------------------------------------

    function setOpen(run, open) {
        run.el.classList.toggle('open', open);
        run.head.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    function setPrompt(run, text) {
        run.prompt = text || '';
        run.promptEl.textContent = run.prompt;
        run.promptEl.title = run.prompt;
        run.promptEl.hidden = !run.prompt;
    }

    function whoLabel(subject) {
        const me = ctx.me();
        if (subject && subject === me.subject) return me.name || subject;
        return subject;
    }

    function newRun(subject, prompt, system) {
        // A run the viewer never opened or closed by hand folds away when the
        // next one starts.
        runs.forEach((r) => { if (!r.userToggled) setOpen(r, false); });

        const run = {
            id: `run${++seq}`, subject, system: !!system, prompt: '', startedAt: Date.now(),
            entryAt: 0, intentAt: 0, cards: [], entries: [], seen: new Map(), userToggled: false,
        };
        run.el = el('section', `run${system ? ' run-system' : ''}`);
        run.head = el('button', 'run-head');
        run.head.type = 'button';
        const line = el('span', 'run-line');
        run.timeEl = el('span', 'run-time', new Date().toLocaleTimeString([], { hour12: false }));
        run.whoEl = el('span', 'run-who', system ? 'service records' : whoLabel(subject));
        if (!system && subject) run.whoEl.title = subject;
        run.pillEl = el('span', 'run-pill pending', 'waiting');
        run.intentEl = el('span', 'run-intent');
        run.intentEl.hidden = true;
        run.countEl = el('span', 'run-count', '');
        line.append(el('span', 'run-chevron', '▸'), run.timeEl, run.whoEl, run.pillEl, run.intentEl, run.countEl);
        run.promptEl = el('span', 'run-prompt');
        run.promptEl.hidden = true;
        run.head.append(line, run.promptEl);
        run.head.addEventListener('click', () => {
            run.userToggled = true;
            setOpen(run, !run.el.classList.contains('open'));
        });
        run.body = el('div', 'run-body');
        run.el.append(run.head, run.body);
        setOpen(run, true);

        const waiting = pendingPrompt && Date.now() - pendingPrompt.at < PROMPT_WAIT_MS ? pendingPrompt.text : '';
        if (prompt || (waiting && !system)) {
            setPrompt(run, prompt || waiting);
            pendingPrompt = null;
        }

        const empty = host.querySelector('.ledger-empty');
        if (empty) empty.remove();
        host.appendChild(run.el);
        runs.push(run);
        if (!system && subject) current.set(subject, run);
        allCollapsed = false;
        syncToolbar();
        updateCount();
        scrollToEnd();
        notify();
        return run;
    }

    function systemRun() {
        const last = runs[runs.length - 1];
        return last && last.system ? last : newRun('', '', true);
    }

    // The run a record belongs to; creates one when the record opens a request.
    function runFor(event, decision) {
        const now = Date.now();
        const subject = event.subject || '';
        const person = !!subject && !ctx.agentName(subject);
        let run;
        if (person) {
            run = current.get(subject);
            const entry = isEntry(event, decision);
            const intent = event.service === 'intent-agent';
            const reenters = run && (
                (entry && run.entryAt && now - run.entryAt > ENTRY_GAP_MS)
                || (intent && run.intentAt && now - run.intentAt > ENTRY_GAP_MS));
            if (!run || reenters) run = newRun(subject);
            if (entry && !run.entryAt) run.entryAt = now;
            if (intent && !run.intentAt) run.intentAt = now;
        } else {
            run = (event.trace_id && byTrace.get(event.trace_id)) || systemRun();
        }
        if (event.trace_id && !run.system) byTrace.set(event.trace_id, run);
        if (event.prompt && !run.prompt) setPrompt(run, event.prompt);
        return run;
    }

    // A prompt sent from this console: it opens the sender's next run.
    function notePrompt(text) {
        const me = ctx.me();
        if (me.subject) newRun(me.subject, text);
        else pendingPrompt = { text, at: Date.now() };
    }

    function refreshRun(run) {
        const tones = run.cards.map((c) => c.dataset.tone);
        const denied = tones.filter((t) => t === 'deny').length;
        const failed = tones.filter((t) => t === 'warn').length;
        const state = denied ? 'deny' : failed ? 'warn' : tones.includes('ok') ? 'ok' : tones.length ? 'info' : 'pending';
        run.pillEl.className = `run-pill ${state}`;
        run.pillEl.textContent = {
            deny: 'denied', warn: 'attention', ok: 'allowed', info: 'recorded', pending: 'waiting',
        }[state];
        const parts = [`${run.cards.length} record${run.cards.length === 1 ? '' : 's'}`];
        if (denied) parts.push(`${denied} denied`);
        if (failed) parts.push(`${failed} failed`);
        run.countEl.textContent = parts.join(' · ');
        // What the Intent Agent made of the prompt, when it was asked.
        const asked = run.entries.filter((e) => e.event.service === 'intent-agent' || question(e.event) === 'INTENT');
        const own = asked.find((e) => e.event.service === 'intent-agent');
        run.intentEl.hidden = !asked.length;
        if (asked.length) {
            const found = asked.some((e) => e.tone === 'ok');
            const n = own && Array.isArray(own.event.evaluations) ? own.event.evaluations.length : 0;
            run.intentEl.className = `run-intent${found ? '' : ' none'}`;
            run.intentEl.textContent = found ? (n ? `intent · ${n}` : 'intent') : 'no intent';
            run.intentEl.title = found
                ? 'The Intent Agent read an intent from the prompt' + (n ? `: ${n} evaluation${n === 1 ? '' : 's'}` : '')
                : 'The Intent Agent found no intent in the prompt';
        }
    }

    // ---------------------------------------------------------------
    // Cards
    // ---------------------------------------------------------------

    async function copyText(text, button) {
        const label = button.textContent;
        try {
            if (navigator.clipboard && window.isSecureContext) {
                await navigator.clipboard.writeText(text);
            } else {
                const area = el('textarea');
                area.value = text;
                area.style.position = 'fixed';
                area.style.opacity = '0';
                document.body.appendChild(area);
                area.select();
                document.execCommand('copy');
                area.remove();
            }
            button.textContent = 'copied';
        } catch (_) {
            button.textContent = 'copy failed';
        }
        setTimeout(() => { button.textContent = label; }, 1400);
    }

    // The record exactly as the service sent it, folded under the card.
    function addFooter(card, event, run, entry) {
        const raw = event.raw || event;
        const foot = el('div', 'audit-foot');
        if (ctx.onGraph && !run.system) {
            const graph = el('button', 'audit-tool', 'graph');
            graph.type = 'button';
            graph.title = 'Show where this record sits in the request graph';
            graph.addEventListener('click', () => ctx.onGraph(run.id, entry));
            foot.appendChild(graph);
        }
        const show = el('button', 'audit-tool', 'raw record');
        show.type = 'button';
        const copy = el('button', 'audit-tool', 'copy');
        copy.type = 'button';
        const pre = el('pre', 'audit-raw');
        pre.hidden = true;
        show.addEventListener('click', () => {
            if (!pre.textContent) pre.textContent = JSON.stringify(raw, null, 2);
            pre.hidden = !pre.hidden;
            show.classList.toggle('on', !pre.hidden);
        });
        copy.addEventListener('click', () => copyText(JSON.stringify(raw, null, 2), copy));
        foot.append(show, copy);
        card.append(foot, pre);
    }

    function entityText(e) {
        return `${e.type || '?'}:${e.id || '*'}`;
    }

    // The questions of a record that name both a subject and a resource: the
    // platform's answers the gateway recorded, or the evaluations the Intent
    // Agent wrote with concrete ids.
    function recheckItems(event) {
        const out = [];
        (Array.isArray(event.resolved_evaluations) ? event.resolved_evaluations : []).forEach((r) => {
            if (!r || !r.subjectID || !r.resourceID || !r.action) return;
            out.push({
                subject: { type: r.subjectType, id: r.subjectID },
                action: { name: r.action },
                resource: { type: r.resourceType, id: r.resourceID },
                was: r.allowed,
            });
        });
        if (event.service === 'intent-agent') {
            (Array.isArray(event.evaluations) ? event.evaluations : []).forEach((ev) => {
                if (!ev || !ev.subject || !ev.resource || !ev.action) return;
                if (!ev.subject.id || !ev.resource.id || !ev.action.name) return;
                out.push({
                    subject: { type: ev.subject.type, id: ev.subject.id },
                    action: { name: ev.action.name },
                    resource: { type: ev.resource.type, id: ev.resource.id },
                    context: ev.context,
                });
            });
        }
        return out;
    }

    // "Ask the platform now": the same AuthZEN question, answered live. The
    // answer can differ from the recorded one - the graph may have changed,
    // and the question is sent without the delegation token of the call.
    function attachRecheck(card, event) {
        if (!recheckEnabled) return;
        const items = recheckItems(event);
        if (!items.length) return;
        const details = el('details', 'audit-reason audit-recheck');
        details.appendChild(el('summary', '', 'ask the platform now'));
        items.forEach((item) => {
            const row = el('div', 'audit-recheck-row');
            const text = el('span', 'audit-recheck-q',
                `${entityText(item.subject)} → ${item.action.name} → ${entityText(item.resource)}`);
            const ask = el('button', 'audit-tool', 'ask');
            ask.type = 'button';
            const answer = el('span', 'audit-recheck-a');
            ask.addEventListener('click', async () => {
                ask.disabled = true;
                answer.className = 'audit-recheck-a';
                answer.textContent = 'asking…';
                try {
                    const body = { subject: item.subject, action: item.action, resource: item.resource };
                    if (item.context && Object.keys(item.context).length) body.context = item.context;
                    const res = await fetch(`${ctx.apiUrl}/api/recheck`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        credentials: 'include',
                        body: JSON.stringify(body),
                    });
                    const data = await res.json().catch(() => ({}));
                    if (!res.ok) {
                        answer.textContent = data.error || `HTTP ${res.status}`;
                        answer.classList.add('warn');
                    } else {
                        const time = new Date().toLocaleTimeString([], { hour12: false });
                        const changed = typeof item.was === 'boolean' && item.was !== data.decision;
                        answer.textContent = `${data.decision ? 'allowed' : 'denied'} at ${time}`
                            + (changed ? ` (recorded: ${item.was ? 'allowed' : 'denied'})` : '');
                        answer.classList.add(data.decision ? 'ok' : 'deny');
                    }
                } catch (e) {
                    answer.textContent = `failed: ${e.message}`;
                    answer.classList.add('warn');
                } finally {
                    ask.disabled = false;
                }
            });
            row.append(text, ask, answer);
            details.appendChild(row);
        });
        card.appendChild(details);
    }

    // One AuthZEN question as chips: subject -> action -> resource, with an
    // optional verdict in front and a note behind. Entities are {type, id}.
    function evalRow(q) {
        const row = el('div', 'audit-eval');
        if (q.verdict) row.appendChild(el('span', `audit-verdict ${q.verdictTone || ''}`, q.verdict));
        const entity = (e, role) => {
            const chip = el('span', `audit-entity ${role}`);
            chip.appendChild(el('b', '', (e && e.type) || '?'));
            if (e && e.id) {
                const name = ctx.agentName(e.id);
                const id = el('span', 'audit-entity-id', name || e.id);
                id.title = e.id;
                chip.appendChild(id);
            }
            return chip;
        };
        row.append(
            entity(q.subject, 'subject'), el('span', 'audit-arrow', '→'),
            el('span', 'audit-entity action', q.action || 'any action'), el('span', 'audit-arrow', '→'),
            entity(q.resource, 'resource'),
        );
        if (q.note) row.appendChild(el('span', `audit-eval-note ${q.noteTone || ''}`, q.note));
        return row;
    }

    // A fold-out of evaluation rows under a card.
    function evalBlock(card, label, rows, open, extraClass) {
        if (!rows.length) return null;
        const details = el('details', `audit-reason audit-evals${extraClass ? ` ${extraClass}` : ''}`);
        details.open = !!open;
        details.appendChild(el('summary', '', label));
        rows.forEach((row) => details.appendChild(row));
        card.appendChild(details);
        return details;
    }

    // Put a finished card into its run.
    function mount(card, event, decision, run) {
        card.dataset.kind = kindOf(event, decision);
        card.dataset.tone = toneOf(decision);
        card.dataset.service = event.service || 'unknown';
        const entry = { event, decision, card, kind: card.dataset.kind, tone: card.dataset.tone, token: '' };
        attachRecheck(card, event);
        addFooter(card, event, run, entry);
        run.body.appendChild(card);
        run.cards.push(card);
        run.entries.push(entry);
        records.push(event.raw || event);
        noteService(card.dataset.service);
        refreshRun(run);
        applyFilters();
        scrollToEnd();
        notify();
        return card;
    }

    // The same record arrived again within the run: count it on its card.
    function repeat(run, key) {
        const card = run.seen.get(key);
        if (!card) return;
        const n = Number(card.dataset.repeat || 1) + 1;
        card.dataset.repeat = String(n);
        let badge = card.querySelector('.audit-repeat');
        if (!badge) {
            badge = el('span', 'audit-repeat');
            const time = card.querySelector('.audit-time');
            if (time) time.before(badge);
            else card.appendChild(badge);
        }
        badge.textContent = `×${n}`;
        badge.title = `The same record arrived ${n} times in this request`;
    }

    // ---------------------------------------------------------------
    // Tokens
    // ---------------------------------------------------------------

    function decodeJwt(token) {
        const parts = String(token || '').split('.');
        if (parts.length < 2) return null;
        try {
            const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
            const bin = atob(b64 + '='.repeat((4 - (b64.length % 4)) % 4));
            const claims = JSON.parse(new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0))));
            return claims && typeof claims === 'object' ? claims : null;
        } catch (_) {
            return null;
        }
    }

    function principal(value) {
        return ctx.agentName(value) || value;
    }

    // The actors of a delegation token, oldest first: `act` nests the previous
    // actor inside the newest one.
    function actChain(claims) {
        const hops = [];
        let act = claims.act;
        while (act && typeof act === 'object') {
            if (act.sub) hops.push(act.sub);
            act = act.act;
        }
        return hops.reverse();
    }

    // The intent a token carries, as evaluations (null: none, or opaque token).
    function tokenIntent(token) {
        const claims = decodeJwt(token);
        return claims ? actIntent(claims) : null;
    }

    // The intent the newest actor carries, as evaluations.
    function actIntent(claims) {
        let act = claims.act;
        while (act && typeof act === 'object') {
            if (act.intent !== undefined) {
                const intent = act.intent;
                if (Array.isArray(intent)) return intent;
                if (intent && Array.isArray(intent.evaluations)) return intent.evaluations;
                return [intent];
            }
            act = act.act;
        }
        return null;
    }

    function claimRow(box, key, value, title) {
        if (value === undefined || value === null || value === '') return;
        const row = el('div', 'audit-claim');
        row.append(el('span', 'audit-claim-k', key), el('span', 'audit-claim-v', String(value)));
        if (title) row.title = title;
        box.appendChild(row);
    }

    function fillClaims(box, token) {
        box.querySelectorAll('.audit-claim, .audit-claims-all').forEach((n) => n.remove());
        const claims = decodeJwt(token);
        if (!claims) {
            claimRow(box, 'claims', 'opaque token: nothing to decode');
            return;
        }
        claimRow(box, 'subject', principal(claims.sub), claims.sub);
        claimRow(box, 'audience', Array.isArray(claims.aud) ? claims.aud.join(', ') : claims.aud);
        const chain = actChain(claims);
        if (chain.length) claimRow(box, 'chain', chain.map(principal).join(' → '), chain.join(' → '));
        const intent = actIntent(claims);
        if (intent) {
            const lines = intent.map((e) => (e && typeof e === 'object' && (e.subject || e.resource)
                ? `${entityText(e.subject || {})} → ${(e.action && e.action.name) || 'any action'} → ${entityText(e.resource || {})}`
                : JSON.stringify(e)));
            claimRow(box, 'intent', `${intent.length} evaluation${intent.length === 1 ? '' : 's'}\n${lines.join('\n')}`);
        }
        if (typeof claims.exp === 'number') {
            const left = Math.round(claims.exp - Date.now() / 1000);
            const when = new Date(claims.exp * 1000).toLocaleTimeString([], { hour12: false });
            claimRow(box, 'expires', left > 0 ? `${when} (in ${left} s)` : `${when} (expired)`);
        }
        claimRow(box, 'scope', claims.scope);
        claimRow(box, 'issuer', claims.iss);
        claimRow(box, 'token id', claims.jti);
        const all = el('details', 'audit-claims-all');
        all.appendChild(el('summary', '', 'all claims'));
        all.appendChild(el('pre', 'audit-raw', JSON.stringify(claims, null, 2)));
        box.appendChild(all);
    }

    // Show (or replace) the token of a hop on its card: the raw string and
    // its claims read out.
    function setToken(card, event) {
        const token = event.reason || '';
        let raw = card.querySelector('.audit-token');
        let claims = card.querySelector('.audit-claims');
        if (!raw) {
            raw = el('details', 'audit-reason audit-token');
            raw.appendChild(el('summary', '', 'token'));
            raw.appendChild(el('div'));
            claims = el('details', 'audit-reason audit-claims');
            claims.appendChild(el('summary', '', 'token claims'));
            card.append(claims, raw);
        }
        raw.querySelector('div').textContent = token;
        fillClaims(claims, token);
        card.dataset.token = '1';
        runs.forEach((run) => run.entries.forEach((entry) => { if (entry.card === card) entry.token = token; }));
        applyFilters();
        notify();
    }

    // ---------------------------------------------------------------
    // Filters
    // ---------------------------------------------------------------

    function filtering() {
        return !!(filters.text || filters.service || filters.deniedOnly || filters.kinds.size);
    }

    function matches(card, run) {
        if (filters.service && card.dataset.service !== filters.service) return false;
        if (filters.deniedOnly && card.dataset.tone !== 'deny' && card.dataset.tone !== 'warn') return false;
        if (filters.kinds.size) {
            const hit = filters.kinds.has(card.dataset.kind) || (filters.kinds.has('token') && card.dataset.token === '1');
            if (!hit) return false;
        }
        if (filters.text) {
            const hay = `${card.textContent} ${run.prompt} ${run.subject}`.toLowerCase();
            if (!hay.includes(filters.text)) return false;
        }
        return true;
    }

    function applyFilters() {
        const active = filtering();
        let shown = 0;
        let total = 0;
        runs.forEach((run) => {
            let visible = 0;
            run.cards.forEach((card) => {
                total += 1;
                const ok = !active || matches(card, run);
                card.classList.toggle('filtered-out', !ok);
                if (ok) { visible += 1; shown += 1; }
            });
            run.el.classList.toggle('filtered-out', active && visible === 0);
        });
        // While a filter is on, every run with a match shows its records.
        host.classList.toggle('ledger-filtering', active);
        updateCount(shown, total);
    }

    function updateCount(shown, total) {
        const count = byId('ledgerCount');
        if (!count) return;
        if (total === undefined) {
            total = runs.reduce((n, r) => n + r.cards.length, 0);
            shown = total;
        }
        if (!runs.length) { count.textContent = ''; return; }
        const records_ = filtering() ? `${shown} of ${total}` : `${total}`;
        count.textContent = `${records_} record${total === 1 ? '' : 's'} · ${runs.length} run${runs.length === 1 ? '' : 's'}`;
    }

    function noteService(name) {
        if (services.has(name)) return;
        services.add(name);
        const select = byId('ledgerService');
        if (!select) return;
        const option = el('option', '', name);
        option.value = name;
        select.appendChild(option);
    }

    // ---------------------------------------------------------------
    // The pane
    // ---------------------------------------------------------------

    function scrollToEnd() {
        if (!following) return;
        requestAnimationFrame(() => { host.scrollTop = host.scrollHeight; });
    }

    function syncToolbar() {
        const follow = byId('ledgerFollow');
        if (follow) {
            follow.setAttribute('aria-pressed', following ? 'true' : 'false');
            follow.textContent = following ? 'following' : 'follow ↓';
        }
        const collapse = byId('ledgerCollapse');
        if (collapse) collapse.textContent = allCollapsed ? 'expand' : 'collapse';
    }

    function showEmpty() {
        host.innerHTML = '';
        host.appendChild(el('div', 'ledger-empty',
            'No records yet. Each request becomes one run: what the gateways, the Token Service and the '
            + 'Intent Agent recorded about it.'));
    }

    function reset() {
        runs.length = 0;
        records.length = 0;
        current.clear();
        byTrace.clear();
        pendingPrompt = null;
        following = true;
        allCollapsed = false;
        showEmpty();
        syncToolbar();
        updateCount();
        notify();
    }

    // Bring cards into view and mark them for a moment: the graph points at
    // the records behind a node this way.
    function focusCards(cards) {
        const list = (cards || []).filter(Boolean);
        if (!list.length) return;
        document.querySelectorAll('.audit-card.flash').forEach((c) => c.classList.remove('flash'));
        runs.forEach((run) => {
            if (run.cards.some((c) => list.includes(c))) { run.userToggled = true; setOpen(run, true); }
        });
        following = false;
        syncToolbar();
        list.forEach((card) => {
            void card.offsetWidth;  // restart the animation on a repeated click
            card.classList.add('flash');
        });
        const first = list.find((c) => c.offsetParent !== null) || list[0];
        first.scrollIntoView({ block: 'nearest' });
    }

    // What the graph reads: every run with its records in arrival order.
    function listRuns() {
        return runs.map((run) => ({
            id: run.id,
            subject: run.subject,
            who: run.whoEl.textContent,
            time: run.timeEl.textContent,
            prompt: run.prompt,
            system: run.system,
            entries: run.entries,
        }));
    }

    function exportRecords() {
        const blob = new Blob([JSON.stringify(records, null, 2)], { type: 'application/json' });
        const link = el('a');
        link.href = URL.createObjectURL(blob);
        link.download = `audit-records-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    }

    function bindToolbar() {
        const search = byId('ledgerSearch');
        if (search) {
            search.addEventListener('input', () => {
                filters.text = search.value.trim().toLowerCase();
                applyFilters();
            });
        }
        const service = byId('ledgerService');
        if (service) {
            service.addEventListener('change', () => {
                filters.service = service.value;
                applyFilters();
            });
        }
        document.querySelectorAll('#ledgerChips [data-kind]').forEach((chip) => {
            chip.addEventListener('click', () => {
                const kind = chip.dataset.kind;
                if (filters.kinds.has(kind)) filters.kinds.delete(kind);
                else filters.kinds.add(kind);
                chip.setAttribute('aria-pressed', filters.kinds.has(kind) ? 'true' : 'false');
                applyFilters();
            });
        });
        const denied = byId('ledgerDenied');
        if (denied) {
            denied.addEventListener('click', () => {
                filters.deniedOnly = !filters.deniedOnly;
                denied.setAttribute('aria-pressed', filters.deniedOnly ? 'true' : 'false');
                applyFilters();
            });
        }
        const follow = byId('ledgerFollow');
        if (follow) {
            follow.addEventListener('click', () => {
                following = !following;
                syncToolbar();
                scrollToEnd();
            });
        }
        const collapse = byId('ledgerCollapse');
        if (collapse) {
            collapse.addEventListener('click', () => {
                allCollapsed = !allCollapsed;
                runs.forEach((run) => { run.userToggled = true; setOpen(run, !allCollapsed); });
                syncToolbar();
            });
        }
        const exportBtn = byId('ledgerExport');
        if (exportBtn) exportBtn.addEventListener('click', exportRecords);
        const clear = byId('ledgerClear');
        if (clear) {
            clear.addEventListener('click', () => {
                reset();
                ctx.onClear();
            });
        }
        // Scrolling up pauses the follow; reaching the end resumes it.
        host.addEventListener('scroll', () => {
            const atEnd = host.scrollHeight - host.scrollTop - host.clientHeight < NEAR_BOTTOM_PX;
            if (atEnd !== following) {
                following = atEnd;
                syncToolbar();
            }
        });
    }

    // Drag the pane's left edge to resize it; the width is kept per browser.
    function bindResizer() {
        const pane = byId('terminalContainer');
        const grip = byId('terminalResizer');
        if (!pane || !grip) return;
        const clampWidth = (w) => Math.max(MIN_WIDTH, Math.min(w, Math.round(window.innerWidth * 0.7)));
        const apply = (w) => { pane.style.width = `${clampWidth(w)}px`; };
        const saved = Number(localStorage.getItem(WIDTH_KEY));
        if (saved) apply(saved);
        grip.addEventListener('pointerdown', (down) => {
            down.preventDefault();
            grip.setPointerCapture(down.pointerId);
            const startX = down.clientX;
            const startWidth = pane.getBoundingClientRect().width;
            pane.classList.add('resizing');
            const move = (e) => apply(startWidth + (startX - e.clientX));
            const up = () => {
                grip.removeEventListener('pointermove', move);
                grip.removeEventListener('pointerup', up);
                grip.removeEventListener('pointercancel', up);
                pane.classList.remove('resizing');
                localStorage.setItem(WIDTH_KEY, String(Math.round(pane.getBoundingClientRect().width)));
            };
            grip.addEventListener('pointermove', move);
            grip.addEventListener('pointerup', up);
            grip.addEventListener('pointercancel', up);
        });
        grip.addEventListener('dblclick', () => {
            pane.style.width = '';
            localStorage.removeItem(WIDTH_KEY);
        });
    }

    function init(options) {
        ctx = Object.assign(ctx, options || {});
        host = byId('terminalMessages');
        showEmpty();
        bindToolbar();
        bindResizer();
        syncToolbar();
    }

    window.Ledger = {
        init,
        reset,
        notePrompt,
        runFor,
        mount,
        repeat,
        setToken,
        setRecheck(enabled) { recheckEnabled = !!enabled; },
        runs: listRuns,
        focus: focusCards,
        evalRow,
        evalBlock,
        tokenIntent,
        subscribe(fn) { listeners.push(fn); },
    };
})();
