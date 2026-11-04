# ibac-demo - plan

A copy of `iag-token-exchange` that keeps the whole demo (console, orchestrated
A2A agents behind Agent Gateways, Token Service delegation, internal IndyKite
MCP, external Google Drive MCP, Salesforce CRM, graph-filtered ERP database)
and changes three things:

1. **People come from Auth0.** The identities are the music demo's Person
   nodes; every `external_id` is an Auth0 subject (`auth0|<id>`). Curity is gone.
2. **The Intent Agent sits in the flow.** The orchestrator gateway asks it for
   the intent of every prompt (gateway `intent_agent` section).
3. **IBAC** (intent-based access control) is shown as far as the gateway
   supports it, and prepared for the release that enforces it.

Dataset: `instant-stack/data/ibac` (`DATASET=ibac`). Org name stays CanBank.

## Part 1 - done

- `a2a/ibac-demo/` = `a2a/iag-token-exchange/` minus caches, `.env`,
  `token-service/token-service.yaml`, Drive credentials and the Salesforce key
  (the `.gitkeep` directories are kept). Nothing inside is changed yet; the
  images still build as `:token-exchange` and the host ports are the same, so
  the two apps cannot run at once until part 2 step 1.
- `instant-stack/data/ibac/` generated from `canbank-ts` + `intent` +
  `insurance-ts` (CRM workflow) + the music people. Summary below.

## Identity design (Auth0 replaces Curity)

Verified in the gateway and Token Service sources (release 2.89.1):

| Fact | Consequence |
| --- | --- |
| The gateway validates the user's access token only by RFC 7662 introspection at `identity_provider.introspect_endpoint`; it never fetches a JWKS. | Auth0 has no introspection endpoint, so something must answer RFC 7662 for Auth0 tokens. |
| `identity_provider.{introspect,client_credential,exchange}_endpoint` are joined onto one `base_url` with `url.JoinPath`. | Introspection and client-credentials must live under the same base URL. |
| The Token Service `/oauth2/introspect` answers only tokens it signed itself (`IntrospectSelf`); any other issuer is `active: false`. | The Token Service cannot introspect Auth0 tokens for the gateway or for the Intent Agent. |
| The Token Service exchange validates subject and actor tokens through `token_introspection.configurations`, offline with a `jwks_uri`. | Auth0 user and agent tokens are fine as exchange inputs: one jwt matcher per issuer + audience. |
| The delegation's `act.sub` is the actor token's `sub`; the gateway resolves workflows with the actors chain (`get-agent-workflows` with `agent_id` = that `sub`). | Agent nodes must carry the agents' Auth0 subjects. An Auth0 machine-to-machine token has `sub = <client_id>@clients`. |
| The gateway's intent call gets its token by client credentials with `audience=intent_agent`; the Intent Agent introspects it by RFC 7662 at `INTROSPECT_URL`. | Same bridge serves the Intent Agent. |

### Components

```text
browser ──Auth0 login (code + PKCE, audience ibac-console)──▶ console
console ──Bearer U (Auth0 access token, aud ibac-console)──▶ orchestrator-iag
orchestrator-iag ──introspect U──▶ auth0-bridge (/oauth/introspect: verifies the JWT against the Auth0 JWKS)
orchestrator-iag ──client credentials──▶ auth0-bridge (/oauth/token → Auth0 /oauth/token, audience ibac-agents)
orchestrator-iag ──exchange U + actor──▶ token-service (Auth0 matchers, offline JWKS) ──▶ D1 (aud ibac-console)
orchestrator-iag ──client credentials (audience intent_agent)──▶ auth0-bridge ──▶ Auth0
orchestrator-iag ──A2A SendMessage, subject from whoami──▶ intent-agent ──introspect──▶ auth0-bridge
… then as in iag-token-exchange: U + Dn hop by hop to retriever / weather / analyst / crm / mcp-iag / drive / erp
platform ──Token Introspects: Auth0 issuer + ibac-console (Person), Token Service issuer + ibac-console (Person)
```

`auth0-bridge` is new and small (one Python service, no LLM): it is the
gateway's `identity_provider`. Two endpoints under one base URL:

