# ibac-demo

Demonstrates the IndyKite Agent Gateway, a smart, context-enabled gateway that
secures A2A and MCP communication channels, together with the **IndyKite
Token Service** (the delegation chain in `X-IK-Token`, growing hop by hop)
and the **IndyKite Intent Agent** (what each prompt *means*, before it is
forwarded). It is the `iag-token-exchange` demo with three changes:

- **People are Auth0 users.** Every login - staff and customers alike - is a
  `Person` whose `external_id` is the Auth0 subject of their token
  (`auth0|…`), and families are in the graph: parents, minors, guardians.
  No other identity provider is involved; a small bridge gives the gateways
  the RFC 7662 introspection endpoint Auth0 does not publish.
- **The Intent Agent sits in the flow** (compose profile `intent`): the
  orchestrator gateway asks it for the intent of every prompt and the
  console shows the answer beside the authorization decisions.
- **One usecase, CanBank**, with the music demo's people as the cast; see
  [`usecases/ibac/DEMO_SCRIPT.md`](usecases/ibac/DEMO_SCRIPT.md) and
  [`SCENARIO.md`](usecases/ibac/SCENARIO.md). Dataset: instant-stack
  `data/ibac`.

One prompt, end to end (`who am I` as Millicent):

```text
browser ──Auth0 login (code + PKCE, audience ibac-console)──▶ console
console ──U (Auth0 access token)──▶ orchestrator-iag   introspect U at auth0-bridge · Token Service exchange → D1 (sub: auth0|…, act: orchestrator) · admission: AuthZEN CAN_TRIGGER on Agent/orchestrator with chain [orchestrator] · ask the Intent Agent
                                    └─▶ orchestrator   Authorization=U + X-IK-Token=D1; LLM picks query_retriever, forwards both
U + D1 ───────────────────────────▶ retriever-iag      introspect U · introspect D1 at the Token Service · exchange D1 → D2 (act: retriever→orchestrator) · admission with chain [orchestrator, retriever]
                                    └─▶ retriever      Authorization=U + X-IK-Token=D2; LLM picks ciq_execute(get-self) → mcp-iag
U + D2 ───────────────────────────▶ mcp-iag            admission: chain [orchestrator, retriever, mcp] = wf1's path of agents ✓ · exchange D2 → D3
                                    └─▶ platform MCP   Authorization=U + X-IK-Token=D3 → CIQ get-self runs against the graph → "You are Millicent…"
```

The platform validates both tokens through the project's two Token
Introspect configurations (Auth0 issuer and Token Service issuer, both for
the `ibac-console` audience, node type `Person`), and policies read the
chain as `$ik_token`: the `get-internal-documents-chained` knowledge query
and the `person-can-disclose-document-chained` policy refuse any request
whose chain is not exactly console → orchestrator → retriever → mcp.

**Admission is one policy decision.** At every hop the gateway sends a
single AuthZEN evaluation: the subject `whoami` resolved from the user's
token, the action `CAN_TRIGGER`, and as resource the `Agent` node of the
gateway's own actor, the newest hop of the chain. The dataset's
`person-can-reach-agent` policy answers it by walking from the person
(directly or through their department) to a workflow they can trigger and
comparing that workflow's path of agents with `$ik_token.act_chain`, which
the platform derives from the delegation token in `X-IK-Token`. A chain
from another workflow, or one that does not end at this agent, is refused.
The gateways pin no workflow themselves; restrictions belong in tagged
policies fed by `JARVIS_AUTHZEN_INPUT_PARAMS` and `JARVIS_AUTHZEN_POLICY_TAGS`.

## Contents

