// Copyright (c) 2026 IndyKite
//
// The Graph view of the console: one request drawn from its audit records.
//
// Nothing here asks the platform anything. The graph is rebuilt from the
// records the ledger (static/js/ledger.js) holds for a run, each time one
// arrives, in two modes:
//   - request: the person, then for every hop the gateway that decided and
//     the agent or MCP server behind it, following the delegation chain of
//     each record; the capabilities a call named hang off the party that
//     offers them, and the Intent Agent off the gateway that asked it.
//     Selecting a node marks its path back to the person (the chain) and
//     points the ledger at the records behind it.
//   - capabilities: for one gateway, what the caller was permitted to see
//     (by kind), what it used, what was refused, and how much of the
//     server's list stayed hidden.
//   - intent: what the Intent Agent read from the prompt and what the IBAC
//     engine of the gateways did with it - the evaluations, the skills and
//     tools each one fits, the calls checked against the intent, and the
//     platform's answers about the data those calls named - under a strip
//     of the six steps the prompt went through.
(function () {
    'use strict';

    // Request mode grows downwards (a chain is long, its nodes are wide and
    // flat); capabilities mode grows to the right (three levels, long lists).
    const REQUEST = { vertical: true, hop: 116, branch: 228 };
    const CAPABILITIES = { vertical: false, hop: 252, branch: 50 };
    const INTENT = { column: 272, row: 96 };   // intent mode: one column per step, left to right
    const STATUS_TTL_MS = 20000;
    const MAX_FIT_ZOOM = 1.1;
    const MIN_ZOOM = 0.3;
    const MAX_ZOOM = 2.2;
    const FIT_PADDING = { x: 40, top: 28, bottom: 66 };   // the legend sits at the bottom of the stage
    const TONE_RANK = { none: 0, info: 1, ok: 2, warn: 3, deny: 4 };
    // What a node is, said above its name; kinds without one are compact.
    const KICKER = {
        person: 'Person', gateway: 'Agent Gateway', agent: 'Agent', server: 'MCP server', intent: 'Intent Agent',
        prompt: 'Prompt', evaluation: 'Evaluation', skill: 'Skill', call: 'Call', answer: 'Platform',
    };
    const WIDTH = {
        person: 170, gateway: 204, agent: 178, server: 178, intent: 186, capability: 190, group: 136, ghost: 190,
        prompt: 204, evaluation: 222, skill: 184, call: 204, answer: 192,
    };
    const MARK = { ok: '✓', deny: '✕', warn: '!' };
    const SVG_NS = 'http://www.w3.org/2000/svg';
    const QUESTION_TAGS = {
        ADMISSION: 'admission', DISCOVERY: 'discovery', INVOCATION: 'invocation', NOOP: 'refused early',
        IBAC_USAGE: 'intent check', IBAC_DISCOVERY: 'intent check', RESOLVED_EVALUATIONS: 'data check',
        INTENT: 'intent',
    };
    const TAG_ORDER = ['admission', 'refused early', 'discovery', 'invocation', 'intent', 'intent check', 'data check', 'token'];
    const KIND_PLURAL = { tool: 'tools', resource: 'resources', prompt: 'prompts', skill: 'skills' };

    let ctx = { apiUrl: '', agentName: () => '', setLayer: () => {} };
    let ibacStatus = null;       // /api/ibac/status: is IBAC on, and is the Intent Agent there
    let ibacStatusAt = 0;
    let world = null;            // the pannable, zoomable layer inside the stage
    let view = { x: 0, y: 0, k: 1 };
    let drawn = { nodes: new Map(), edges: [], bounds: null, key: '' };
    let visible = false;
    let dirty = true;
    let mode = 'request';
    let runId = null;
    let followLatest = true;
    let gatewayId = null;        // capabilities mode: the gateway whose lists are drawn
    let selectedId = null;
    let userMoved = false;       // the viewer panned or zoomed: stop refitting on every record
    let model = null;            // what is on the canvas: { nodes: Map, root }
    let requestModel = null;     // the request graph of the run, also behind capabilities mode
    let lastSignature = '';

    const byId = (id) => document.getElementById(id);
    const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    };

    // ---------------------------------------------------------------
    // Reading the records
    // ---------------------------------------------------------------

    function question(event) {
        return (event.action || '').toUpperCase();
    }

    function tagOf(entry) {
        const q = question(entry.event);
        return QUESTION_TAGS[q] || (entry.decision === 'token_exchanged' ? 'token' : 'admission');
    }

    function isAdmission(entry) {
        const q = question(entry.event);
        return !QUESTION_TAGS[q] || q === 'ADMISSION';
    }

    function worst(tones) {
        return tones.reduce((a, b) => (TONE_RANK[b] > TONE_RANK[a] ? b : a), 'none');
    }

    function chainOf(event) {
        const chain = Array.isArray(event.actors_chain) ? event.actors_chain.filter(Boolean) : [];
        return chain.length ? chain : (event.actor ? [event.actor] : []);
    }

    function principal(subject) {
        return ctx.agentName(subject) || subject;
    }

    // An MCP server rather than an agent: told by its name only, for the shape.
    function isServer(subject) {
        return /mcp|server/i.test(principal(subject));
    }

    // ---------------------------------------------------------------
    // Request mode: person -> gateway -> agent, along each chain
    // ---------------------------------------------------------------

    function buildRequest(run) {
        const nodes = new Map();
        const add = (node) => {
            const full = Object.assign({ entries: [], tone: 'none', sub: '', edge: { tone: 'none', label: '' } }, node);
            nodes.set(full.id, full);
            return full;
        };
        add({ id: 'person', kind: 'person', label: run.who || run.subject || 'caller', parent: null, title: run.subject });

        // The agent at the end of a chain prefix; hops no record names yet are
        // drawn as placeholders so a later hop still hangs from its caller.
        const ensureAgent = (prefix) => {
            const id = `ag:${prefix.join('>')}`;
            let node = nodes.get(id);
            if (!node) {
                const parent = prefix.length > 1 ? ensureAgent(prefix.slice(0, -1)).id : 'person';
                const subject = prefix[prefix.length - 1];
                node = add({
                    id, kind: isServer(subject) ? 'server' : 'agent', label: principal(subject), title: subject,
                    parent, placeholder: true, reached: false,
                });
            }
            return node;
        };

        const intentEntries = [];
        let entryGateway = null;
        run.entries.forEach((entry) => {
            const event = entry.event;
            if (event.service === 'intent-agent' || question(event) === 'INTENT') {
                intentEntries.push(entry);
                return;
            }
            if (entry.decision === 'token_exchanged') return;  // a token no gateway record claimed
            const chain = chainOf(event);
            const caller = chain.length > 1 ? `ag:${chain.slice(0, -1).join('>')}` : 'person';
            const agent = chain.length ? ensureAgent(chain) : null;
            const gid = `gw:${event.service}|${chain.join('>')}`;
            let gateway = nodes.get(gid);
            if (!gateway) {
                gateway = add({ id: gid, kind: 'gateway', label: event.service || 'gateway', parent: caller, agentId: agent && agent.id });
                if (agent && agent.parent === caller) agent.parent = gid;
                if (chain.length <= 1 && !entryGateway) entryGateway = gateway;
            }
            gateway.entries.push(entry);
            if (entry.token) gateway.hasToken = true;
            if (agent) {
                agent.placeholder = false;
                if (entry.tone === 'ok') agent.reached = true;
            }
            // The capabilities a call named, on the party that offers them.
            if (question(event) === 'INVOCATION' && agent && event.capabilities && typeof event.capabilities === 'object') {
                Object.entries(event.capabilities).forEach(([kind, ids]) => {
                    (Array.isArray(ids) ? ids : [ids]).filter(Boolean).forEach((name) => {
                        const cid = `cap:${agent.id}|${kind}:${name}`;
                        const cap = nodes.get(cid) || add({ id: cid, kind: 'capability', label: String(name), sub: kind, parent: agent.id });
                        cap.entries.push(entry);
                    });
                });
            }
        });

        if (intentEntries.length) {
            const own = intentEntries.find((e) => e.event.service === 'intent-agent');
            const count = own && Array.isArray(own.event.evaluations) ? own.event.evaluations.length : null;
            add({
                id: 'intent', kind: 'intent', label: 'Intent Agent', parent: entryGateway ? entryGateway.id : 'person',
                entries: intentEntries,
                sub: count === null ? 'asked' : `${count} evaluation${count === 1 ? '' : 's'}`,
            });
        }

        // Gateways first: what stands behind a gateway is described by its outcome.
        const ordered = [...nodes.values()].sort((a, b) => (b.kind === 'gateway') - (a.kind === 'gateway'));
        ordered.forEach((node) => {
            const tones = node.entries.map((e) => e.tone);
            if (node.kind === 'gateway') {
                node.tone = worst(tones);
                const seen = node.entries.map(tagOf).concat(node.hasToken ? ['token'] : []);
                const tags = TAG_ORDER.filter((t) => seen.includes(t));
                node.sub = tags.join(' · ');
                node.chips = tags;
                const admissions = node.entries.filter(isAdmission);
                const tone = worst(admissions.map((e) => e.tone));
                node.edge = { tone, label: { ok: 'admitted', deny: 'refused', warn: 'failed' }[tone] || '' };
            } else if (node.kind === 'agent' || node.kind === 'server') {
                const gateway = nodes.get(node.parent);
                const viaGateway = gateway && gateway.kind === 'gateway';
                node.tone = node.reached ? 'ok' : 'none';
                node.sub = node.reached ? '' : (viaGateway && gateway.tone === 'deny' ? 'not reached' : 'no record');
                node.edge = node.reached
                    ? { tone: 'ok', label: 'forwarded' }
                    : { tone: viaGateway && gateway.tone === 'deny' ? 'deny' : 'none', label: viaGateway && gateway.tone === 'deny' ? 'blocked' : '' };
            } else if (node.kind === 'capability') {
                node.tone = worst(tones);
                node.edge = { tone: node.tone, label: node.tone === 'deny' ? 'refused' : node.tone === 'ok' ? 'used' : '' };
            } else if (node.kind === 'intent') {
                node.tone = worst(tones);
                node.edge = { tone: node.tone, label: node.tone === 'ok' ? 'intent found' : node.tone === 'none' ? '' : 'no intent' };
            }
        });
        return { nodes, root: 'person' };
    }

    // ---------------------------------------------------------------
    // Capabilities mode: one gateway's permitted set, uses and refusals
    // ---------------------------------------------------------------

    function capabilityGateways(request) {
        return [...request.nodes.values()].filter((n) => n.kind === 'gateway'
            && n.entries.some((e) => ['DISCOVERY', 'INVOCATION'].includes(question(e.event))));
    }

    function buildCapabilities(request, gateway) {
        const nodes = new Map();
        const add = (node) => {
            const full = Object.assign({ entries: [], tone: 'none', sub: '', edge: { tone: 'none', label: '' } }, node);
            nodes.set(full.id, full);
            return full;
        };
        const party = request.nodes.get(gateway.agentId);
        add({
            id: 'c:root', kind: party ? party.kind : 'agent', label: party ? party.label : gateway.label,
            sub: `behind ${gateway.label}`, parent: null, entries: gateway.entries.filter((e) => e.kind === 'capability'),
            tone: 'ok', reached: true,
        });
        const group = (kind) => nodes.get(`c:k:${kind}`)
            || add({ id: `c:k:${kind}`, kind: 'group', label: KIND_PLURAL[kind] || kind, parent: 'c:root', permitted: 0 });
        const item = (kind, name) => nodes.get(`c:i:${kind}:${name}`)
            || add({ id: `c:i:${kind}:${name}`, kind: 'capability', label: String(name), parent: group(kind).id, state: 'named' });

        gateway.entries.forEach((entry) => {
            const event = entry.event;
            const q = question(event);
            if (q === 'DISCOVERY') {
                const permitted = event.permitted_set && typeof event.permitted_set === 'object' ? event.permitted_set : {};
                const kinds = Object.keys(permitted);
                kinds.forEach((kind) => {
                    const ids = Array.isArray(permitted[kind]) ? permitted[kind] : [];
                    const g = group(kind);
                    g.permitted = Math.max(g.permitted, ids.length);
                    g.entries.push(entry);
                    ids.forEach((name) => {
                        const node = item(kind, name);
                        if (node.state === 'named') node.state = 'permitted';
                        node.entries.push(entry);
                    });
                });
                // What the server listed beyond what the caller was given.
                const stats = event.discovery_stats;
                if (stats && typeof stats === 'object' && Number(stats.listed) > Number(stats.kept)) {
                    const parent = kinds.length === 1 ? group(kinds[0]).id : 'c:root';
                    const hidden = Number(stats.listed) - Number(stats.kept);
                    const ghost = nodes.get(`c:g:${parent}`) || add({ id: `c:g:${parent}`, kind: 'ghost', parent });
                    ghost.label = `+${hidden} hidden`;
                    ghost.sub = 'listed by the server, not permitted';
                    ghost.entries = [entry];
                }
            } else if (q === 'INVOCATION' && event.capabilities && typeof event.capabilities === 'object') {
                Object.entries(event.capabilities).forEach(([kind, ids]) => {
                    (Array.isArray(ids) ? ids : [ids]).filter(Boolean).forEach((name) => {
                        const node = item(kind, name);
                        node.entries.push(entry);
                        if (entry.tone === 'deny') node.state = 'refused';
                        else if (entry.tone === 'ok' && node.state !== 'refused') node.state = 'used';
                    });
                });
            }
        });

        nodes.forEach((node) => {
            if (node.kind === 'group') {
                node.sub = `${node.permitted} permitted`;
                node.edge = { tone: 'none', label: '' };
            } else if (node.kind === 'capability') {
                node.tone = { used: 'ok', refused: 'deny' }[node.state] || 'none';
                node.sub = { used: 'used', refused: 'refused', permitted: '', named: 'named, not listed' }[node.state];
                node.edge = { tone: node.tone, label: '' };
            } else if (node.kind === 'ghost') {
                node.edge = { tone: 'none', label: '' };
            }
        });
        return { nodes, root: 'c:root' };
    }

    // ---------------------------------------------------------------
    // Intent mode: prompt -> evaluations -> what they fit -> calls -> answers
    // ---------------------------------------------------------------

    const evalKey = (subjectType, action, resourceType) => `${subjectType || '?'}|${action || '*'}|${resourceType || '?'}`;
    const keyOfEvaluation = (ev) => evalKey(ev.subject && ev.subject.type, ev.action && ev.action.name, ev.resource && ev.resource.type);
    const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

    function buildIntent(run) {
        const nodes = new Map();
        const edges = [];
        const add = (node) => {
            const full = Object.assign({ entries: [], tone: 'none', sub: '', parent: null, edge: { tone: 'none', label: '' } }, node);
            nodes.set(full.id, full);
            return full;
        };
        const link = (source, target, tone, label) => {
            if (!edges.some((e) => e.source === source && e.target === target)) edges.push({ source, target, tone, label: label || '' });
        };
        const of = (q) => run.entries.filter((e) => question(e.event) === q);
        const own = run.entries.filter((e) => e.event.service === 'intent-agent');
        const asked = of('INTENT').filter((e) => e.event.service !== 'intent-agent');
        const fits = of('IBAC_DISCOVERY');
        const usages = of('IBAC_USAGE');
        const answers = of('RESOLVED_EVALUATIONS');
        const carried = run.entries.filter((e) => e.token && window.Ledger.tokenIntent(e.token));
        const isEntryHop = (entry) => chainOf(entry.event).length <= 1;
        // A record saying the engine was not asked: the skills or tools it names carry no mapping
        // (or one that cannot be read), so the intent was not compared with them.
        const notCompared = (entry) => ['missing_coaz_mapping', 'invalid_coaz_mapping'].includes(entry.event.reason_code);
        const namedIn = (event) => (Array.isArray(event.skill_ids) && event.skill_ids.length
            ? event.skill_ids : [event.skill_id || event.capability_name].filter(Boolean));
        const hasAny = own.length || asked.length || fits.length || usages.length || answers.length || carried.length;

        // The evaluations: the Intent Agent's own (with ids), else what a
        // token carries, else the types the gateways compared.
        const ownRecord = own[own.length - 1];
        const byKey = new Map();
        const addEvaluation = (subject, action, resource, entry) => {
            const key = evalKey(subject && subject.type, action, resource && resource.type);
            const id = `ev:${nodes.size}`;
            const node = add({
                id, layer: 1, kind: 'evaluation', evalKey: key,
                label: `${(subject && subject.type) || '?'} → ${action || 'any action'} → ${(resource && resource.type) || '?'}`,
                sub: resource && resource.id ? String(resource.id) : '',
                title: subject && subject.id ? `subject ${subject.id}` : '',
                tone: 'intent', entries: entry ? [entry] : [],
            });
            if (!byKey.has(key)) byKey.set(key, []);
            byKey.get(key).push(node);
            return node;
        };
        const listed = ownRecord && Array.isArray(ownRecord.event.evaluations) && ownRecord.event.evaluations.length
            ? { entry: ownRecord, list: ownRecord.event.evaluations }
            : carried.length ? { entry: carried[0], list: window.Ledger.tokenIntent(carried[0].token) } : null;
        if (listed) {
            listed.list.forEach((ev) => {
                if (ev && typeof ev === 'object') addEvaluation(ev.subject, ev.action && ev.action.name, ev.resource, listed.entry);
            });
        }
        const evaluationsFor = (key, subjectType, action, resourceType, entry) => {
            if (!byKey.has(key)) addEvaluation({ type: subjectType }, action, { type: resourceType }, entry);
            return byKey.get(key);
        };

        // The prompt, and what the Intent Agent answered.
        const promptEntries = own.concat(asked);
        const found = promptEntries.some((e) => e.tone === 'ok') || !!listed;
        const promptText = run.prompt || 'the prompt';
        add({
            id: 'prompt', layer: 0, kind: 'prompt', entries: promptEntries, tone: found ? 'intent' : worst(promptEntries.map((e) => e.tone)),
            label: promptText.length > 70 ? `${promptText.slice(0, 70)}…` : promptText, title: run.prompt,
        });
        if (promptEntries.length && !found) {
            const failed = promptEntries.find((e) => e.tone !== 'ok');
            add({
                id: 'nointent', layer: 1, kind: 'ghost', label: 'no intent', entries: promptEntries, tone: failed.tone,
                sub: (failed.event.reason || '').slice(0, 60),
            });
            link('prompt', 'nointent', failed.tone, 'refused by the gateway');
        }

        // What each evaluation fits: skills at the entry gateway, listed
        // tools at an MCP gateway.
        const capability = (service, name, layer, kind, sub) => {
            const id = `${kind}:${service}:${name}`;
            return nodes.get(id) || add({ id, layer, kind, label: String(name), sub, tags: [], service });
        };
        fits.forEach((entry) => {
            const event = entry.event;
            const entryHop = isEntryHop(entry);
            if (notCompared(entry)) {
                // Drawn apart from what the intent fits: nothing was decided about these.
                const names = namedIn(event);
                if (!names.length) return;
                const label = names.length <= 2 ? names.join(', ')
                    : `${names.length} ${entryHop ? 'skills' : 'entries'} without a mapping`;
                const id = `skip:${event.service}`;
                const node = nodes.get(id) || add({
                    id, layer: entryHop ? 2 : 3, kind: entryHop ? 'skill' : 'ghost', label, faint: true,
                    sub: `no mapping · not compared${entryHop ? '' : ` at ${event.service}`}`, title: names.join(', '),
                });
                node.entries.push(entry);
                [...nodes.values()].filter((n) => n.kind === 'evaluation').forEach((source) => link(source.id, id, 'none', 'not compared'));
                return;
            }
            (Array.isArray(event.evaluations) ? event.evaluations : []).forEach((item) => {
                if (!item || !Array.isArray(item.matchedSkills)) return;
                const key = evalKey(item.subjectType, item.action, item.resourceType);
                const sources = evaluationsFor(key, item.subjectType, item.action, item.resourceType, entry);
                sources.forEach((source) => { if (!source.entries.includes(entry)) source.entries.push(entry); });
                if (!item.matchedSkills.length) {
                    const none = nodes.get(`none:${event.service}:${key}`) || add({
                        id: `none:${event.service}:${key}`, layer: entryHop ? 2 : 3, kind: 'ghost', label: 'fits nothing',
                        sub: `at ${event.service}`, tone: 'deny',
                    });
                    none.entries.push(entry);
                    sources.forEach((source) => link(source.id, none.id, 'deny', entryHop ? 'left out' : 'not listed'));
                    return;
                }
                item.matchedSkills.forEach((name) => {
                    const node = entryHop
                        ? capability(event.service, name, 2, 'skill', `skill of ${event.capability_name || 'the agent'}`)
                        : capability(event.service, name, 3, 'call', `at ${event.service}`);
                    node.entries.push(entry);
                    if (!entryHop && !node.tags.includes('listed')) node.tags.push('listed');
                    node.tone = 'ok';
                    sources.forEach((source) => link(source.id, node.id, 'ok', 'fits'));
                });
            });
        });
        // No record of the entry gateway's comparison: the console's own
        // reading of the agent card, marked as a preview.
        const preview = ownRecord && ownRecord.event.discovery;
        if (preview && !fits.some(isEntryHop) && Array.isArray(preview.per_evaluation) && listed && listed.entry === ownRecord) {
            const evaluationNodes = [...nodes.values()].filter((n) => n.kind === 'evaluation');
            preview.per_evaluation.forEach((skills, i) => {
                (Array.isArray(skills) ? skills : []).forEach((name) => {
                    const node = capability('preview', name, 2, 'skill', 'preview by the console');
                    node.entries = [ownRecord];
                    if (evaluationNodes[i]) link(evaluationNodes[i].id, node.id, 'none', 'would fit');
                });
            });
        }

        // The calls a gateway checked against the intent in the token.
        usages.forEach((entry) => {
            const event = entry.event;
            const name = event.capability_name || event.skill_id || 'call';
            const node = capability(event.service, name, 3, 'call', `at ${event.service}`);
            node.entries.push(entry);
            if (notCompared(entry)) {
                // A call of a tool without a mapping: it went on, unchecked.
                node.tags.push('call not checked');
                node.faint = node.tone === 'none';
                return;
            }
            node.faint = false;
            node.tags.push(entry.tone === 'deny' ? 'call refused' : 'call checked');
            node.tone = worst([node.tone === 'none' ? 'ok' : node.tone, entry.tone]);
            const intent = Array.isArray(event.current_intent) ? event.current_intent : [];
            intent.forEach((ev) => {
                (byKey.get(keyOfEvaluation(ev)) || []).forEach((source) => {
                    link(source.id, node.id, entry.tone === 'deny' ? 'deny' : 'ok', entry.tone === 'deny' ? 'does not cover' : 'covers');
                });
            });
        });

        // What the gateway asked the platform about the data a call named.
        answers.forEach((entry) => {
            const event = entry.event;
            (Array.isArray(event.resolved_evaluations) ? event.resolved_evaluations : []).forEach((r) => {
                if (!r) return;
                const id = `ans:${r.subjectID}|${r.action}|${r.resourceType}:${r.resourceID}`;
                const tone = r.allowed === true ? 'ok' : r.allowed === false ? 'deny' : 'warn';
                const node = nodes.get(id) || add({
                    id, layer: 4, kind: 'answer', label: `${r.action || '?'} ${r.resourceType || '?'}`,
                    sub: `${r.resourceID || ''} · ${{ ok: 'allowed', deny: 'denied', warn: 'no answer' }[tone]}`, tone,
                });
                node.entries.push(entry);
                const calls = [...nodes.values()].filter((n) => n.kind === 'call' && n.service === event.service
                    && n.tags.some((t) => t.startsWith('call')));
                const sources = calls.length ? calls : (byKey.get(evalKey(r.subjectType, r.action, r.resourceType)) || []);
                sources.forEach((source) => link(source.id, node.id, tone, 'asked'));
            });
        });

        nodes.forEach((node) => {
            if (node.kind === 'evaluation') link('prompt', node.id, 'intent', '');
            if (node.tags && node.tags.length) node.sub = `${node.tags.filter((t, i, a) => a.indexOf(t) === i).join(' · ')} ${node.sub}`;
        });

        // One column per step that has something in it; a column is centred.
        const layers = [0, 1, 2, 3, 4].map((layer) => [...nodes.values()].filter((n) => n.layer === layer)).filter((l) => l.length);
        const pos = {};
        layers.forEach((column, x) => {
            column.forEach((node, i) => { pos[node.id] = { x: x * INTENT.column, y: (i - (column.length - 1) / 2) * INTENT.row }; });
        });

        // The six steps of the prompt, for the strip above the graph.
        const cards = (list) => list.map((e) => e.card);
        const steps = [];
        steps.push({ title: 'Prompt', state: run.prompt ? 'ok' : 'idle', text: run.prompt ? 'sent to the entry gateway' : 'not recorded', cards: [] });
        if (ownRecord) {
            const n = Array.isArray(ownRecord.event.evaluations) ? ownRecord.event.evaluations.length : 0;
            const ms = ownRecord.event.latency_ms;
            steps.push({
                title: 'Intent Agent', state: ownRecord.tone === 'ok' ? 'ok' : ownRecord.tone, cards: cards(promptEntries),
                text: ownRecord.tone === 'ok' ? `${plural(n, 'evaluation')}${ms ? ` · ${Math.round(ms)} ms` : ''}`
                    : `no intent${ownRecord.event.reason ? `: ${ownRecord.event.reason}` : ''}`,
            });
        } else if (asked.length) {
            const last = asked[asked.length - 1];
            steps.push({
                title: 'Intent Agent', state: last.tone === 'ok' ? 'ok' : last.tone, cards: cards(asked),
                text: `${last.tone === 'ok' ? 'intent found' : 'no intent'}${last.event.duration_ms ? ` · ${last.event.duration_ms} ms` : ''}`,
            });
        } else {
            steps.push({ title: 'Intent Agent', state: 'idle', text: 'not asked', cards: [] });
        }
        const entryFits = fits.filter(isEntryHop);
        const compared = entryFits.filter((e) => !notCompared(e));
        const uncompared = [...new Set(entryFits.filter(notCompared).flatMap((e) => namedIn(e.event)))];
        if (compared.length) {
            const items = compared.flatMap((e) => (Array.isArray(e.event.evaluations) ? e.event.evaluations : []).filter((i) => i && Array.isArray(i.matchedSkills)));
            const fitting = items.filter((i) => i.matchedSkills.length).length;
            steps.push({
                title: 'Entry gateway', cards: cards(entryFits),
                state: worst(compared.map((e) => e.tone)) === 'deny' || !fitting ? 'deny' : 'ok',
                text: `${fitting} of ${plural(items.length, 'evaluation')} fit a skill`
                    + (uncompared.length ? ` · not compared with ${uncompared.join(', ')}` : ''),
            });
        } else if (uncompared.length) {
            // Only the "not compared" record arrived: the intent was let through, not checked.
            steps.push({
                title: 'Entry gateway', state: 'info', cards: cards(entryFits),
                text: `not compared: ${uncompared.join(', ')} ${uncompared.length === 1 ? 'has' : 'have'} no mapping`,
            });
        } else if (preview) {
            steps.push({
                title: 'Entry gateway', state: 'idle', cards: cards([ownRecord]),
                text: preview.decision ? `no record; preview: ${preview.matched_skills.join(', ')}` : 'no record; preview: fits no skill',
            });
        } else {
            steps.push({ title: 'Entry gateway', state: 'idle', text: 'no comparison recorded', cards: [] });
        }
        if (carried.length) {
            const n = window.Ledger.tokenIntent(carried[carried.length - 1].token).length;
            steps.push({ title: 'Token', state: 'ok', text: `${plural(n, 'evaluation')} in act.intent`, cards: cards(carried) });
        } else {
            steps.push({ title: 'Token', state: 'idle', text: 'no token with an intent seen', cards: [] });
        }
        const downstream = usages.concat(fits.filter((e) => !isEntryHop(e)));
        if (downstream.length) {
            const checked = usages.filter((e) => !notCompared(e));
            const unchecked = usages.length - checked.length;
            const refused = checked.filter((e) => e.tone === 'deny').length;
            const narrowed = fits.some((e) => !isEntryHop(e) && !notCompared(e));
            const parts = [];
            if (checked.length) parts.push(`${plural(checked.length, 'call')} checked${refused ? `, ${refused} refused` : ''}`);
            if (unchecked) parts.push(`${unchecked} not checked (no mapping)`);
            if (narrowed) parts.push('lists narrowed');
            if (!parts.length) parts.push('lists not compared (no mapping)');
            steps.push({
                title: 'MCP gateway', state: refused ? 'deny' : checked.length || narrowed ? 'ok' : 'info',
                text: parts.join(' · '), cards: cards(downstream),
            });
        } else {
            steps.push({ title: 'MCP gateway', state: 'idle', text: 'no call checked', cards: [] });
        }
        const resolved = answers.flatMap((e) => (Array.isArray(e.event.resolved_evaluations) ? e.event.resolved_evaluations : []));
        if (resolved.length) {
            const allowed = resolved.filter((r) => r && r.allowed === true).length;
            const denied = resolved.filter((r) => r && r.allowed === false).length;
            steps.push({
                title: 'Platform', state: denied ? 'deny' : 'ok', cards: cards(answers),
                text: `${allowed} allowed, ${denied} denied`,
            });
        } else {
            steps.push({ title: 'Platform', state: 'idle', text: 'not asked about data', cards: [] });
        }
        return { nodes, root: null, edges, pos, dag: true, steps, empty: !hasAny };
    }

    // ---------------------------------------------------------------
    // Drawing: cards on a dark stage, joined by curves
    // ---------------------------------------------------------------

    // A tidy tree: a leaf takes the next free branch slot, a parent sits
    // midway between its first and last child.
    function positions(graph, shape) {
        const children = new Map();
        graph.nodes.forEach((node) => {
            if (node.parent === null || !graph.nodes.has(node.parent)) return;
            if (!children.has(node.parent)) children.set(node.parent, []);
            children.get(node.parent).push(node.id);
        });
        const out = {};
        let slot = 0;
        const place = (id, depth) => {
            const kids = children.get(id) || [];
            let y;
            if (!kids.length) {
                y = slot;
                slot += 1;
            } else {
                const ys = kids.map((kid) => place(kid, depth + 1));
                y = (ys[0] + ys[ys.length - 1]) / 2;
            }
            out[id] = shape.vertical
                ? { x: y * shape.branch, y: depth * shape.hop }
                : { x: depth * shape.hop, y: y * shape.branch };
            return y;
        };
        place(graph.root, 0);
        return out;
    }

    // The links of a graph: a tree's come from each node's parent.
    function linksOf(graph) {
        if (graph.dag) return graph.edges;
        const links = [];
        graph.nodes.forEach((node) => {
            if (node.parent !== null && graph.nodes.has(node.parent)) {
                links.push({ source: node.parent, target: node.id, tone: node.edge.tone, label: node.edge.label });
            }
        });
        return links;
    }

    // One node as a card: what it is, its name, and what is known about it.
    function cardOf(node) {
        const faint = node.faint || node.kind === 'ghost' || node.placeholder
            || ((node.kind === 'agent' || node.kind === 'server') && node.tone === 'none');
        const card = el('button', `gnode k-${node.kind} t-${node.tone}${faint ? ' faint' : ''}`);
        card.type = 'button';
        card.dataset.id = node.id;
        card.style.width = `${WIDTH[node.kind] || 180}px`;
        if (node.title) card.title = node.title;
        const kicker = KICKER[node.kind];
        if (kicker) {
            const head = el('span', 'gnode-head');
            head.appendChild(el('span', 'gnode-kicker', kicker));
            if (MARK[node.tone]) head.appendChild(el('span', 'gnode-mark', MARK[node.tone]));
            card.appendChild(head);
        }
        if (node.kind === 'evaluation') {
            // subject -> action -> resource, the action set apart.
            const parts = node.label.split(' → ');
            const line = el('span', 'gnode-triple');
            parts.forEach((part, i) => {
                if (i) line.appendChild(el('span', 'gnode-arrow', '→'));
                line.appendChild(el('span', i === 1 ? 'gnode-action' : 'gnode-entity', part));
            });
            card.appendChild(line);
        } else if (node.kind === 'prompt') {
            card.appendChild(el('span', 'gnode-title', `“${node.label}”`));
        } else {
            card.appendChild(el('span', 'gnode-title', node.label));
        }
        if (node.chips && node.chips.length) {
            const chips = el('span', 'gnode-chips');
            node.chips.forEach((chip) => chips.appendChild(el('span', 'gnode-chip', chip)));
            card.appendChild(chips);
        } else if (node.sub) {
            card.appendChild(el('span', 'gnode-sub', node.sub));
        }
        card.addEventListener('click', (e) => {
            e.stopPropagation();
            select(node.id, true);
        });
        return card;
    }

    function svgEl(tag, attrs) {
        const node = document.createElementNS(SVG_NS, tag);
        Object.entries(attrs || {}).forEach(([k, v]) => node.setAttribute(k, v));
        return node;
    }

    function arrowDefs() {
        const defs = svgEl('defs');
        ['ok', 'deny', 'warn', 'intent', 'none', 'info'].forEach((tone) => {
            const marker = svgEl('marker', {
                id: `garrow-${tone}`, viewBox: '0 0 10 10', refX: '8.5', refY: '5', markerWidth: '7', markerHeight: '7',
                orient: 'auto-start-reverse', class: `garrow t-${tone}`,
            });
            marker.appendChild(svgEl('path', { d: 'M 0 1 L 9 5 L 0 9 z' }));
            defs.appendChild(marker);
        });
        return defs;
    }

    // A point on the cubic curve p0 -> p3 with controls p1, p2.
    function curvePoint(p0, p1, p2, p3, t) {
        const u = 1 - t;
        const f = (a, b, c, d) => u * u * u * a + 3 * u * u * t * b + 3 * u * t * t * c + t * t * t * d;
        return { x: f(p0.x, p1.x, p2.x, p3.x), y: f(p0.y, p1.y, p2.y, p3.y) };
    }

    function ensureWorld() {
        if (world) return world;
        const stage = byId('graphCanvas');
        world = el('div', 'graph-world');
        stage.appendChild(world);

        // Wheel zooms about the pointer; dragging the background pans.
        stage.addEventListener('wheel', (e) => {
            if (!drawn.bounds) return;
            e.preventDefault();
            const rect = stage.getBoundingClientRect();
            const mx = e.clientX - rect.left;
            const my = e.clientY - rect.top;
            const k = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, view.k * Math.exp(-e.deltaY * 0.0015)));
            view = { x: mx - (mx - view.x) * (k / view.k), y: my - (my - view.y) * (k / view.k), k };
            userMoved = true;
            applyView(false);
        }, { passive: false });
        stage.addEventListener('pointerdown', (down) => {
            if (down.button !== 0 || down.target.closest('.gnode')) return;
            const start = { x: down.clientX, y: down.clientY, vx: view.x, vy: view.y };
            let moved = false;
            stage.setPointerCapture(down.pointerId);
            stage.classList.add('panning');
            const move = (e) => {
                const dx = e.clientX - start.x;
                const dy = e.clientY - start.y;
                if (!moved && Math.hypot(dx, dy) < 4) return;
                moved = true;
                userMoved = true;
                view = { x: start.vx + dx, y: start.vy + dy, k: view.k };
                applyView(false);
            };
            const up = () => {
                stage.removeEventListener('pointermove', move);
                stage.removeEventListener('pointerup', up);
                stage.removeEventListener('pointercancel', up);
                stage.classList.remove('panning');
                if (!moved) select(null, false);   // a plain click on the background clears the selection
            };
            stage.addEventListener('pointermove', move);
            stage.addEventListener('pointerup', up);
            stage.addEventListener('pointercancel', up);
        });
        return world;
    }

    function applyView(animate) {
        if (!world) return;
        world.classList.toggle('gliding', !!animate);
        world.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.k})`;
    }

    function clearStage() {
        if (world) world.innerHTML = '';
        drawn = { nodes: new Map(), edges: [], bounds: null, key: drawn.key };
        lastSignature = '';
    }

    // Put the graph on the stage: cards first (their sizes decide where the
    // curves attach), then one curve and one label per link.
    function draw(graph, shape) {
        const layer = ensureWorld();
        const pos = graph.dag ? graph.pos : positions(graph, shape);
        const vertical = !graph.dag && shape.vertical;
        const key = `${mode}|${runId}|${gatewayId}`;
        // Cards appear one after the other the first time a graph is shown;
        // a record arriving later only brings in its own card.
        const known = key === drawn.key ? new Set(drawn.nodes.keys()) : new Set();
        const fresh = key !== drawn.key;
        layer.innerHTML = '';
        const svg = svgEl('svg', { class: 'gedges', width: '1', height: '1' });
        svg.appendChild(arrowDefs());
        layer.appendChild(svg);

        const nodes = new Map();
        const box = {};
        let order = 0;
        graph.nodes.forEach((node) => {
            const at = pos[node.id] || { x: 0, y: 0 };
            const card = cardOf(node);
            card.style.left = `${at.x}px`;
            card.style.top = `${at.y}px`;
            if (!known.has(node.id)) {
                card.classList.add('enter');
                card.style.animationDelay = `${fresh ? Math.min(order, 14) * 35 : 0}ms`;
            }
            order += 1;
            layer.appendChild(card);
            nodes.set(node.id, card);
            box[node.id] = { x: at.x, y: at.y, w: card.offsetWidth, h: card.offsetHeight };
        });

        const edges = [];
        linksOf(graph).forEach((link) => {
            const a = box[link.source];
            const b = box[link.target];
            if (!a || !b) return;
            let p0;
            let p1;
            let p2;
            let p3;
            if (vertical) {
                p0 = { x: a.x, y: a.y + a.h / 2 };
                p3 = { x: b.x, y: b.y - b.h / 2 - 3 };
                const mid = (p0.y + p3.y) / 2;
                p1 = { x: p0.x, y: mid };
                p2 = { x: p3.x, y: mid };
            } else {
                p0 = { x: a.x + a.w / 2, y: a.y };
                p3 = { x: b.x - b.w / 2 - 3, y: b.y };
                const mid = (p0.x + p3.x) / 2;
                p1 = { x: mid, y: p0.y };
                p2 = { x: mid, y: p3.y };
                // A link that skips a column arcs around the cards between.
                if (graph.dag && Math.round((b.x - a.x) / INTENT.column) > 1) {
                    const arc = (a.y + b.y <= 0 ? -1 : 1) * INTENT.row * 0.8;
                    const reach = (p3.x - p0.x) * 0.3;
                    p0 = { x: a.x + a.w / 2 - 14, y: a.y + Math.sign(arc) * a.h / 2 };
                    p3 = { x: b.x - b.w / 2 + 14, y: b.y + Math.sign(arc) * (b.h / 2 + 3) };
                    p1 = { x: p0.x + reach, y: p0.y + arc };
                    p2 = { x: p3.x - reach, y: p3.y + arc };
                }
            }
            const path = svgEl('path', {
                class: `gedge t-${link.tone}`, 'marker-end': `url(#garrow-${link.tone})`,
                d: `M ${p0.x} ${p0.y} C ${p1.x} ${p1.y}, ${p2.x} ${p2.y}, ${p3.x} ${p3.y}`,
            });
            svg.appendChild(path);
            let label = null;
            if (link.label) {
                const at = curvePoint(p0, p1, p2, p3, 0.62);
                label = el('span', `gedge-label t-${link.tone}`, link.label);
                label.style.left = `${at.x}px`;
                label.style.top = `${at.y}px`;
                layer.appendChild(label);
            }
            edges.push({ source: link.source, target: link.target, path, label });
        });

        const all = Object.values(box);
        const bounds = all.length ? {
            minX: Math.min(...all.map((n) => n.x - n.w / 2)), maxX: Math.max(...all.map((n) => n.x + n.w / 2)),
            minY: Math.min(...all.map((n) => n.y - n.h / 2)), maxY: Math.max(...all.map((n) => n.y + n.h / 2)),
        } : null;
        drawn = { nodes, edges, bounds, key };
    }

    function fit(animate) {
        const stage = byId('graphCanvas');
        const b = drawn.bounds;
        if (!b || !stage.clientWidth || !stage.clientHeight) return;
        // The details of a selection lie over the stage: beside the graph, or
        // under it in intent mode, where the graph is wide and flat.
        const info = byId('graphInfo');
        const open = !info.classList.contains('hinting');
        const side = open && mode !== 'intent' ? info.offsetWidth + 16 : 0;
        const under = open && mode === 'intent' ? info.offsetHeight + 10 : 0;
        const roomX = stage.clientWidth - 2 * FIT_PADDING.x - side;
        const roomY = stage.clientHeight - FIT_PADDING.top - FIT_PADDING.bottom - under;
        const k = Math.max(MIN_ZOOM, Math.min(
            roomX / Math.max(b.maxX - b.minX, 1),
            roomY / Math.max(b.maxY - b.minY, 1),
            MAX_FIT_ZOOM,
        ));
        view = {
            k,
            x: FIT_PADDING.x + roomX / 2 - k * (b.minX + b.maxX) / 2,
            y: FIT_PADDING.top + roomY / 2 - k * (b.minY + b.maxY) / 2,
        };
        userMoved = false;
        applyView(animate !== false);
    }

    // ---------------------------------------------------------------
    // Selection: the path back to the root, and the records in the ledger
    // ---------------------------------------------------------------

    function pathTo(id) {
        const ids = [];
        let node = model && model.nodes.get(id);
        while (node) {
            ids.push(node.id);
            node = node.parent === null ? null : model.nodes.get(node.parent);
        }
        return ids;
    }

    function recordLine(entry) {
        const event = entry.event;
        const time = new Date(event.timestamp);
        const stamp = isNaN(time.getTime()) ? '' : time.toLocaleTimeString([], { hour12: false });
        const line = el('div', `graph-record ${entry.tone}`);
        line.append(
            el('span', 'graph-record-time', stamp),
            el('span', 'graph-record-tag', tagOf(entry)),
            el('span', 'graph-record-decision', (entry.decision || '').replace(/_/g, ' ')),
        );
        const reason = entry.decision === 'token_exchanged' ? '' : (event.reason || event.reason_code || '');
        if (reason) line.appendChild(el('span', 'graph-record-reason', reason));
        line.addEventListener('click', () => window.Ledger.focus([entry.card]));
        line.title = 'Show this record in the audit terminal';
        return line;
    }

    const KIND_TEXT = {
        prompt: 'what the person asked', evaluation: 'one question the prompt implies',
        skill: 'skill the evaluation fits', call: 'tool or skill at a gateway that checks calls against the intent', answer: 'the platform\'s answer about the data a call named',
        person: 'the person the request is for', gateway: 'Agent Gateway', agent: 'agent', server: 'MCP server',
        capability: 'capability', intent: 'asked by the entry gateway about the prompt', group: 'kind of capability',
        ghost: 'not shown to this caller',
    };

    function renderInfo(node) {
        const info = byId('graphInfo');
        info.innerHTML = '';
        info.classList.toggle('hinting', !node);
        if (!node) {
            info.appendChild(el('div', 'graph-info-hint', {
                request: 'Select a node: its path back to the person is the delegation chain, and its records are marked in the audit terminal.',
                capabilities: 'Select an entry to see the records that permitted, used or refused it.',
                intent: 'Select a node to follow one evaluation from the prompt to the platform\'s answer; a step above points at its records.',
            }[mode]));
            return;
        }
        const head = el('div', 'graph-info-head');
        head.append(el('span', 'graph-info-title', node.label), el('span', 'graph-info-kind', KIND_TEXT[node.kind] || node.kind));
        if (node.title && node.title !== node.label) head.appendChild(el('span', 'graph-info-id', node.title));
        info.appendChild(head);
        if (mode === 'request') {
            const chain = pathTo(node.id).reverse().map((id) => model.nodes.get(id))
                .filter((n) => ['person', 'agent', 'server'].includes(n.kind)).map((n) => n.label);
            // A gateway decides about the chain that ends with the party behind it.
            const behind = node.kind === 'gateway' && node.agentId ? model.nodes.get(node.agentId) : null;
            if (behind) chain.push(behind.label);
            if (chain.length > 1) info.appendChild(el('div', 'graph-info-chain', `chain: ${chain.join(' → ')}`));
            if (node.kind === 'gateway' && capabilityGateways(requestModel).includes(node)) {
                const open = el('button', 'graph-info-link', 'capabilities of this hop →');
                open.type = 'button';
                open.addEventListener('click', () => { gatewayId = node.id; setMode('capabilities'); });
                info.appendChild(open);
            }
        }
        if (!node.entries.length) {
            info.appendChild(el('div', 'graph-info-hint', node.kind === 'person'
                ? 'Every record of this request names this subject.'
                : 'No record of its own: it appears in the chain of a later hop.'));
            return;
        }
        const list = el('div', 'graph-records');
        node.entries.forEach((entry) => list.appendChild(recordLine(entry)));
        info.appendChild(list);
    }

    // Everything a node came from and everything it led to (intent mode).
    function reachOf(id) {
        const keep = new Set([id]);
        const walk = (from, forward, seen) => {
            drawn.edges.forEach((edge) => {
                const next = forward ? (edge.source === from ? edge.target : null) : (edge.target === from ? edge.source : null);
                if (next && !seen.has(next)) { seen.add(next); walk(next, forward, seen); }
            });
        };
        // Down and up are walked separately: a node reached going down must
        // not stop the way up.
        const down = new Set([id]);
        const up = new Set([id]);
        walk(id, true, down);
        walk(id, false, up);
        down.forEach((x) => keep.add(x));
        up.forEach((x) => keep.add(x));
        return keep;
    }

    function select(id, focusLedger) {
        selectedId = id && model && model.nodes.has(id) ? id : null;
        drawn.nodes.forEach((card) => card.classList.remove('dim', 'sel'));
        drawn.edges.forEach((edge) => {
            edge.path.classList.remove('dim', 'path');
            if (edge.label) edge.label.classList.remove('dim');
        });
        const node = selectedId ? model.nodes.get(selectedId) : null;
        // The root of a tree has no path to mark: selecting it dims nothing.
        if (node && drawn.nodes.has(selectedId) && (model.dag || node.parent !== null)) {
            const keep = model.dag ? reachOf(selectedId) : new Set(pathTo(selectedId));
            drawn.nodes.forEach((card, nodeId) => card.classList.toggle('dim', !keep.has(nodeId)));
            drawn.edges.forEach((edge) => {
                const on = keep.has(edge.source) && keep.has(edge.target);
                edge.path.classList.toggle('path', on);
                edge.path.classList.toggle('dim', !on);
                if (edge.label) edge.label.classList.toggle('dim', !on);
            });
        }
        if (node) {
            const card = drawn.nodes.get(selectedId);
            if (card) card.classList.add('sel');
            if (focusLedger) window.Ledger.focus(node.entries.map((e) => e.card));
        }
        const wasOpen = !byId('graphInfo').classList.contains('hinting');
        renderInfo(node);
        // The details opening or closing changes the room the graph has.
        if (wasOpen !== !!node && !userMoved) fit(true);
    }

    // ---------------------------------------------------------------
    // The view
    // ---------------------------------------------------------------

    function graphRuns() {
        return window.Ledger.runs().filter((run) => !run.system);
    }

    function syncControls(runs, gateways) {
        const select_ = byId('graphRun');
        const wanted = runs.map((run) => `${run.id}|${run.time}|${run.who}|${run.prompt}`).join('\n');
        if (select_.dataset.sig !== wanted) {
            select_.dataset.sig = wanted;
            select_.innerHTML = '';
            runs.forEach((run) => {
                const prompt = run.prompt ? ` · ${run.prompt.length > 60 ? `${run.prompt.slice(0, 60)}…` : run.prompt}` : '';
                const option = el('option', '', `${run.time} · ${run.who}${prompt}`);
                option.value = run.id;
                select_.appendChild(option);
            });
        }
        if (runId) select_.value = runId;
        byId('graphLatest').checked = followLatest;
        document.querySelectorAll('#graphModes [data-mode]').forEach((b) => {
            b.setAttribute('aria-pressed', b.dataset.mode === mode ? 'true' : 'false');
        });
        byId('graphLayer').dataset.mode = mode;
        const gatewaySelect = byId('graphGateway');
        gatewaySelect.hidden = mode !== 'capabilities' || !gateways.length;
        const sig = gateways.map((g) => g.id).join('\n');
        if (gatewaySelect.dataset.sig !== sig) {
            gatewaySelect.dataset.sig = sig;
            gatewaySelect.innerHTML = '';
            gateways.forEach((g) => {
                const party = requestModel.nodes.get(g.agentId);
                const option = el('option', '', `${g.label}${party ? ` → ${party.label}` : ''}`);
                option.value = g.id;
                gatewaySelect.appendChild(option);
            });
        }
        if (gatewayId) gatewaySelect.value = gatewayId;
    }

    function showEmpty(text) {
        const empty = byId('graphEmpty');
        empty.textContent = text || '';
        empty.hidden = !text;
        if (text) clearStage();
    }

    function render() {
        if (!visible) { dirty = true; return; }
        dirty = false;
        const runs = graphRuns();
        if (followLatest || !runs.some((r) => r.id === runId)) {
            const latest = runs.length ? runs[runs.length - 1].id : null;
            if (latest !== runId) { runId = latest; selectedId = null; gatewayId = null; userMoved = false; }
        }
        const run = runs.find((r) => r.id === runId);
        const prompt = byId('graphPrompt');
        prompt.textContent = run && run.prompt ? run.prompt : '';
        prompt.hidden = !(run && run.prompt);
        renderStatus();
        if (!run) {
            requestModel = model = null;
            renderPipeline(null);
            syncControls(runs, []);
            clearStage();
            lastSignature = '';
            showEmpty('No request yet. Send a prompt: its graph is drawn here as the records arrive.');
            renderInfo(null);
            return;
        }
        requestModel = buildRequest(run);
        const gateways = capabilityGateways(requestModel);
        let shape = REQUEST;
        renderPipeline(null);
        if (mode === 'intent') {
            model = buildIntent(run);
            syncControls(runs, gateways);
            renderPipeline(model.steps);
            if (model.empty) {
                model = null;
                clearStage();
                lastSignature = '';
                showEmpty(ibacStatus && ibacStatus.enabled === false
                    ? 'IBAC is off: no gateway asked the Intent Agent about this prompt.'
                    : 'No intent record in this request: no gateway asked the Intent Agent about its prompt.');
                renderInfo(null);
                return;
            }
        } else if (mode === 'capabilities') {
            const gateway = gateways.find((g) => g.id === gatewayId) || gateways[0];
            gatewayId = gateway ? gateway.id : null;
            syncControls(runs, gateways);
            if (!gateway) {
                model = null;
                clearStage();
                lastSignature = '';
                showEmpty('No gateway recorded a discovery or an invocation in this request.');
                renderInfo(null);
                return;
            }
            model = buildCapabilities(requestModel, gateway);
            shape = CAPABILITIES;
        } else {
            model = requestModel;
            syncControls(runs, gateways);
        }
        showEmpty(run.entries.length ? '' : 'Waiting for the first record of this request…');
        const signature = `${mode}|${runId}|${gatewayId}|${JSON.stringify([
            [...model.nodes.values()].map((n) => [n.id, n.kind, n.label, n.sub, n.tone, n.parent, n.placeholder, n.faint, n.edge]),
            model.dag ? [model.edges, model.pos] : null,
        ])}`;
        if (signature !== lastSignature) {
            const fresh = `${mode}|${runId}|${gatewayId}` !== drawn.key;
            lastSignature = signature;
            draw(model, shape);
            if (!userMoved) fit(!fresh);
        }
        select(selectedId, false);
    }

    // The six steps of the prompt (intent mode), each pointing at its records.
    function renderPipeline(steps) {
        const strip = byId('graphPipeline');
        strip.innerHTML = '';
        strip.hidden = !steps;
        if (!steps) return;
        steps.forEach((step, i) => {
            const item = el('button', `graph-step ${step.state}`);
            item.type = 'button';
            item.disabled = !step.cards.length;
            item.title = step.cards.length ? `${step.text}\nClick to show the records of this step in the audit terminal` : step.text;
            item.append(el('span', 'graph-step-n', String(i + 1)), el('span', 'graph-step-title', step.title), el('span', 'graph-step-text', step.text));
            item.addEventListener('click', () => window.Ledger.focus(step.cards));
            strip.appendChild(item);
        });
    }

    // Whether IBAC is on and the Intent Agent answers, so an empty intent
    // view explains itself.
    function renderStatus() {
        const strip = byId('graphIbac');
        strip.hidden = mode !== 'intent';
        if (mode !== 'intent') return;
        if (Date.now() - ibacStatusAt > STATUS_TTL_MS) {
            ibacStatusAt = Date.now();
            fetch(`${ctx.apiUrl}/api/ibac/status`, { credentials: 'include' })
                .then((res) => (res.ok ? res.json() : null))
                .then((data) => { ibacStatus = data; if (mode === 'intent') { paintStatus(); render(); } })
                .catch(() => { ibacStatus = null; paintStatus(); });
        }
        paintStatus();
    }

    function paintStatus() {
        const strip = byId('graphIbac');
        strip.innerHTML = '';
        if (!ibacStatus) {
            strip.appendChild(el('span', 'graph-ibac-item idle', 'IBAC status unknown'));
            return;
        }
        const item = (text, state, title) => {
            const node = el('span', `graph-ibac-item ${state}`, text);
            if (title) node.title = title;
            strip.appendChild(node);
        };
        item(ibacStatus.enabled ? 'IBAC on' : 'IBAC off', ibacStatus.enabled ? 'ok' : 'idle',
            ibacStatus.enabled ? 'The gateways ask the Intent Agent about every prompt (IBAC_ENABLED=true)'
                : 'The gateways do not ask the Intent Agent (IBAC_ENABLED=false): this view stays empty');
        item(`unmapped skills: ${ibacStatus.unmapped}`, 'idle',
            'What a skill or tool without a mapping means to the entry gateway (IBAC_UNMAPPED): allow = it fits every intent');
        const agent = ibacStatus.agent;
        item(`Intent Agent ${{ up: 'reachable', down: 'not reachable', off: 'not started', unknown: 'not checked' }[agent] || agent}`,
            { up: 'ok', down: 'deny' }[agent] || 'idle',
            agent === 'off' ? 'The compose profile "intent" is not enabled' : 'Health endpoint of the Intent Agent, asked by the console');
    }

    function setMode(next) {
        mode = ['capabilities', 'intent'].includes(next) ? next : 'request';
        selectedId = null;
        userMoved = false;
        render();
    }

    // From a card's "graph" button: its request, with the record's node selected.
    function show(targetRun, entry) {
        followLatest = false;
        runId = targetRun;
        userMoved = false;
        mode = !entry ? 'request' : { capability: 'capabilities', intent: 'intent', data: 'intent' }[entry.kind] || 'request';
        selectedId = null;
        gatewayId = null;
        ctx.setLayer('graph');
        const run = graphRuns().find((r) => r.id === runId);
        if (!run || !entry) { render(); return; }
        if (mode === 'intent') {
            render();
            // The most specific node the record is behind: an answer or a
            // call before an evaluation, an evaluation before the prompt.
            const rank = { answer: 0, call: 1, skill: 2, ghost: 3, evaluation: 4, prompt: 5 };
            const holders = model ? [...model.nodes.values()].filter((n) => n.entries.includes(entry)) : [];
            holders.sort((a, b) => rank[a.kind] - rank[b.kind]);
            select(holders.length ? holders[0].id : null, false);
            return;
        }
        const request = buildRequest(run);
        const holder = [...request.nodes.values()].find((n) => (n.kind === 'gateway' || n.kind === 'intent') && n.entries.includes(entry));
        if (mode === 'capabilities') {
            gatewayId = holder ? holder.id : null;
            render();
            const named = model && [...model.nodes.values()].find((n) => n.kind === 'capability' && n.entries.includes(entry)
                && question(entry.event) === 'INVOCATION');
            select(named ? named.id : 'c:root', false);
        } else {
            render();
            select(holder ? holder.id : null, false);
        }
    }

    // The Graph layer became visible (or hidden): cards can be measured, and
    // the view fitted, only while it is shown.
    function setVisible(now) {
        visible = !!now;
        if (!visible) return;
        requestAnimationFrame(() => {
            const refit = dirty || !userMoved;
            lastSignature = '';
            render();
            if (refit) fit(false);
        });
    }

    function init(options) {
        ctx = Object.assign(ctx, options || {});
        byId('graphRun').addEventListener('change', (e) => {
            runId = e.target.value;
            followLatest = false;
            selectedId = null;
            gatewayId = null;
            userMoved = false;
            render();
        });
        byId('graphLatest').addEventListener('change', (e) => {
            followLatest = e.target.checked;
            render();
        });
        document.querySelectorAll('#graphModes [data-mode]').forEach((b) => {
            b.addEventListener('click', () => setMode(b.dataset.mode));
        });
        byId('graphGateway').addEventListener('change', (e) => {
            gatewayId = e.target.value;
            selectedId = null;
            userMoved = false;
            render();
        });
        byId('graphFit').addEventListener('click', () => fit(true));
        // The stage changes size with the window and with the audit pane's grip.
        new ResizeObserver(() => { if (visible && !userMoved) fit(false); }).observe(byId('graphCanvas'));
        window.Ledger.subscribe(render);
        renderInfo(null);
    }

    // What is drawn right now, as plain data (the tests read it).
    function snapshot() {
        return {
            mode, runId, gatewayId, selectedId,
            nodes: model ? [...model.nodes.values()].map((n) => ({
                id: n.id, kind: n.kind, label: n.label, sub: n.sub, tone: n.tone, parent: n.parent,
                edge: n.edge.label, records: n.entries.length,
            })) : [],
            drawn: drawn.nodes.size,
        };
    }

    window.Graphs = { init, show, setVisible, snapshot };
})();