- `POST /oauth/token` - forwards the form to Auth0 `/oauth/token` unchanged,
  adding `audience=ibac-agents` when the caller sent none (the gateway's actor
  grant sends no audience; Auth0 requires one). Basic auth passes through, so
  each gateway authenticates with its own M2M client.
- `POST /oauth/introspect` - RFC 7662 for Auth0 JWTs: checks the caller's
  basic auth against the configured M2M clients, verifies signature (tenant
  JWKS, cached), `iss`, `exp`, and answers `{active, sub, aud, iss, exp, iat,
  scope, client_id (azp), …claims}`; anything else answers `{active: false}`.

Alternative considered: a local OAuth2 server (ORY Hydra) for the agents only,
which would keep readable agent ids (`sub` = client id). Rejected for now:
Auth0 is the one IdP the demo is about, and the bridge is needed for the user
tokens anyway.

### Auth0 tenant (your hands-on steps, part 2)

The music tenant already holds the twelve users. To add (names are the
dataset's literals):

1. API `ibac-console` (identifier `ibac-console`): the audience of user access
   tokens. Token lifetime ≥ 3600 s for comfortable demos.
2. API `ibac-agents` (identifier `ibac-agents`): the audience of agent actor
   tokens.
3. API `intent_agent` (identifier `intent_agent`): what the orchestrator
   gateway asks for before calling the Intent Agent (its
   `EXPECTED_TOKEN_AUDIENCE`).
4. Regular Web Application `ibac-console`: callback
   `http://localhost:3000/auth/callback`, logout URL `http://localhost:3000/`,
   grant types authorization code (+ refresh optional). Its client id/secret go
   to `CHATBOT_IDP_CLIENT_ID/SECRET`.
5. Eight Machine-to-Machine applications, one per gateway (`orchestrator`,
   `retriever`, `weather`, `analyst`, `mcp`, `drive`, `crm`, `erp`), each
   authorized for the APIs `ibac-agents` and `intent_agent`. Their client ids
   become the agents' subjects: `AUTH0_SUB_<AGENT>=<client_id>@clients` in
   instant-stack `.env`, and `<AGENT>_IDP_CLIENT_ID/SECRET` in the app `.env`.
6. No tenant "default audience" is needed (the bridge supplies it).

Access tokens carry `sub`, `aud` (`ibac-console` plus the tenant `userinfo`
URL when `openid` is requested), `azp`, `scope`; no `email`. The platform Token
Introspects therefore map no claims and only bind `sub` to the Person node.

## Dataset `instant-stack/data/ibac`

Generated, not hand-edited; regenerate on request (the generator takes the
music people, canbank-ts, intent and insurance-ts as inputs).

- **People (26 Person)**: the twelve Auth0 users of the music tenant with their
  real subs, plus fourteen relatives from the music graph with synthetic subs
  of the same shape (`auth0|69c4…`, derived from the music id). Properties kept
  from music: `firstname`, `lastname`, `email`, `age`, `city`, `state`,
  `profession`; added `name` and, for staff, `title`. Music taste properties
  were dropped. Family edges kept: `PARENT_OF`, `MARRIED_TO`, `CO_PARENTS`,
  `PARTNERS`. Every person `IS_AGE_GROUP` → `minor` | `adult` (age bands as
  nodes, because KBAC conditions cannot compare properties).
- **Staff** (`WORKS_IN`): Millicent Contextsworth (support + trading, CSR 2,
  the main persona: direct `CAN_TRIGGER` on wf1, wf3, wf3-console, wf-drive,
  wf-drive-analyst, wf-drive-console), Vivienne Boardroom (support supervisor,
  made decision_001, manager of Millicent and Cookie), Cookie Malhotra
  (trading), Preston Capitalworth (private banking), Mild Concern (risk),
  BetaTest McAllister and ApiCall Jackson-Lee (tech), Marmaduke Doomington
  (ops; direct `CAN_TRIGGER` wf1 only - the "denied on wf3" persona).
- **Customers**: a Person `IS_CUSTOMER` a `Customer` record contained in
  `sys_canbank_core` and served by a department. Bambi Stardust (chequing,
  mortgage; invoices 001-002), Cornelius Pickleworth 14 and Eustorgio Rodriguez
  15 (youth savings; invoices 003, 004), Marmaduke (401k, personal loan;
  invoices 005-006), Cookie (chequing, investing, late credit card; invoices
  007-008; ticket_002), Cashmere WiFi Malhotra 20 (investing, paid credit
  card; invoice 009; ticket_001 closed by decision_001), and the children
  Tokenina, Berengar, Humperdinck, Synergy, Quarterly-Returns (youth savings).
  Invoices carry `HAS_STATUS` → `InvoiceStatus` (open, paid, overdue).
- **Guardian scenarios**: Millicent → Tokenina (11), Marmaduke → Berengar (14)
  and Humperdinck (15), Preston and Vivienne → Synergy and Quarterly-Returns
  (17), Cookie → Cashmere (20, adult: no guardian access), Mortimer and Wilfred
  → Cornelius, Filogonio and Petronila → Eustorgio (parents without login).
- **Copied verbatim from canbank-ts**: departments and org tree, documents,
  folders, taxonomy, regulatory agreements, jurisdictions, investment
  categories, tiers, quote, weather, skills/tools, workflows wf1-wf3,
  wf3-console, wf-drive*, wf-erp*; plus `wf-crm` (support and trading can
  trigger; orchestrator → crm) from insurance-ts. Agent nodes carry a `name`
  and the placeholder subjects `${AUTH0_SUB_ORCHESTRATOR}`, `_RETRIEVER`,
  `_WEATHER`, `_ANALYST`, `_MCP`, `_DRIVE`, `_CRM`, `_ERP`.
- **KBAC (33, all subject Person, 2.0-kbac)**: gateway and app policies
  (`CAN_TRIGGER`, quote, invoices for staff/customer/guardian, chain-guarded
  `CAN_DISCLOSE`), the intent vocabulary ported from `data/intent` (staff and
  customer sets, parameterised ones with their parameter-less twins), and
  family/guardian policies (`PARENT_OF`, `HOUSEHOLD_WITH`, `GUARDIAN_OF`,
  `VIEW_DEPENDENT_ACCOUNT`).
- **CIQ (14)**: the thirteen canbank-ts queries ported to Person (`get-self`
  now also returns the customer record; explain queries keep the `u` alias the
  console parses; `store-decision` upserts `Person-MADE->Decision`; the chained
  query's token filter expects `${AUTH0_SUB_ORCHESTRATOR}`), plus
  `get-my-family`. Resolvers unchanged.
- **Platform config**: project `ibac-demo`, app agent `ibac-agent` (incl.
  `ReadAuthZConfigs` + `ReadDataSchema` for the Intent Agent), MCP server, two
  Token Introspects (Auth0 issuer `${AUTH0_ISSUER}` and Token Service issuer
  `https://token-service.ibac-demo.local`, both audience `ibac-console`, node
  type Person), audit signing + event sink as in canbank-ts.

### Needs your approval before the dataset can be provisioned

instant-stack resolves `${VAR}` placeholders only in the token-introspect and
event-sink payloads today. The `ibac` dataset also uses them in nodes,
relationships, KBAC filters and a CIQ token filter. Proposed change (small):
apply `api/_dataset.py:_resolve_env_placeholders` to the capture nodes and
relationships payloads, the KBAC form default and the CIQ policy default, and
document the keys in instant-stack `.env.example`:

```text
AUTH0_ISSUER=https://<tenant>.auth0.com/
AUTH0_SUB_ORCHESTRATOR=<client_id>@clients
AUTH0_SUB_RETRIEVER=… AUTH0_SUB_WEATHER=… AUTH0_SUB_ANALYST=… AUTH0_SUB_MCP=…
AUTH0_SUB_DRIVE=… AUTH0_SUB_CRM=… AUTH0_SUB_ERP=…
TOKEN_SERVICE_PUBLIC_JWK={"kty":"RSA",…}
```

## Part 2 - app changes (proposed order, each step approved separately)

1. **Separate the copy** - done: `makefile` `TAG := ibac-demo`, compose
   image tags, compose project `name: ibac-demo`, host ports shifted so the
   demos run side by side: console 3300, agents 7001-7006, gateways
   9881-9889, Token Service 9102, Drive MCP 9000 (in-network ports of the
   Token Service and Drive MCP stay 8102 and 8000). The agents now get
   `CHATBOT_UPDATES_URL` from compose instead of their code default.
2. **Remove Curity** - done: `iag-base-docker.yaml` identity provider →
   `http://auth0-bridge:8090` with endpoints `oauth/introspect`,
   `oauth/token`, `oauth/token`; `JARVIS_IDENTITY_PROVIDER_OPENID_CONFIGURATION_URL`
   (Auth0 discovery URL), `JARVIS_PROTECTED_AGENT_AUTHENTICATION_AUDIENCES`
   and `JARVIS_TOKEN_SERVICE_AUDIENCE` = `${AUTH0_CONSOLE_AUDIENCE}`,
   `AUTHZEN_SUBJECT_TYPES=Person` (gateways, console, ERP). The console's
   compose lines point at the tenant (`authorize`, `oauth/token`); the
   `.example.env` IdP block is now an Auth0 block (`AUTH0_DOMAIN`, the three
   API identifiers) and the client ids are the M2M applications. Gateway
   pinned to `indykite/agent-gateway:2.89.1` (the user's instruction), the
   release whose sources the audience settings, `token_service.audience`
   and the `intent_agent` section were read from.
3. **Console login on Auth0** - done: `chatbot/app.py` sends
   `ID_SERVER_AUDIENCE` on the authorize request (compose:
   `${AUTH0_CONSOLE_AUDIENCE}`) and builds the logout URL from
   `ID_SERVER_LOGOUT_ENDPOINT` (`v2/logout`) with `client_id` + `returnTo`;
   scopes stay `openid profile email` for the id_token the UI reads the
   display name from. `static/architecture.html` identity band and flow
   text describe Auth0, the bridge and the Token Service; `chatbot/README.md`
   documents the new settings. The Auth0 application must list
   `http://localhost:3300/auth/callback` as callback URL and
   `http://localhost:3300/` as allowed logout URL.
4. **auth0-bridge** - done: `auth0_bridge/` (FastAPI + PyJWT, JWKS cached),
   compose service on every gateway network, env `AUTH0_DOMAIN`,
   `AUTH0_DEFAULT_AUDIENCE=${AUTH0_AGENTS_AUDIENCE}`. Callers authenticate
   with their own M2M credentials, verified by a client-credentials grant at
   Auth0 and cached, so no client list is configured. 11 tests cover the
   RFC 7662 answer the gateway parses, inactive cases, caller auth, the
   cache and the audience passthrough. Built by `make` as
   `auth0-bridge:ibac-demo`.
5. **Token Service config** - done: `token-service/token-service.example.yaml`
   issuer `https://token-service.ibac-demo.local`, audience `ibac-console`
   only (the one every gateway asks for), two offline matchers (subject:
   Auth0 issuer + `ibac-console`; actor: Auth0 issuer + `ibac-agents`, both
   via the tenant `jwks_uri`), no opaque fallback.
6. **Intent Agent in the stack** - done: compose profile `intent` adds
   `intent-agent` (+ `intent-redis` on a private network) on the
   orchestrator gateway's network; `INTROSPECT_URL` → the bridge,
   `INTROSPECT_CLIENT_ID/SECRET` default to the orchestrator M2M client
   (`INTENT_INTROSPECT_*` to use another), `IK_APP_AGENT_KEY` = app agent
   credentials, `EXPECTED_TOKEN_AUDIENCE=${INTENT_AGENT_AUDIENCE}`, audit
   webhook → console (`no-auth`, like the gateways). `orchestrator-iag`
   gets `JARVIS_INTENT_AGENT_BASE_URL=${INTENT_AGENT_BASE_URL}` (empty =
   not asked), `_AUDIENCE`, `_TIMEOUT`; `make intent-agent-image` builds the
   image from a checkout of the public repo. Today the gateway logs the
   intent and continues; unreachable agent = 502. The console renders the
   agent's audit record (step 11).
7. **ERP** - done: `init.sql` holds the nine `inv-cb-*` rows for the new
   customers (`cust-*` ids, names as in the graph, Berengar on `inv-cb-006`),
   the SecureHome rows are gone; `ERP_SUBJECT_TYPE` follows
   `AUTHZEN_SUBJECT_TYPES` (Person, step 2).
8. **Usecase bundle** - done: `usecases/ibac/` replaces `canbank` and
   `insurance` (both removed, as was `switch-usecase.sh`; compose and
   `.example.env` default to `USECASE=ibac`). `usecase.env` (CanBank
   vocabulary), `app.json` (canbank cards plus a customer-only Family group
   and the staff Cases group from insurance), the generic skills with
   examples on the new cast, `ibac-authz` (Person triples, parameterised
   twins, the cast with Auth0 subjects and customer records), `query-crm`,
   `DEMO_SCRIPT.md` (seven acts incl. customers/guardians and CRM) and
   `SCENARIO.md` ("Cashmere's late fee"). The dataset moved `inv-cb-006`
   from Marmaduke to his minor son Berengar so the guardian invoice policy
   has a login to show it (Marmaduke sees 2 rows: own + guardian).
9. **Bruno** - done: `bruno/ibac-demo` replaces `bruno/iag-demo`, generated
   from the dataset (ingest, token introspects, 33 KBAC, 14 CIQ pairs with
   execute requests, AuthZEN probes incl. guardian/staff `search/resource`)
   plus the adapted chain-guard (Auth0 actor tokens with the agents
   audience, Token Service hops with the console audience), WF4 (Marmaduke
   as the denied person, Person subjects) and Drive folders. Environment
   `ibac-demo`: tenant values, M2M client ids/secrets, `auth0_sub_*`,
   person subjects, host ports 9xxx/3300; gids recorded by the Create
   requests. Parity is by construction: regenerate when the dataset changes.
10. **README** - done: rewritten for ibac-demo (Auth0 tenant steps, the
    bridge, the Intent Agent profile, the ibac dataset and cast, the shifted
    ports, troubleshooting for the Auth0/bridge/Token Service failure
    modes); the deep-dives (MCP gateway, Drive, grant, CRM, why?, ERP, WF4)
    kept and retold with the new personas. `.example.env` was already
    updated in steps 2 and 6.
11. **Console** - done: `/api/push-update` recognises an Intent Agent
    record (`outcome` + `prompt`) and maps it onto the terminal's event
    (decision `INTENT_FOUND` / `_UNMATCHED` / `_INVALID` / `_ERROR`, subject
    = the resolved person, actor = the requesting gateway's subject, plus
    prompt, evaluations, request_id); the terminal renders it as a violet
    INTENT card with the prompt and one line per evaluation (subject →
    action → resource · context), keyed by request id so repeats show.
    Agents are shown by name: compose feeds `AGENT_DISPLAY_NAMES`
    (`<client_id>@clients=<name>` from the `*_IDP_CLIENT_ID` values, names
    as the Agent nodes') and the cards keep the raw subject as a tooltip.

## Part 3 - IBAC

On gateway 2.89.1 the IBAC engine (`EvaluateDiscovery` / `EvaluateUsage`
over agent-card skills with `x-coaz-mappings`) is complete and audited
(`IBAC_DISCOVERY` / `IBAC_USAGE` records) but not wired into the request
path; an upcoming release adds `ibac.enabled` (requires `intent_agent` in
front of A2A, 45 s default timeout, `INTENT` audit records, the gateway answers
a failed intent with an A2A error + obligation). The Token Service already
accepts an `intent` form parameter and writes it into the hop's `act`, but the
gateway does not send it yet. The platform ignores intent entirely (AuthZEN
context reads only `input_params` and `policy_tags`).

Done (A, B, C):

- A. `usecases/ibac/skills/<agent>/coaz-mappings.json` for orchestrator
  (`query-erp`, `query-retriever`, `query-crm`), retriever (`authzen`,
  `ciq-execute`, `max-purchase-amount`), analyst (`erp-invoices`, `authzen`,
  `ciq-execute`) and crm (`create-crm-case`): subject `Person`, resource
  types with the dataset's actions, literal ids only where fixed
  (`stock_quote`), empty otherwise (matches as a search). Weather,
  web-search and the meta skills stay unmapped.
- B. The five A2A agents serve their public card from the SDK's serializer
  plus the skills' `x-coaz-mappings` (`_agent_card_routes`, replacing the
  SDK route; the SDK skill type has no extension field). Verified with the
  real SDK: all SDK keys preserved, mappings attached by skill id.
- C. Console: `IBAC_CARD_URL` (the orchestrator's raw card, cached 60 s);
  `_discovery_preview` mirrors `EvaluateDiscovery` (types + action, any
  evaluation × any skill, empty intent = negative) and every INTENT card
  shows "IBAC discovery preview · skills: …" or "no skill matches". 8 tests
  in `chatbot/tests/ibac_preview_test.py`.

Pending:

- D. Demo beats once the first run shows what the Intent Agent extracts
  (matching invoice prompt, a customer's staff-shaped prompt, an empty
  intent for weather).
- E3. Gateway 2.94.0 (2026-11-04, `agent-gateway:latest`): one MCP-only
  change, the gateway now checks the server's `initialize` answer for a
  supported revision and answers `-32603` with `offered`/`supported`
  otherwise; no configuration change. Verify the ERP server's revision on
  the first `initialize` through `erp-mcp-iag`.
- E2. Gateway 2.93 (2026-11-03, `agent-gateway:latest` = 2.93.0): admission
  is one AuthZEN evaluation - subject from whoami, action `CAN_TRIGGER`,
  resource the Agent node of the gateway's own actor (newest hop), user
  token in `Authorization`, delegation in `X-IK-Token`, the policy comparing
  `$ik_token.act_chain` with a workflow's path of agents. Applied: KBAC
  `person-can-reach-agent` added (34 policies), CIQ slot 8
  `get-agent-workflows` dropped (13 queries), `JARVIS_SERVICE_BASE_URL` on
  every gateway, `JARVIS_IDENTITY_PROVIDER_ISSUER` in the base, MCP gateways
  with full endpoint URLs on both sides (unencoded `gid:`), every
  `CONTX_IQ_QUERY_ID` / `ALLOWED_WORKFLOW_ID` / `AUTHZEN_SUBJECT_TYPES`
  gateway setting removed; `*_WORKFLOW_ID` remain as console why?-card
  hints only; Bruno regenerated with an admission probe. Needs the platform
  release that derives `$ik_token.act_chain` (same release line).
- E. Gateway 2.91+ (2026-11-03: run as the locally loaded image
  `AGENT_GATEWAY_IMAGE`, default `agent-gateway:latest`, `pull_policy:
  never` - Docker Hub is not ready; the loaded image is 2.92.0, which
  changes nothing in the gateway: its two features are platform-side, a
  token-introspect `id_key`/`label` pair required only for IKG schema V2
  projects, and the IKG schema version on the connection; 2.91 added the
  reuse of client-credentials tokens until `expires_in`) ships the switch
  from 2.90: `ibac.enabled`
  gates the `intent_agent` section (`IBAC_ENABLED` in `.env`, orchestrator
  gateway only), default timeout 45 s, INTENT audit records
  (`INTENT_FOUND` / `INTENT_FAILED`, `durationMs`, prompt with
  `audit.include_prompt`, set in `audit-config.yaml`), and a failed intent
  answered to the caller in place of the agent (A2A message with error +
  obligation, `intent_error` metadata; the console shows the text as the
  reply). The IBAC engine (discovery/usage against the card mappings) is
  still not called on 2.90.0; when it is, confirm the gateway reads the raw
  card (it serves a typed copy today, which drops `x-coaz-mappings`). The
  console already renders INTENT, `IBAC_DISCOVERY` and `IBAC_USAGE` records.

## Open verification points

- Auth0 access tokens carry `aud` as an array when `openid` is requested;
  confirm the platform's `jwt_matcher.audience` and the Token Service matcher
  match an element of the array (first provisioning + first login).
- `perform_upsert: true` with empty `claims_mapping` on pre-captured Person
  nodes (canbank-ts does the same for the Token Service introspect).
- The orchestrator M2M client is also the Intent Agent's introspection client
  at the bridge; fine for a demo, a dedicated client is cleaner.
- Intent Agent image: the published `indykite/intent-agent:1.0.0` (digest
  pinned in compose since 2026-11-02); `make intent-agent-image` only for an
  unreleased build.
- Gateway `authorization_header.same_user_check` stays on: Authorization is
  always the user's Auth0 token on every hop.