- [Folders](#folders)
- [Running the demo](#running-the-demo)
    - [1. Prerequisites](#1-prerequisites)
    - [2. Auth0 tenant](#2-auth0-tenant)
    - [3. Provision the IndyKite project](#3-provision-the-indykite-project)
    - [4. Token Service setup](#4-token-service-setup)
    - [5. Configure `.env`](#5-configure-env)
    - [6. Build the local service images](#6-build-the-local-service-images)
    - [7. The Agent Gateway image](#7-the-agent-gateway-image)
    - [8. Start the stack](#8-start-the-stack)
    - [9. Try the demo prompts](#9-try-the-demo-prompts)
    - [10. Troubleshooting](#10-troubleshooting)
    - [11. Stop](#11-stop)
- Feature deep-dives
    - [Auth0 as the identity provider (auth0-bridge)](#auth0-as-the-identity-provider-auth0-bridge)
    - [The Intent Agent (profile intent)](#the-intent-agent-profile-intent)
    - [Protecting MCP traffic (mcp-iag)](#protecting-mcp-traffic-mcp-iag)
    - [Google Drive behind the gateway (profile drive)](#proxying-a-non-indykite-mcp-server-google-drive-profile-drive)
    - [Deny → remediate → allow (grant button)](#deny--remediate--allow-grant-button)
    - [Salesforce cases (profile crm)](#salesforce-cases-profile-crm)
    - [Explain the decision (why? cards)](#explain-the-decision-why-cards)
    - [Graph-filtered ERP rows (profile erp)](#graph-filtered-erp-rows-profile-erp)
    - [Parallel multi-agent MCP (WF4)](#parallel-multi-agent-mcp-wf4)

## Folders

Refer to the README files in each folder for details.

### `chatbot`

The console: a web UI and A2A client for the people of the demo. Login is
Auth0's Authorization Code flow with PKCE, requesting the `ibac-console`
audience so the access token is a JWT the gateways introspect. The header's
**Architecture** link opens `/static/architecture.html`, a diagram of every
container, the IndyKite platform services, Auth0 and the Token Service, the
external parties and the numbered request flow. The audit terminal renders
the gateways' decisions, the Token Service exchanges, the agents' TOKEN
cards and the Intent Agent's INTENT cards; agents appear by name
(`AGENT_DISPLAY_NAMES`), their raw subject is the tooltip.

### `auth0_bridge`

The gateways' identity-provider endpoint in front of Auth0: RFC 7662
introspection of Auth0 JWTs (verified against the tenant JWKS) and a
passthrough of the token endpoint that adds the agents' audience. See
[Auth0 as the identity provider](#auth0-as-the-identity-provider-auth0-bridge)
and [`auth0_bridge/README.md`](auth0_bridge/README.md).

### `orchestrator_agent`

An A2A agent that manages the interactions with the end-users and delegates
to further agents down the workflow chain: `query_retriever` (graph data),
`query_weather`, `query_drive` (Google Drive, via the analyst), `query_erp`
(invoices, via the analyst) and `query_crm` (Salesforce cases).

### `retriever_agent`

An A2A agent that answers graph questions and authorization questions as an
MCP client of the IndyKite MCP server (through `mcp-iag`).

### `weather_agent`

An A2A agent that returns the current weather for a city through the public
[Open-Meteo](https://open-meteo.com) APIs. When the prompt mentions the
headquarters (`WEATHER_HQ_KEYWORDS` in `usecases/ibac/usecase.env`), it calls
the `get-hq-weather` knowledge query instead: the `hq_weather` node's
`current` and `units` properties are populated live by the `weather` and
`weather-units` external data resolvers.

### `analyst_agent`

An A2A agent like the retriever but **multi-backend**: it connects to one or
more MCP servers per request (`MCP_SERVER_URLS` - the IndyKite MCP via
`mcp-iag`, Google Drive via `drive-mcp-iag`, the ERP via `erp-mcp-iag`),
prefixing each backend's tool names with its alias (`indykite_ciq_execute`,
`drive_search`, `erp_list_invoices`, …). The orchestrator delegates Drive and
invoice prompts to it; its own gateway (`:9885`) is callable directly.

### `crm_agent`

An A2A agent that files **Salesforce cases on behalf of a person** (profile
`crm`). It reads the delegation token, performs a second token exchange - an
OAuth 2.0 JWT Bearer assertion (RFC 7523) signed with the connected app's
key - for a Salesforce access token, and creates the Case; the description
records the delegated subject and the agent chain. No LLM. Its workflow
(`wf-crm`) is staff-only. See [Salesforce cases](#salesforce-cases-profile-crm).

### `erp_mcp`

The graph-filtered "ERP" (profile `erp`): a Python MCP server in front of a
Postgres invoices table that asks AuthZEN **search/resource** which `Invoice`
ids the caller may `CAN_VIEW` before any SQL runs - staff see the customers
their department serves, customers their own, parents their minor
children's. See [Graph-filtered ERP rows](#graph-filtered-erp-rows-profile-erp).

### `token-service`

Configuration template of the IndyKite Token Service (issuer of the
delegation tokens); see [Token Service setup](#4-token-service-setup).

### `usecases`

The demo's domain as configuration: `usecases/ibac/` holds the vocabulary
(`usecase.env`), the console's app layer (`app.json`), the per-agent skills
(incl. `ibac-authz`, the policy vocabulary and the cast), the demo script and
the scenario. See [`usecases/README.md`](usecases/README.md).

### `bruno`

Bruno collection `bruno/ibac-demo`, generated from the dataset so it mirrors
instant-stack `data/ibac` request by request: the Capture ingest (nodes +
two relationship batches), the two Token Introspects, the 33 KBAC policies,
the 14 CIQ policy/query pairs with an execute each, the resolvers, AuthZEN
probes (incl. `search/resource` for the staff, customer and guardian invoice
views), the chain-guard suite (actor tokens from Auth0, hops at the Token
Service), the WF4 parallel-MCP suite (Millicent allowed, Marmaduke denied)
and the Drive MCP probes. Its environment `ibac-demo` holds the Auth0 tenant
values, the agents' subjects and the ibac-demo host ports; gids are recorded
by the Create requests.

### `drive_mcp`

The Google Drive MCP server of the `drive` profile (the reference stdio
server wrapped into Streamable HTTP); see [`drive_mcp/README.md`](drive_mcp/README.md).

### `agentgateway.dev`

Configuration samples for the open-source AgentGateway tool, unrelated to the
IndyKite gateway; kept for reference.

## Running the demo

The stack at a glance (compose project `ibac-demo`; the host ports are
disjoint from the sibling demos, so they can run side by side):

| Service (host port) | Role | Profile |
| --- | --- | --- |
| console `:3300` | the web UI, Auth0 login | base |
| `auth0-bridge` (internal `:8090`) | RFC 7662 introspection + token endpoint for the gateways and the Intent Agent | base |
| `token-service` `:9102` | issues the delegation tokens for every gateway | base |
| `orchestrator-iag` `:9881` → `orchestrator` `:7001` | entry gateway (asks the Intent Agent) and the orchestrator | base |
| `retriever-iag` `:9882` → `retriever` `:7002` | graph + AuthZEN via `mcp-iag` | base |
| `weather-iag` `:9884` → `weather` `:7004` | Open-Meteo + `mcp-iag` (HQ) | base |
| `analyst-iag` `:9885` → `analyst` `:7005` | `mcp-iag`, `drive-mcp-iag`, `erp-mcp-iag` | base |
| `mcp-iag` `:9886` | the hosted IndyKite MCP server (`*.mcp.indykite.com`) | base |
| `intent-agent` (internal `:6003`) + `intent-redis` | the Intent Agent behind the orchestrator gateway | `intent` |
| `drive-mcp-iag` `:9887` → `drive-mcp` `:9000` | Google Drive API | `drive` |
| `crm-iag` `:9888` → `crm` (internal `:7006`) | Salesforce (RFC 7523) | `crm` |
| `erp-mcp-iag` `:9889` → `erp-mcp`, `erp-db` (internal) | Postgres invoices | `erp` |

In short:

1. Create the Auth0 APIs and applications ([Auth0 tenant](#2-auth0-tenant)).
2. Provision the IndyKite project with instant-stack `data/ibac`
   ([Provision](#3-provision-the-indykite-project)), after the Token Service
   key exists ([Token Service setup](#4-token-service-setup)).
3. `cp .example.env .env`, fill it ([Configure `.env`](#5-configure-env)).
4. `make`.
5. `docker compose up -d`.
6. Log in at `http://localhost:3300` and prompt.

### 1. Prerequisites

- **Docker + Docker Compose v2** (compose interpolation with nested defaults).
- **An Auth0 tenant** you administer, holding the demo's people as users.
  The dataset's twelve logins are the users of the music demo's tenant;
  their subjects are listed in
  [`usecases/ibac/skills/retriever/ibac-authz/SKILL.md`](usecases/ibac/skills/retriever/ibac-authz/SKILL.md).
  A relative without a login (synthetic subject) becomes one by creating the
  user with that id in the tenant.
- **An IndyKite project** provisioned from instant-stack `data/ibac`.
- **A Gemini API key** (the agents' LLM and the Intent Agent's extraction
  model), or an **Ollama** instance reachable from Docker for the agents.
- **Python 3.11+** only if you run or test services outside Docker.

### 2. Auth0 tenant

Three APIs (Applications → APIs; the identifier is what the dataset, the
gateways and the Token Service name):

| API identifier | Audience of | Why |
| --- | --- | --- |
| `ibac-console` | the user's access token (the console requests it) and every delegation | the gateways accept only tokens issued for it; the dataset's Token Introspects validate it |
| `ibac-agents` | the gateways' actor tokens (the bridge adds it) | the Token Service matches actor tokens on it |
| `intent_agent` | the token the orchestrator gateway presents to the Intent Agent | the agent's `EXPECTED_TOKEN_AUDIENCE` |

Applications:

| Application | Type | Settings |
| --- | --- | --- |
| console | Regular Web Application | callback `http://localhost:3300/auth/callback`, logout URL `http://localhost:3300/`, grant type authorization code; client id/secret → `CHATBOT_IDP_CLIENT_ID/SECRET` |
| orchestrator, retriever, weather, analyst, mcp, drive, crm, erp | Machine to Machine, one each | authorized for the APIs `ibac-agents` **and** `intent_agent`; client id/secret → `<AGENT>_IDP_CLIENT_ID/SECRET`; `<client_id>@clients` → `AUTH0_SUB_<AGENT>` in instant-stack's `.env` |

An Auth0 client-credentials token has `sub = <client_id>@clients`: that
value is the agent's identity in the delegation chain, in the gateways'
audit records and in the graph (the Agent nodes' `external_id`). The console
shows the agents by name instead (`AGENT_DISPLAY_NAMES`, built by compose
from the client ids).

Access tokens carry `sub`, `aud` (`ibac-console`, plus the tenant's
`userinfo` URL when `openid` is requested), `azp` and `scope`; there is no
`email` claim, which is why the Token Introspects map no claims. The console
still requests `openid profile email` for the id_token it shows the display
name from.

### 3. Provision the IndyKite project

With **instant-stack** (`../../instant-stack`), `DATASET=ibac`. Before
**Provision everything**, set in instant-stack's `.env`:

- `AUTH0_ISSUER=https://<tenant>.auth0.com/` (trailing slash, as in Auth0's
  `iss`);
- the eight `AUTH0_SUB_<AGENT>=<client_id>@clients`;
- `TOKEN_SERVICE_PUBLIC_JWK` (see [Token Service setup](#4-token-service-setup)).

The manifest covers everything: the people (26 `Person` with Auth0 subjects,
families, age bands), departments, customers, accounts, invoices,
documents, decisions, agent workflows (`wf1`…`wf3`, console and Drive
shapes, `wf-erp*`, `wf-crm`), the capabilities of every agent (`Tool`,
`McpResource` and `Skill` nodes attached with `HAS {workflow_name}` edges:
which MCP tools and which card skills a workflow may use at which agent), 40
KBAC policies (subject `Person`, incl. the gateways' admission policy
`person-can-reach-agent`, the four `person-can-use-*` capability policies
and the two vocabulary policies that let the Intent Agent map "who am I"
and the weather prompts, `person-can-view-profile` and
`person-can-read-weather`), 13 CIQ queries incl.
`get-my-family`, three external data resolvers, the app agent (with `ReadAuthZConfigs` and
`ReadDataSchema` for the Intent Agent), the MCP server configuration and the
two Token Introspects. Allow a minute after provisioning: the data schema the
Intent Agent reads is persisted at most once a minute.

From the provisioning output you need: the app agent credential
(`APP_AGENT_CREDENTIALS_TOKEN`), the project gid (`MCP_SERVER_PATH`, written
unencoded as `/mcp/v1/gid:<PROJECT_GID>`) and the two explain queries
(`EXPLAIN_STAFF_QUERY_ID`, `EXPLAIN_DIRECT_QUERY_ID`).

### 4. Token Service setup

The Token Service issues the exchanged (delegation) tokens for **every**
gateway, A2A and MCP alike: all gateways inherit the `JARVIS_TOKEN_SERVICE_*`
block from [`iag-base-docker.yaml`](iag-base-docker.yaml). Details in
[`token-service/README.md`](token-service/README.md); in short:

1. **Image**: [`indykite/token-service`](https://hub.docker.com/r/indykite/token-service/tags)
   on Docker Hub, named in `.env` as `TOKEN_SERVICE_IMAGE`, the same release
   as the gateways (see [The Agent Gateway image](#7-the-agent-gateway-image)).
   It is `linux/amd64`; arm64 hosts run it under emulation.
2. **Configure**: copy `token-service/token-service.example.yaml` to
   `token-service/token-service.yaml` (gitignored); fill a fresh RSA private
   JWK (`idp.signing_keys`) and the client secret the gateways present
   (`idp.client_auth`, mirrored as `TOKEN_SERVICE_CLIENT_SECRET` in `.env`);
   replace the tenant domain in the two `token_introspection` matchers
   (`auth0-subject`: user tokens, audience `ibac-console`; `auth0-actor`:
   the gateways' tokens, audience `ibac-agents`; both verified offline
   against the tenant JWKS). `idp.audiences` holds `ibac-console` only: every
   gateway asks for that audience, so the delegations carry the same
   audience as the user's token.
3. **Platform-side trust**: the dataset's `token-service-https-ibac-console`
   Token Introspect validates the delegations offline with the **public**
   half of the signing key (`kty`/`n`/`e`/`alg`/`kid`/`use` only) - put it in
   instant-stack's `.env` as `TOKEN_SERVICE_PUBLIC_JWK` before provisioning;
   a running service publishes it at `http://localhost:9102/.well-known/jwks.json`.
   The issuer `https://token-service.ibac-demo.local` is an identifier (the
   platform validates `https` issuers offline); the gateways reach the
   service at `http://token-service:8102`.

**On the wire.** The protected agents receive the user's own token in
`Authorization` and the delegation in `X-IK-Token`; the agents forward both
to the next gateway. The CRM agent and the ERP MCP server read the subject
and the `act` chain from `X-IK-Token`. The chain-guarded knowledge query
(`get-internal-documents-chained`) and the `person-can-disclose-document-chained`
policy require `$ik_token.act_chain`, the hops of the delegation oldest
first, to equal `[AUTH0_SUB_ORCHESTRATOR, AUTH0_SUB_RETRIEVER, AUTH0_SUB_MCP]`,
i.e. exactly the console → orchestrator → retriever → mcp route; a chain of
the same depth over other agents is refused. Prompt *"Use get-internal-documents-chained to list
the policy documents"* and the query answers; the same query over a shorter
chain is refused with `invalid_delegation_chain`.

**Auditing.** The Token Service delivers every exchange attempt to the
console's webhook, so delegation minting appears in the audit terminal
beside the gateway decisions (`service: token-service`).

### 5. Configure `.env`

```bash
cp .example.env .env
```

| Variable | Where to get it |
| --- | --- |
| `INDYKITE_BASE_URL` | `https://eu.api.indykite.com` or `https://us.api.indykite.com` |
| `AUTH0_DOMAIN` | the tenant domain, e.g. `your-tenant.eu.auth0.com` |
| `AUTH0_CONSOLE_AUDIENCE`, `AUTH0_AGENTS_AUDIENCE`, `INTENT_AGENT_AUDIENCE` | the three API identifiers (defaults `ibac-console`, `ibac-agents`, `intent_agent`, as in the dataset) |
| `CHATBOT_IDP_CLIENT_ID` / `_SECRET` | the console application |
| `ORCHESTRATOR_`, `RETRIEVER_`, `WEATHER_`, `ANALYST_`, `MCP_`, `DRIVE_MCP_`, `CRM_`, `ERP_MCP_IDP_CLIENT_ID` / `_SECRET` | the M2M applications |
| `WORKFLOW_ID`, `WEATHER_WORKFLOW_ID`, `ANALYST_WORKFLOW_ID`, `DRIVE_WORKFLOW_ID`, `CRM_WORKFLOW_ID`, `ERP_WORKFLOW_ID` | the workflow the console's why? card explains per gateway (console hints; the gateways pin nothing) |
| `APP_AGENT_CREDENTIALS_TOKEN` | the app agent credential (gateways, console, ERP, Intent Agent) |
| `MCP_SERVER_ORIGIN`, `MCP_SERVER_PATH`, `MCP_SERVER_URL` | `https://<region>.mcp.indykite.com`, `/mcp/v1/gid:<PROJECT_GID>` (unencoded), their concatenation |
| `EXPLAIN_STAFF_QUERY_ID`, `EXPLAIN_DIRECT_QUERY_ID` | the two explain queries (why? cards) |
| `TOKEN_SERVICE_CLIENT_ID` / `_SECRET` | as in `token-service/token-service.yaml` `idp.client_auth` |
| `GEMINI_API_KEY`, `GEMINI_ENABLED` | the LLM (or `OLLAMA_HOST` for the agents) |
| `COMPOSE_PROFILES` | any of `drive`, `crm`, `erp`, `intent` |
| `IBAC_ENABLED` | `true` with the `intent` profile: the orchestrator gateway asks the Intent Agent about every prompt; `false` = never asked |
| `ANALYST_MCP_SERVER_URLS`, `ERP_TOOL_ENABLED`, `GRANT_WORKFLOW_MAP` | per profile, documented in `.example.env` |
| `FLASK_SECRET_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"` |

`AUTHZEN_SUBJECT_TYPES=Person`: every login is a Person; the gateways, the
console and the ERP evaluate that type. The `.env` file is loaded by compose
for every service; the per-service `.env.example` files under the agents
are for running them outside Docker only.

### 6. Build the local service images

```bash
make                 # chatbot, orchestrator, retriever, weather, analyst, crm, auth0-bridge
make new-drive-mcp   # profile drive
make new-erp-mcp     # profile erp
make intent-agent-image INTENT_AGENT_SRC=/path/to/intent-agent   # only to test an unreleased Intent Agent
```

Every image is tagged `:ibac-demo`, so this stack and the sibling demos
(`iag-mcp-demo` builds `:latest`, `iag-token-exchange` `:token-exchange`)
coexist on one Docker host. The Intent Agent is not built here: the stack
runs the published [`indykite/intent-agent`](https://hub.docker.com/r/indykite/intent-agent)
image, pinned by digest in compose; `INTENT_AGENT_IMAGE` overrides it for a
local build of the [public repo](https://github.com/indykite/intent-agent).

### 7. The Agent Gateway image

The gateways and the Token Service run the images named in `.env` as
`AGENT_GATEWAY_IMAGE` and `TOKEN_SERVICE_IMAGE`. Both are published on
Docker Hub, and `.example.env` names the latest release there:

- [`indykite/agent-gateway`](https://hub.docker.com/r/indykite/agent-gateway/tags)
- [`indykite/token-service`](https://hub.docker.com/r/indykite/token-service/tags)

Keep the two on the same release: the gateway sends the intent to the Token
Service, which has to write it into the token. Check what a gateway runs
with:

```sh
docker compose images orchestrator-iag
```

What the demo relies on:

- **Token Service mode**: the person's token stays in `Authorization`, the
  delegation travels in `X-IK-Token` and nests the chain hop by hop; one
  shared audience; a workflow starts only at the agent the token was issued
  for; the agent gets the person's token only when it was issued for it
  (`authorization_header.forward`, default `correct_audience`, which the
  single audience `ibac-console` satisfies on every hop).
- **Admission as one AuthZEN evaluation** over the delegation chain
  (`person-can-reach-agent` compares `$ik_token.act_chain` with a workflow
  path); no workflow query in the gateway.
- **Capability checks**, on by default: an MCP gateway lists and allows only
  the tools, resources and prompts the dataset attaches to its agent for the
  workflow of the call (`HAS {workflow_name}` edges, `person-can-use-*`
  policies, action `CAN_USE`); a call outside the set is `403` with JSON-RPC
  `-31403`; an A2A gateway filters the skills of the extended agent card.
  `IAG_CAPABILITY_CHECKS=false` in `.env` switches them off.
- **Audit records that name their question**: `action` is `ADMISSION`,
  `DISCOVERY`, `INVOCATION`, `INTENT` or `noop`, with the chain, the
  permitted set and whether the answer was cached; the console shows them.
- MCP gateways serving **one endpoint with OAuth discovery** and the check of
  the server's protocol revision on `initialize`; the **delegated token
  extension** on the agent cards, the MCP capabilities and the protected
  resource metadata; the `ibac.enabled` switch with the INTENT records; the
  reuse of client-credentials tokens until shortly before `expires_in`.
- **The intent in the delegated token** (IBAC on): after the Intent Agent
  answered, the entry gateway exchanges the token a second time with the
  evaluations of the intent, and the Token Service writes them into
  `act.intent` of the hop. The gateway authorizes on the first token; the
  agent receives the second in `X-IK-Token`.
- **Limits on the intent**: an intent with more evaluations than the Token
  Service accepts (`idp.max_intent_evaluations`, 10 by default) is not
  forwarded; the gateway answers in place of the agent and asks the caller
  to split the request. A delegated token over the Token Service's size
  limit (`idp.max_token_bytes`, 8000 by default) is refused like any other
  authorization refusal, and audited.
- CORS is available (`JARVIS_CORS_ALLOWED_ORIGINS`) but not set: the console
  calls the gateways server-side, no browser origin reaches them.

On Apple Silicon add `platform: linux/amd64` to `iag-base`.

### 8. Start the stack

```bash
docker compose up -d
```

Open `http://localhost:3300`, log in as one of the people (the cast is in
the demo script), and prompt. Use the same hostname as `CHATBOT_HOST` in
`.env` (`localhost`, not `127.0.0.1`): the callback URL must match the
Auth0 application.

### 9. Try the demo prompts

The scripted tour is [`usecases/ibac/DEMO_SCRIPT.md`](usecases/ibac/DEMO_SCRIPT.md)
(seven acts: support, trading desk, weather, Drive and the grant, parallel
MCP, customers and guardians, Salesforce), and
[`SCENARIO.md`](usecases/ibac/SCENARIO.md) tells it as one story. Quick
prompts:

- as **Vivienne** (support): *"What policy documents pertain to refunds?"*,
  *"Show me the invoices"* (6 rows), *"why can't I get a stock quote?"*
  (`false` - support has no `CAN_RETRIEVE` edge);
- as **Millicent** (support + trading): *"What is the price of META?"*,
  *"Tell me how many shares of NVDA the customer cust-cashmere can
  purchase"*, *"Am I allowed to retrieve a stock quote? Check with
  authzen."*, *"Show me the invoices"* (all 9);
- as **Marmaduke** (a parent): *"Show my family"*, *"Show me the invoices"*
  (his own and his minor son's - the guardian policy);
- as **Cashmere** (a customer): *"Show me the invoices"* (hers), *"Show me
  invoice inv-cb-001"* (denied);
- any login: *"What's the weather at CanBank HQ?"* (the graph-resolved
  path) vs *"What's the weather in London?"* (direct Open-Meteo).

Evaluations through the console carry the logged-in person's token and the
platform binds them to that token's subject: asking `authzen_evaluate` about
a *different* person always returns `false` by design.

**How the agents get the AuthZEN vocabulary right.** KBAC types, actions
and ids are case-sensitive graph terms (`Person`, `CAN_RETRIEVE`, `Quote`,
`stock_quote`); a made-up value evaluates to a `false` indistinguishable
from a denial. The retriever and analyst pair two skills: `authzen`
(generic: the five operations, never guess, resolve the subject via
`get-self`, token-subject binding) and `ibac-authz` (dataset-owned: the
exact triples of this project, the parameterised policies and the cast).
Keep the latter in sync with the `kbac` section of instant-stack
`data/ibac/manifest.json`.

### Auth0 as the identity provider (auth0-bridge)

Two facts shape the identity side:

- The gateway validates the user's access token only by **RFC 7662
  introspection** at its identity provider, under the same base URL as the
  client-credentials endpoint it gets its own actor token from. Auth0
  publishes no introspection endpoint, and its client-credentials grant
  needs an `audience` the gateway does not send.
- The Token Service introspects only the tokens it signed itself; its
  `token_introspection` matchers validate the subject and actor tokens of an
  **exchange** (offline, against the tenant JWKS), which is all it needs.

So the gateways' identity provider is the `auth0-bridge` service
(`JARVIS_IDENTITY_PROVIDER_BASE_URL: http://auth0-bridge:8090`):

```text
POST /oauth/introspect   verify the JWT against https://<tenant>/.well-known/jwks.json (signature, iss, exp)
                         → {active, sub, aud, iss, exp, iat, scope, client_id}; anything else → {active: false}
POST /oauth/token        → https://<tenant>/oauth/token, adding audience=ibac-agents when the grant names none
```

Callers of the introspection endpoint authenticate with their own M2M
credentials; the bridge verifies them with a client-credentials grant at
Auth0 and caches the result, so no client list exists in its configuration.
The token endpoint is hit rarely: the gateway keeps each client-credentials
token until shortly before the `expires_in` Auth0 answers (24 hours by
default, set per API in the tenant), so the bridge forwards one grant per
gateway and audience per token lifetime.
The Intent Agent uses the same endpoint (`INTROSPECT_URL`). The audience of
an introspected token is not enforced by the bridge: the gateway checks it
against `JARVIS_PROTECTED_AGENT_AUTHENTICATION_AUDIENCES` (`ibac-console`),
the Intent Agent against `EXPECTED_TOKEN_AUDIENCE`.

The Token Service exchanges need no bridge: the user's token and the
gateway's actor token are Auth0 JWTs, matched by issuer + audience and
verified offline. The delegation's `act.sub` is the actor token's `sub`,
`<client_id>@clients`, which is why the Agent nodes carry those subjects.

### The Intent Agent (profile intent)

With `COMPOSE_PROFILES` including `intent` and `IBAC_ENABLED=true`, the
orchestrator gateway asks the Intent Agent for the intent of every A2A
message before forwarding it (`ibac.enabled` gates the `intent_agent`
section; in front of an MCP server nothing is asked):

```text
orchestrator-iag ──whoami (CIQ, user token)──▶ platform          the subject: Person auth0|…
orchestrator-iag ──client credentials, audience intent_agent──▶ auth0-bridge → Auth0
orchestrator-iag ──A2A SendMessage (prompt, subject_type/subject_id), Bearer──▶ intent-agent
intent-agent ──introspect the bearer──▶ auth0-bridge
intent-agent ──data schema + ACTIVE policies for Person──▶ platform   ──extraction──▶ Gemini
intent-agent ──audit record──▶ console (INTENT card: prompt, evaluations)
```

The answer is a list of AuthZEN-shaped evaluations - subject, action,
resource, context - constrained to this project's data schema and the
caller's active KBAC policies (the 40 `Person` policies are the agent's whole
vocabulary). What the gateway does with it:

- an intent: the message is forwarded to the agent;
- a failed task (the prompt gave no intent, or the agent could not answer):
  the message is **not** forwarded; the gateway answers the caller itself,
  in the conversation, with the agent's error and obligation, and the error
  object under `intent_error` in the message metadata - the console shows
  that text as the reply;
- the agent unreachable or unreadable: 502; a timeout
  (`INTENT_AGENT_TIMEOUT`, 45 s): 504.

**What the switch buys, and what it costs.** The entry gateway uses the
intent for three things: a prompt the Intent Agent cannot map onto a policy
never reaches the agent; the intent is compared with the skills of the
orchestrator the caller may use, and one that fits none is refused; and the
evaluations that fit travel to the agent in `act.intent` of the delegated
token. The admission and capability checks run exactly the same with the
switch off. So `IBAC_ENABLED=true` is a vocabulary gate: it refuses
greetings, web search and Drive prompts, and it refuses any prompt the model
fails to map, at the price of one Gemini round trip per prompt. The model
maps from the policies' actions and the node types' property **names**, so a
type without telling properties is invisible to it; that is why the dataset
describes `Quote` (`name`, `description`, `currency`, `symbol`) and `Weather`
beyond the values the resolvers fill in. Run the acts of the demo script with
the switch **off** and switch it on for the intent beat only.

Every call is an **INTENT** audit record of the gateway (`INTENT_FOUND` /
`INTENT_FAILED`, with its duration and, because `audit-config.yaml` sets
`include_prompt`, the prompt), shown as a card beside the gateway's
AUTHORIZED card; the Intent Agent's own record arrives too, with the
evaluations.

**IBAC at the gateways.** Intent-based access control is the gateway comparing
that intent with what the invoked agent *declares* it can do. The agents
already publish the declaration: each skill on their public agent card
carries `x-coaz-mappings`, coarse-authorization mappings of subject type,
resource type and allowed actions (literal ids where fixed), read from
`usecases/ibac/skills/<agent>/coaz-mappings.json` and merged into the
card JSON the agents serve at `/.well-known/agent-card.json`. The gateway's
discovery rule accepts a skill when any mapping matches any evaluation of
the intent on those three fields; usage then resolves ids and runs AuthZEN.
A gateway with IBAC on runs this rule in four places, always against the
intent in the delegated token of the request (`IBAC_DISCOVERY`,
`IBAC_USAGE` and `RESOLVED_EVALUATIONS` records):

- **An A2A message**: the intent of the prompt is compared with the skills
  of the agent the caller may use. An evaluation that fits no skill is left
  out of the token; an intent that fits none is refused with `403`.
- **The extended agent card**: of the skills the caller may use, the ones
  that fit the intent stay.
- **The lists of an MCP server**: a tool, resource or prompt stays when it
  fits the intent, so a list can come back empty.
- **A call at an MCP server**: a call the intent does not match is refused
  with `403`, and for a mapping that names ids (`$request.arguments.<name>`,
  `$token.sub`) the gateway asks the platform one AuthZEN `evaluations`
  question before it lets the call through.

A skill or tool without a mapping is not checked (`ibac.capabilities.unmapped`,
`allow` by default; `deny` turns it into "fits nothing").

In this demo `IBAC_ENABLED=true` switches IBAC on at two gateways. The
entry gateway asks the Intent Agent, compares the intent with the
orchestrator's skills and writes it into the token. Four of the
orchestrator's skills publish mappings; `web-search` has none and every
workflow permits it, so with `IBAC_UNMAPPED=allow` (the default) no intent
is refused or trimmed there, and the `intent check` card shows which skills
each evaluation fits. `IBAC_UNMAPPED=deny` makes only the mapped skills
count: a prompt whose intent fits none of the caller's skills stops at the
entry gateway. `erp-mcp-iag` checks the ERP tools: both publish mappings
(`erp_mcp/server.py`), so under an intent about invoices the analyst sees
and may call them, with the platform asked about the invoice a
`get_invoice` call names; under an intent about something else the tools
list is empty and a call is refused before the server sees it. The ERP
server then searches with the action of the intent, so the rows are the
ones the prompt asked for: an intent about unpaid invoices
(`VIEW_UNPAID_INVOICE`) returns the open ones only, where `CAN_VIEW` returns
every invoice the person may see. The other gateways have IBAC off. Beside
the gateway's own cards, the console previews the discovery decision on every INTENT card from the
orchestrator's raw card (`IBAC_CARD_URL`): "matches query-erp" for an invoice
prompt, "no skill matches" for an intent outside the agent's declared skills. Note what
`IBAC_ENABLED=true` means: a prompt the Intent Agent finds no intent for
never reaches the agent, because the policies have no vocabulary for it.
The Intent Agent reads only the project's KBAC policies, so the dataset
gives the two prompts that had none a policy each: `person-can-view-profile`
(`CAN_VIEW_PROFILE` on `Person`, "who am I") and `person-can-read-weather`
(`CAN_READ` on `Weather`). Greetings, web search and Drive prompts still have
no vocabulary: keep IBAC off for those beats, or accept the gateway's answer
as the beat.

### Protecting MCP traffic (`mcp-iag`)

The `retriever`, `weather` and `analyst` agents reach the IndyKite MCP server
**through** a dedicated gateway, `mcp-iag`, so MCP traffic is subject to the
same token introspection, AuthZEN authorization and audit logging as the A2A
flows. `mcp-iag` extends `iag-base` and sets
`JARVIS_PROTECTED_AGENT_PROTOCOL: mcp`. A gateway serves **one** MCP
endpoint: `JARVIS_SERVICE_BASE_URL` is its public URL with the path
(`http://mcp-iag:9886${MCP_SERVER_PATH}`, the gid unencoded), and
`JARVIS_PROTECTED_AGENT_BASE_URL` the full endpoint of the server it forwards
to (`${MCP_SERVER_ORIGIN}${MCP_SERVER_PATH}`); any other path answers 404.
The agents call the public URL. MCP calls carry the user's Bearer token plus
the `X-IK-Token` delegation; the IndyKite MCP server resolves the app agent
server-side from the project's MCP server configuration.

**Protocol revisions.** The gateway forwards the MCP revisions `2025-06-18`,
`2025-11-25` and `2026-07-28` and checks the server's answer to
`initialize` as well: a server that answers with another revision gets the
client an `Internal error` (`-32603`) under the id of the `initialize`,
whose `data` names the revision the server `offered` and the `supported`
ones, instead of a handshake that succeeds and later calls that fail. The
IndyKite MCP server and the vendored Drive server answer `2025-11-25`; the
ERP server follows its Python MCP SDK, which the first `initialize` through
`erp-mcp-iag` confirms.

**OAuth discovery.** Each MCP gateway publishes RFC 9728 protected resource
metadata at `/.well-known/oauth-protected-resource<path>` without a token:
`resource` is the public endpoint URL and `authorization_servers` names the
Auth0 issuer (`JARVIS_IDENTITY_PROVIDER_ISSUER`); every 401 of the endpoint
links it in `WWW-Authenticate`. A standard MCP client can therefore discover
where to get a token. To bypass the gateway, set `MCP_SERVER_URL` back to the
direct URL in the agents' service definitions.

**Capability checks.** A request that lists or names a capability gets a
capability decision in place of the admission decision: the gateway asks
AuthZEN `search/resource` for the `Tool` (and `McpResource`, `Prompt`) nodes
the person may `CAN_USE`, with the delegation in `X-IK-Token`, and the
`person-can-use-*` policies answer the capabilities the agent at the end of
the chain `HAS` for the workflow the chain belongs to. `tools/list` comes
back filtered to that set (the same IndyKite MCP server lists the retriever
its queries and the AuthZEN tools on `wf1`, and the weather agent only
`ciq_execute` and `get-hq-weather` on `wf2`); a `tools/call` outside it is
refused with `403` and JSON-RPC `-31403` before the server is called. The
audit terminal shows each as a `discovery` or `invocation` card with the
permitted set. The ERP gateway permits `list_invoices` and `get_invoice`.
The Drive gateway runs with the checks **off** (`IAG_DRIVE_CAPABILITY_CHECKS`):
the Drive server serves every file as an MCP resource (`gdrive:///<fileId>`),
ids that cannot be modelled in advance, so a file read would be refused;
admission still decides who reaches Drive, and the dataset's `search` edges
apply the moment the switch is turned on.

### Proxying a non-IndyKite MCP server: Google Drive (profile `drive`)

The gateway's MCP proxy mode is downstream-agnostic. The `drive` profile
proves it with a Google Drive MCP server (the reference stdio server wrapped
by [supergateway](https://github.com/supercorp-ai/supergateway)) behind
`drive-mcp-iag`, on its own workflows.

1. **Google Cloud setup** (once): an OAuth client (**Desktop app**) in a GCP
   project with the **Google Drive API enabled**; download the client keys as
   `drive_mcp/.gdrive/gcp-oauth.keys.json`.
2. **Auth bootstrap** (on the host, opens a browser):

   ```bash
   cd drive_mcp
   GDRIVE_OAUTH_PATH=$PWD/.gdrive/gcp-oauth.keys.json \
   GDRIVE_CREDENTIALS_PATH=$PWD/.gdrive/.gdrive-server-credentials.json \
   npx -y @modelcontextprotocol/server-gdrive auth
   ```

3. **Build + enable**: `make new-drive-mcp`; in `.env` add `drive` to
   `COMPOSE_PROFILES` and set
   `ANALYST_MCP_SERVER_URLS=indykite=http://mcp-iag:9886/mcp/v1/<PROJECT_GID>,drive=http://drive-mcp-iag:9887/mcp`;
   `docker compose up -d`.

Drive `search` is full-text over file contents; only Google-native files
export as readable text (convert uploads with *Open with → Google Docs*).
The gateway replaces the caller's `Authorization` downstream, so the Drive
server holds its own Google credentials; see
[`drive_mcp/README.md`](drive_mcp/README.md).

One workflow holds exactly one agent chain, so each call shape is its own
workflow (parallel `INVOKES` edges between the same agents are discriminated
by `workflow_name`):

| Workflow | Chain | Used by |
| --- | --- | --- |
| `wf-drive` | drive gateway | `test-drive.sh`, bruno (direct) |
| `wf-drive-analyst` | analyst → drive gateway | a prompt to the analyst (`:9885`) |
| `wf-drive-console` | orchestrator → analyst → drive gateway | the console via `query_drive` |
| `wf3-console` | orchestrator → analyst → mcp gateway | the console-routed analyst reaching the IndyKite MCP |

The gateways pin no workflow: the admission policy compares the actual chain
with the path of every workflow the person can trigger, so all shapes
authorize on their own merits.

#### Deny → remediate → allow (grant button)

Authorization is data: when a Drive or invoice prompt ends in a red DENY
card, the card offers a button that depends on who is looking:

- **the denied person** sees **request access** on their own card; it files
  an access request that appears in the staff inbox on the Support page;
- **people who can grant** see **grant access** on other people's cards. One
  click writes the missing `CAN_TRIGGER` edges (Capture upsert to the
  service's workflow bundle), opens the why? graph with the new path, and
  flips to **revoke access** so the beat is repeatable.

Who can grant is a live **AuthZEN self-check**: only someone who can
`CAN_TRIGGER` every workflow of the bundle themselves may grant it
(`/api/auth/status` once per login, `/api/grant` on every call - Millicent
passes, Cookie or a customer gets 403). `GRANT_WORKFLOW_MAP` in `.env` maps
`drive-mcp-iag` to `wf-drive*` and `erp-mcp-iag` to `wf-erp*`. Each gateway
caches a subject's workflow set for `IAG_AUTHZEN_CACHE_TTL` (30 s), which
paces the grant and revoke beats.

### Salesforce cases (profile `crm`)

A staff login asks the console to open a case, and a Case appears in a
Salesforce dev org, created on behalf of the delegated person:

```text
console -> orchestrator-iag -> orchestrator --query_crm--> crm-iag (wf-crm) -> crm_agent
                                                                                |  RFC 7523 JWT (signed with the connected app's key)
                                                                                +-> Salesforce /services/oauth2/token -> Case via REST
```

The CRM agent reads `sub` + `act` from the delegation, exchanges a signed JWT
Bearer assertion for a Salesforce access token and creates the Case; the
description records "Filed on behalf of `<subject>` via agent chain …". The
audit terminal shows **two** TOKEN cards per run (the Salesforce one redacted
unless `SF_REPORT_FULL_TOKEN=true`). `wf-crm` is staff-only (`support` and
`trading` hold `CAN_TRIGGER`), so the same prompt from a customer ends in a
red DENY.

Setup, once: a Salesforce Developer Edition org with a **classic Connected
App** (OAuth settings, **Use digital signatures** + the certificate, scopes
`api` and `refresh_token/offline_access`, "Admin approved users are
pre-authorized" + the integration user's profile); the keypair
(`openssl req -x509 -newkey rsa:2048 -nodes -keyout crm_agent/keys/sf-jwt.key
-out sf-jwt.crt -days 365 -subj "/CN=ibac-demo"`, `chmod 644` the key, upload
the certificate); in `.env` the crm M2M client, `IAG_CRM_HOST=crm-iag`,
`SF_CONSUMER_KEY`, `SF_USERNAME`, and `crm` in `COMPOSE_PROFILES`. The CRM
agent's port is not published: only `crm-iag` reaches it.

### Explain the decision (why? cards)

Every AUTHORIZED / NOT AUTHORIZED card can carry a **why?** button that
renders the authorization path behind the decision, live from the graph:

```text
why? click -> console GET /api/explain?subject=auth0|…&workflow=wf1
           -> POST {INDYKITE_BASE_URL}/contx-iq/v1/execute   (app-agent key)
              explain-staff-workflow-access  + explain-direct-workflow-access
           -> cytoscape modal: (Vivienne)-[WORKS_IN]->(Support)-[CAN_TRIGGER]->(wf1)
```

The two `_Application`-subject explain queries mirror the policy's two path
shapes: the staff leg `Person-[WORKS_IN]->Department-[CAN_TRIGGER]->Workflow`
and the direct leg `Person-[CAN_TRIGGER]->Workflow`. A denial renders as two
nodes with no path. Set `EXPLAIN_STAFF_QUERY_ID` and `EXPLAIN_DIRECT_QUERY_ID`
(dataset slots 11 and 12); the UI parses the workflow from the AUTHORIZED
reason and falls back to `EXPLAIN_WORKFLOW_MAP` for DENY cards.

### Graph-filtered ERP rows (profile `erp`)

An ordinary Postgres "ERP" (one invoices table) whose rows are pre-filtered
per caller by the knowledge graph through **AuthZEN search/resource**:

```text
console -> orchestrator --query_erp--> analyst --erp_list_invoices--> erp-mcp-iag -> erp-mcp
                                                                                     |  POST /access/v1/search/resource (app-agent key)
                                                                                     |  -> allowed invoice ids for the token's subject
                                                                                     +-> SELECT ... WHERE external_id = ANY(ids)  @ erp-db
```

Three policies decide: `staff-can-view-invoice`
(`Person-[WORKS_IN]->Department-[SERVES]->Customer-[HAS_INVOICE]->Invoice`),
`customer-own-invoice` (`Person-[IS_CUSTOMER]->Customer-[HAS_INVOICE]->Invoice`)
and `guardian-can-view-dependent-invoice` (a parent of a child who
`IS_AGE_GROUP` `minor`). **Same prompt, different rows**: Millicent 9,
Vivienne 6, Cookie 3, Cashmere 1, Marmaduke 2 (his own and his son's). The
seed in `erp_mcp/init.sql` mirrors the dataset's Invoice nodes. Setup: `erp`
in `COMPOSE_PROFILES`, `,erp=http://erp-mcp-iag:9889/mcp` appended to
`ANALYST_MCP_SERVER_URLS`, `ERP_TOOL_ENABLED=true`, the erp M2M client,
`make new-erp-mcp`. `erp-mcp` and `erp-db` are not published: only
`erp-mcp-iag` reaches them.

### Parallel multi-agent MCP (WF4)

Several people and agents hold **concurrent MCP sessions through the same
`mcp-iag` gateway**, and authorization follows each person's token. Three
workflows converge on the MCP gateway node: `wf1` (orchestrator → retriever
→ mcp), `wf2` (weather → mcp), `wf3` (analyst → mcp, Millicent only). Log in
as Millicent and Marmaduke in two browsers and run the same `authzen_evaluate`
on `wf3`: `true` for Millicent, `false` for Marmaduke, through the same
retriever and the same gateway. The Bruno suite
[`bruno/ibac-demo/wf4-parallel-mcp`](bruno/ibac-demo/wf4-parallel-mcp) drives
the same flow deterministically with the two people's Auth0 tokens in its
environment.

### 10. Troubleshooting

- **`pull access denied` for `erp-mcp`, `drive-mcp` or `crm-agent` on
  `up`**: those images are local builds behind compose profiles; run
  `make new-erp-mcp`, `make new-drive-mcp` or `make new-crm` before enabling
  the profile.
- **Every prompt is a `502` with `IBAC_ENABLED=true`**: the Token Service
  does not write the intent into the token, because its image is not the
  gateways' release. Set `TOKEN_SERVICE_IMAGE` in `.env` to the same release
  as `AGENT_GATEWAY_IMAGE` and recreate `token-service`.
- **Only a few of the modelled tools are permitted, or a different set than
  the workflow's**: the `HAS` edges were captured without
  `discriminating_property: workflow_name`, so the platform kept one edge
  per agent and tool and the last workflow written won. The dataset carries
  the property on every `HAS` edge; re-capture the relationships
  (instant-stack: clear `CAPTURED_RELATIONSHIPS`, or the Bruno ingest
  requests) and the per-workflow sets appear.
- **Empty tool lists, or `403` with JSON-RPC `-31403` on a tool call, while
  the admission cards are green**: the capability checks found no permitted
  set - the project was provisioned before the `Tool` / `Skill` nodes, the
  `HAS` edges and the `person-can-use-*` policies existed, or the tool name
  the server lists differs from the node's `external_id`. Re-provision the
  dataset, or set `IAG_CAPABILITY_CHECKS=false` to pass the lists through.
- **OAuth redirect mismatch / `invalid_request` at login**: the Auth0
  console application must list `http://${CHATBOT_HOST}:${CHATBOT_PORT}/auth/callback`
  as callback URL and `http://${CHATBOT_HOST}:${CHATBOT_PORT}/` as logout URL.
- **Opaque access token / `active: false` for a freshly logged-in user**:
  the console did not request the audience (`ID_SERVER_AUDIENCE`, compose
  sets it from `AUTH0_CONSOLE_AUDIENCE`) or the API does not exist in the
  tenant - Auth0 then issues an opaque token the bridge cannot verify.
- **`401` from a gateway with `the access token is not issued for this
  agent`**: the token's `aud` does not contain the console audience; check
  the API identifier against `AUTH0_CONSOLE_AUDIENCE`.
- **Bridge answers `401 invalid_client`**: the gateway's M2M client id or
  secret is wrong, or the application is not authorized for the
  `ibac-agents` API (the bridge verifies callers with a client-credentials
  grant for that audience). `docker compose logs auth0-bridge` names the
  caller.
- **Token Service `EXCHANGE_REFUSED` / `invalid_grant` on the first hop**: a
  matcher did not accept the token - the tenant domain in
  `token-service.yaml` differs from `AUTH0_DOMAIN` (the issuer carries a
  trailing slash), or the actor token has no `ibac-agents` audience (the
  bridge's `AUTH0_DEFAULT_AUDIENCE`).
- **`401 Invalid token in X-IK-Token header`** from AuthZEN, ContX IQ or the
  MCP server: the project's Token Introspect for the Token Service issuer is
  missing or its public JWK does not match the signing key - re-provision
  with the current `TOKEN_SERVICE_PUBLIC_JWK` (compare with
  `http://localhost:9102/.well-known/jwks.json`).
- **`401` on MCP calls** for a user who logs in fine: the Auth0 Token
  Introspect (`auth0-person-introspect`) is missing or its issuer differs
  (`AUTH0_ISSUER` in instant-stack's `.env` must be the tenant issuer with
  the trailing slash), or the MCP server configuration is disabled.
- **`403` with `subject Person/… may not reach Agent/<subject> through the
  delegation chain`**: the admission policy found no workflow the person can
  trigger whose path equals the chain. Usual causes: the Agent nodes'
  subjects do not match the M2M clients (`AUTH0_SUB_<AGENT>` in
  instant-stack's `.env` at provisioning time vs the `*_IDP_CLIENT_ID` values
  in `.env`), the person has no `CAN_TRIGGER` path to that workflow, or the
  `person-can-reach-agent` policy is not provisioned. An `ERROR` card with
  "unable to evaluate the admission" is the platform call failing, not a
  denial. A gateway that refuses to start naming `service.base_url` or
  `identity_provider.issuer` is missing those settings.
- **The Intent Agent answers `policy_unavailable_error`**: no ACTIVE policy
  for subject type `Person` is visible to the app agent (provisioning
  incomplete, or the credential lacks `ReadAuthZConfigs`), or the data
  schema is not persisted yet (wait a minute after provisioning).
- **Every prompt answers 502 with `IBAC_ENABLED=true`**: the gateway cannot
  reach the Intent Agent (`INTENT_AGENT_BASE_URL`, the agent's health at
  `http://intent-agent:8001/health` from a member container) or the agent's
  introspection at the bridge fails (its `INTROSPECT_CLIENT_*`). A **504** is
  the intent timeout (`INTENT_AGENT_TIMEOUT`). The gateway refuses to start
  with IBAC on and no `intent_agent.base_url`.
- **The reply is "Unable to extract intent from prompt." instead of an
  answer**: that is the gateway answering in place of the agent because the
  Intent Agent found no intent for the prompt (INTENT_FAILED card). Prompts
  outside the policy vocabulary - greetings, web search, Drive - have no
  intent by construction; see the IBAC paragraph above. A prompt that should
  map (invoices, quotes, documents, family, "who am I", the HQ weather) and
  is still refused is the model being conservative: rephrase it closer to
  the action, e.g. "Retrieve the stock quote for META".
- **`.env` changes don't take effect**: `docker compose restart` does not
  re-read `.env`; recreate: `docker compose up -d --force-recreate <service>`.
- **`Bind for 0.0.0.0:98xx failed: port is already allocated`**: another
  process holds one of this app's ports; the sibling demos use a different
  map.
- **Drive calls fail with `invalid_request` / Google `403 API not enabled`**:
  enable the Google Drive API in the OAuth client's GCP project; after a
  working auth run, `invalid_request` means the refresh failed - re-run the
  auth bootstrap. "binary document" on a read = convert the file to a Google
  Doc.
- **Grant button problems**: no `grant access` = the card's gateway has no
  `GRANT_WORKFLOW_MAP` entry, you are not logged in, it is your own denial
  (you get `request access`), or the AuthZEN self-check found no
  `CAN_TRIGGER` path of your own. 403 on click = the same check on the
  write; 502 = the console cannot reach `INDYKITE_BASE_URL` or the app agent
  key is invalid.
- **Invoices empty or `erp-db` exits after a postgres major upgrade**: reset
  the seed volume: `docker compose rm -sf erp-db && docker volume rm
  ibac-demo_erp_db_data`, then `docker compose up -d erp-db erp-mcp`.
- **ERP answers "authorization search failed"**: check
  `APP_AGENT_CREDENTIALS_TOKEN` / `INDYKITE_BASE_URL` in the erp-mcp
  container; 0 rows for a staff login means the `SERVES`/`HAS_INVOICE` edges
  or the invoice policies are not provisioned.
- **A mounted file exists on the host but the container sees an empty
  directory** (e.g. `crm_agent/keys/`): the host directory was recreated
  while the container ran; `docker compose up -d --force-recreate <service>`.
- **Gateway metrics**: every gateway serves Prometheus metrics at
  `:9090/metrics` inside its network.
- **Tail the control plane**:

  ```bash
  docker compose logs -f auth0-bridge token-service orchestrator-iag retriever-iag mcp-iag analyst-iag intent-agent
  ```

### 11. Stop

```bash
docker compose down
```
