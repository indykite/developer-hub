# iag-mcp-demo

Demonstrates the Indykite Agent Gateway, a smart, context-enabled gateway that secures A2A and MCP communication channels. This variant routes MCP traffic through a dedicated MCP-protecting gateway (`mcp-iag`); see [Protecting MCP traffic](#protecting-mcp-traffic-mcp-iag).

## Contents

- [Folders](#folders)
- [Running the demo](#running-the-demo)
    - [1. Prerequisites](#1-prerequisites)
    - [2. Configure the usecase's env file](#2-configure-the-usecases-env-file)
    - [3. Build the local service images](#3-build-the-local-service-images)
    - [4. Pin the Agent Gateway image tag](#4-pin-the-agent-gateway-image-tag)
    - [5. Start the stack](#5-start-the-stack)
    - [6. Try the demo prompts](#6-try-the-demo-prompts)
    - [7. Troubleshooting](#7-troubleshooting)
    - [8. Stop](#8-stop)
- Feature deep-dives
    - [How a request is authorized](#how-a-request-is-authorized)
    - [Protecting MCP traffic (mcp-iag)](#protecting-mcp-traffic-mcp-iag)
    - [Capability checks](#capability-checks)
    - [Google Drive behind the gateway (profile drive)](#proxying-a-non-indykite-mcp-server-google-drive-profile-drive)
    - [Deny → remediate → allow (grant button)](#deny--remediate--allow-grant-button)
    - [Salesforce cases (profile crm)](#salesforce-cases-profile-crm)
    - [Explain the decision (why? cards)](#explain-the-decision-why-cards)
    - [Graph-filtered ERP rows (profile erp)](#graph-filtered-erp-rows-profile-erp)
    - [Parallel multi-agent MCP (WF4)](#parallel-multi-agent-mcp-wf4)

## Folders

This repo contains the following folders, refer to the README files in each of the folders for additional information.

### `agentgateway.dev`

Contains some configuration yaml files for the open-source AgentGateway tool. See [AgentGateway](https://github.com/agentgateway/agentgateway) for details.

### `chatbot`

A simple Web GUI app and  A2A client that enables Human-user prompting and interactions. The `chatbot` communicates directly with the `orchestrator_agent` (see below).

The console header's **Architecture** link opens `/static/architecture.html`: a
self-contained diagram of every container (console, gateways, agents, MCP
servers, database), the IndyKite platform services, the IdP with its OAuth2
token-exchange service, the external parties (Salesforce, Google Drive,
Open-Meteo, the LLM) and the numbered request flow between them. It reads
`/api/config` to badge the active usecase and grey out the `drive`/`crm`/`erp`
groups when their compose profile is off.

### `orchestrator_agent`

An A2A agent that manages the interactions with the end-users, and delegates the action to further agents down the workflow chain: `query_retriever` (data/IKG), `query_weather` (weather), and `query_drive` (Google Drive, via the `analyst_agent` - enabled when `ANALYST_HOST` is set).

### `retriever_agent`

An A2A agent that manages all data or IKG requests. It acts as an MCP client to the Indykite MCP Server.

### `weather_agent`

An A2A agent that returns the current weather for a requested city. It calls the public
[Open-Meteo](https://open-meteo.com) geocoding + forecast APIs (no API key required) and
is wired into the stack as an additional downstream agent the `orchestrator_agent` can
delegate to - useful for demonstrating multi-agent routing behind the Indykite Agent
Gateway.

When the prompt mentions the usecase's headquarters (keywords from
`WEATHER_HQ_KEYWORDS` in the usecase's `usecase.env`, e.g. `HQ`, `headquarters`,
`office`) **and** `MCP_SERVER_URL` is configured, the agent takes a different path:
it calls the usecase's `get-hq-weather` knowledge query through the IndyKite MCP
server. That query reads the `hq_weather` Weather node, whose `current` and `units`
properties are populated live by the usecase's `weather` and `weather-units`
external data resolvers (open-meteo). All other cities still go through the direct
Open-Meteo path. See
[`canbank/README.md`](../../canbank/README.md#external-data-resolvers) for the
canbank resolver setup.

### `analyst_agent`

An A2A agent modeled on the `retriever_agent` but **multi-backend**: it
connects to one or more MCP servers per request (`MCP_SERVER_URLS`, e.g. the
IndyKite MCP via `mcp-iag` plus Google Drive via `drive-mcp-iag`), prefixing
each backend's tool names with its alias (`indykite_ciq_execute`,
`drive_search`, …). Users can call its gateway directly (`:8885`), and the
orchestrator delegates Google Drive prompts to it via the `query_drive` tool.
It also demonstrates the parallel multi-agent MCP flow: two agents
(retriever + analyst) and two users (millicent + carol) holding concurrent,
isolated MCP sessions through the same `mcp-iag` gateway, with per-user
authorization bound to each Bearer token. See
[Parallel multi-agent MCP (WF4)](#parallel-multi-agent-mcp-wf4).

### `crm_agent`

An A2A agent that files **Salesforce cases on behalf of a person** (optional
`crm` compose profile, insurance usecase). It receives the gateway's
delegation token, then performs a second, standards-based token exchange - an
OAuth 2.0 JWT Bearer assertion (RFC 7523) signed with the connected app's
private key - for a Salesforce access token, and creates the Case via the
REST API. The Case description records the delegated subject and the agent
actor chain; both tokens surface in the console's audit terminal as TOKEN
cards. No LLM: the orchestrator's `query_crm` tool composes the
subject/description deterministically. Its workflow (`wf-crm`) is
staff-only - a customer's request ends in a red DENY. See
[Salesforce cases (profile `crm`)](#salesforce-cases-profile-crm).

### `erp_mcp`

The graph-filtered "ERP" of the optional `erp` compose profile: a Python
MCP server (streamable HTTP) in front of a Postgres invoices table. Before
any SQL runs it asks AuthZEN **search/resource** which `Invoice` ids the
calling subject may `CAN_VIEW` - the graph's `SERVES`/`HAS_INVOICE` edges
decide the rows, the database knows nothing about authorization. Same
prompt, different rows per login. See
[Graph-filtered ERP rows (profile `erp`)](#graph-filtered-erp-rows-profile-erp).

### `bruno`

Bruno collection of sample data, ciq queries and kbac queries, plus the
[`wf4-parallel-mcp`](bruno/iag-demo/wf4-parallel-mcp) suite mirroring the
jarvis-proto "WF4 - Parallel Multi-Agent MCP" e2e tests.

### `usecases`

The demo's domain, packaged per usecase (`canbank`, `insurance`): a
`usecase.env` with the domain vocabulary, the per-agent skill files mounted
into the containers, and a `DEMO_SCRIPT.md` with the suggested prompts and
narrative. The agents, gateways, and compose topology stay usecase-agnostic;
see [`usecases/README.md`](usecases/README.md) for selecting and adding
usecases.

### `drive_mcp`

The Google Drive MCP server used by the optional `drive` compose profile
(the reference stdio server wrapped into Streamable HTTP); see
[`drive_mcp/README.md`](drive_mcp/README.md).

## Running the demo

The stack boots five Agent Gateway instances: four protecting the
orchestrator, retriever, weather, and analyst agents (A2A), plus one
(`mcp-iag`) protecting the IndyKite MCP server (MCP) - alongside five in-repo
services (`chatbot`, `orchestrator_agent`, `retriever_agent`, `weather_agent`,
`analyst_agent`) wired together via Docker Compose. The optional `drive`
compose profile adds a sixth gateway (`drive-mcp-iag`) and a Google Drive MCP
server. See [Protecting MCP traffic](#protecting-mcp-traffic-mcp-iag) for the
MCP gateway and the [Google Drive section](#proxying-a-non-indykite-mcp-server-google-drive-profile-drive)
for Drive.

The stack at a glance:

| Gateway (host port) | Protects | Downstream / external | Profile |
| --- | --- | --- | --- |
| console `:3000` | - | the chatbot UI itself | base |
| `orchestrator-iag` `:8881` | orchestrator agent | routes to the other agents | base |
| `retriever-iag` `:8882` | retriever agent | IndyKite MCP via `mcp-iag` | base |
| `weather-iag` `:8884` | weather agent | Open-Meteo + `mcp-iag` (HQ) | base |
| `analyst-iag` `:8885` | analyst agent | `mcp-iag`, `drive-mcp-iag`, `erp-mcp-iag` | base |
| `mcp-iag` `:8886` | IndyKite MCP server (hosted) | `*.mcp.indykite.com` | base |
| `drive-mcp-iag` `:8887` | drive-mcp server | Google Drive API | `drive` |
| `crm-iag` `:8888` | crm agent | Salesforce (RFC 7523) | `crm` |
| `erp-mcp-iag` `:8889` | erp-mcp server | `erp-db` (Postgres) | `erp` |

In short, the actual run sequence is:

1. Provision the IndyKite project ([Prerequisites](#1-prerequisites)).
2. `cp .example.env .env.<usecase>`, fill it, then
   `./switch-usecase.sh <usecase>`.
3. `make`.
4. Check the gateway image tag.
5. `docker compose up -d`.
6. Log in at `http://localhost:3000` and prompt.

For Google Drive, additionally follow the
[Drive section](#proxying-a-non-indykite-mcp-server-google-drive-profile-drive)
before step 5.

### 1. Prerequisites

- **Docker + Docker Compose v2**.
- **Python 3.11+** with a package/environment manager: the four in-repo
  services (`chatbot`, `orchestrator_agent`, `retriever_agent`,
  `weather_agent`) each ship a `Pipfile`, so
  [`pipenv`](https://pipenv.pypa.io) is the default. Any equivalent tool
  (`uv`, `poetry`, `venv` + `pip`) works if you prefer. You only need this
  locally if you plan to run or debug the services outside Docker; the
  `docker compose up` path installs everything inside the images.
- **An IndyKite project** per usecase, provisioned with that usecase's data,
  policies and queries (each usecase lives in its own project; see
  [`usecases/README.md`](usecases/README.md)). Both usecases provision with
  **instant-stack**: `data/canbank` for **canbank** (`DATASET=canbank`) and
  `data/insurance` for **insurance** (`DATASET=insurance`; see
  [`usecases/insurance/DEMO_SCRIPT.md`](usecases/insurance/DEMO_SCRIPT.md)).
  The instant-stack manifests cover everything: capture
  nodes/relationships, CIQ policies + knowledge queries, KBAC policies
  (admission and capability policies included), App Agent, the Token
  Introspect configurations, MCP server config, and external data
  resolvers. Re-provision an existing project with the dataset whenever the
  dataset changes. For canbank, the **Bruno collection** (`bruno/iag-demo`)
  mirrors the same data request-by-request as a manual alternative, as
  linked below.

  Required in either case:
    - the canbank graph ingested (instant-stack `data/canbank`, or Bruno:
    [`bruno/iag-demo/ingest/{canbank,customers,customer-docs}`](bruno/iag-demo/ingest)),
    - the agent-workflow graph ingested (Bruno:
    [`bruno/iag-demo/ingest/agent-workflow`](bruno/iag-demo/ingest/agent-workflow)):
    `User`s, `Workflow`s (`wf1`/`wf2`/`wf3` plus the Drive/console shapes
    `wf-drive`, `wf-drive-analyst`, `wf-drive-console`, `wf3-console`) and the
    `Agent` chains. Each `Agent` node's `external_id` is the IdP client id
    of its gateway. The `INVOKES` edges between workflows and agents carry a
    `workflow_name` property (and `discriminating_property: workflow_name` on
    edges into shared agent nodes): the admission policy filters on it at
    every hop, so ingest without these properties admits **no** request,
    - a `CAN_TRIGGER` edge from the calling `User` (or their department) to
    the workflow (see
    [`bruno/iag-demo/authzen/subject-can-trigger-workflow.yml`](bruno/iag-demo/authzen/subject-can-trigger-workflow.yml)),
    - the capability nodes (`Tool`, `McpResource`, `Skill`) and the
    `(:Agent)-[:HAS {workflow_name}]->` edges that attach them to the agents
    (see [Capability checks](#capability-checks)),
    - the KBAC policies (Bruno: [`bruno/iag-demo/authzen`](bruno/iag-demo/authzen)):
    the admission policy `user-can-reach-agent` (insurance:
    `person-can-reach-agent`), the capability policies `user-can-use-tool`,
    `user-can-use-mcp-resource`, `user-can-use-prompt` and
    `user-can-use-skill` (insurance: `person-...`), and
    `user-can-trigger-workflow` (insurance: `person-can-trigger-workflow`)
    for the console's grant self-check; see
    [How a request is authorized](#how-a-request-is-authorized),
    - a ContX IQ knowledge query + policy: pick any pair from
    [`bruno/iag-demo/ciq-context`](bruno/iag-demo/ciq-context),
    - an App Agent with a credentials token,
    - the Token Introspect configurations, all for the Curity issuer and the
    usecase's node type (`User` in canbank, `Person` in insurance): the
    MCP-bound one (audience `indykiteagent-mcp`), the console user token
    (audience `chatbot-mcp`), and one per gateway client audience
    (`indykiteagent`, `indykiteagent-2`, `indykiteagent-3`, `indykiteagent-4`,
    `indykiteagent-drive`, `indykiteagent-erp`, plus `indykiteagent-crm` in
    insurance). The platform must resolve every token a gateway sends it:
    the bearer token for whoami and the delegation token in `X-IK-Token`,
    - the project's **MCP server configuration** `enabled` and bound to that App
    Agent (`app_agent_id`) and the MCP-bound Token Introspect
    (`token_introspect_id`). This config is created together with the project
    (it can't be created from the demo); enable and configure it in the
    IndyKite console. The MCP server resolves the App Agent server-side from
    it, so MCP callers send only the user's Bearer token.
- **Provider clients** at the IdP (Curity, `idsvr.indykite.one`, which logs
  users in and performs the RFC 8693 token exchange) for `console` (chatbot
  login), `indykiteagent` (orchestrator), `indykiteagent-2` (retriever),
  `indykiteagent-3` (weather), `indykiteagent-4` (analyst), and
  `indykiteagent-mcp` (`mcp-iag`): each with its secret.
  The optional Drive MCP PoC additionally needs `indykiteagent-drive`, the
  optional CRM profile `indykiteagent-crm`, and the optional ERP profile
  `indykiteagent-erp`.
- **(Optional) Gemini API key**, otherwise an **Ollama** instance reachable
  from Docker (default `http://host.docker.internal:11434`).

### 2. Configure the usecase's env file

Each usecase keeps a complete env file (`.env.canbank`, `.env.insurance`, …;
gitignored) and `.env` is a symlink to the active one:

```bash
cp .example.env .env.canbank        # or .env.insurance - fill the placeholders
./switch-usecase.sh canbank         # relinks .env and (re)creates the stack
```

The root `.env` symlink is always required: `docker compose` loads it for
every service. The per-service `.env` files under `chatbot/`,
`orchestrator_agent/`, `retriever_agent/`, and `weather_agent/` are only used
when running those services directly on the host (outside Docker); they are
**not** needed for the `docker compose up` path.

Fill in, at a minimum:

| Variable | Where to get it |
| --- | --- |
| `INDYKITE_BASE_URL` | `https://api.eu.indykite.com` or `https://api.us.indykite.com` |
| `IDP_BASE_URL` | Base URL of the IdP where the `console` / `indykiteagent*` clients live. The chatbot login and all gateways (introspection, client credentials, token exchange) use the same instance. |
| `IDP_ISSUER` | Issuer of that IdP, exactly as written into the `iss` of its tokens (default `https://idsvr.indykite.one/oauth/v2/oauth-anonymous`). The gateways advertise it: OpenID discovery URL on the agent cards, authorization server in the MCP gateways' protected resource metadata. |
| `WORKFLOW_ID` | Read by the console only: the workflow the "why?" cards of the orchestrator, retriever and MCP gateways explain (`wf1`). The gateways take no workflow setting; the admission policy decides. |
| `APP_AGENT_CREDENTIALS_TOKEN` | App Agent credentials token the gateways use for their whoami and AuthZEN calls (`JARVIS_CONTX_IQ_APP_AGENT_CREDENTIALS_TOKEN`) |
| `MCP_SERVER_ORIGIN` | Scheme + host of the MCP server, e.g. `https://us.mcp.indykite.com` / `https://eu.mcp.indykite.com`. `mcp-iag` forwards to `<MCP_SERVER_ORIGIN><MCP_SERVER_PATH>`. |
| `MCP_SERVER_PATH` | MCP endpoint path, `/mcp/v1/<PROJECT_GID>`, with the gid written unencoded (`gid:...`, not `gid%3A...`; a gateway does not start with an encoded path). The compose file appends this to the `mcp-iag` host: it is the one endpoint `mcp-iag` serves and the agents call. |
| `MCP_SERVER_URL` | Direct URL to the MCP server (`<MCP_SERVER_ORIGIN><MCP_SERVER_PATH>`, same unencoded gid). Kept for reference / bypassing `mcp-iag`; by default the agents are routed through the gateway instead. |
| `MCP_IDP_CLIENT_ID` / `_SECRET` | IdP Provider client `mcp-iag` uses to authenticate to the MCP server (e.g. `indykiteagent-mcp`). |
| `CHATBOT_IDP_CLIENT_ID` / `_SECRET` | IdP Provider `console` client |
| `ORCHESTRATOR_IDP_CLIENT_ID` / `_SECRET` | IdP Provider `indykiteagent` client |
| `RETRIEVER_IDP_CLIENT_ID` / `_SECRET` | IdP Provider `indykiteagent-2` client |
| `WEATHER_IDP_CLIENT_ID` / `_SECRET` | IdP Provider `indykiteagent-3` client (weather agent) |
| `ANALYST_IDP_CLIENT_ID` / `_SECRET` | IdP Provider `indykiteagent-4` client (analyst agent) |
| `ANALYST_WORKFLOW_ID` | Read by the console only: the workflow the analyst gateway's "why?" cards explain. Leave **empty** (default): the analyst serves several call shapes (`wf3` direct, `wf3-console`, `wf-drive-*`, `wf-erp-*`), so no single workflow fits its cards. |
| `WEATHER_WORKFLOW_ID` | Read by the console only: the workflow the weather gateway's "why?" cards explain (`wf2`). |
| `DRIVE_WORKFLOW_ID` / `ERP_WORKFLOW_ID` | *(Drive / ERP profile)* Read by the console only. Leave **empty**: these gateways serve one workflow per call shape, and their "why?" cards take the workflow from the gateway's grant bundle (`GRANT_WORKFLOW_MAP`). |
| `CRM_WORKFLOW_ID` | *(CRM profile)* Read by the console only: the workflow the CRM gateway's "why?" cards explain (`wf-crm`). |
| `AUTHZEN_SUBJECT_TYPES` | Subject node type the console (grant self-check, `CAN_TRIGGER` edges) and the ERP server (AuthZEN search) use: `User` for canbank, `Person` for insurance. The gateways do not read it; they take the type from whoami. |
| `IAG_CAPABILITY_CHECKS` | Optional, default `true`. Capability checks of the gateways (`JARVIS_AUTHZEN_CAPABILITIES_ENABLED`); `false` = admission decision only, lists unfiltered. See [Capability checks](#capability-checks). |
| `IAG_AUTHZEN_CACHE_TTL` | Optional, default `30s`. How long a gateway keeps an admission decision and a permitted set per subject and chain, i.e. how fast a grant or revoke reaches the gateways. |
| `AGENT_GATEWAY_IMAGE` | Optional. Agent Gateway image for all gateways; the default is the published `2.0.3` release pinned by digest in [`iag-base-docker.yaml`](iag-base-docker.yaml). Never use the `latest` tag. |
| `ANALYST_MCP_SERVER_URLS` | *(Drive profile)* Multi-backend analyst: `indykite=http://mcp-iag:8886/mcp/v1/<PROJECT_GID>,drive=http://drive-mcp-iag:8887/mcp` (same unencoded gid as `MCP_SERVER_PATH`). Required for Drive prompts; see the Drive section. |
| `CIQ_QUERY_HQ_WEATHER` | Optional. Name (not gid) of the `get-hq-weather` knowledge query; the weather agent calls the MCP tool of that name for HQ prompts (default: `get-hq-weather`). Needs slot 9 + the `weather` / `weather-units` EDRs. Without it, weather prompts go to Open-Meteo. |
| `FLASK_SECRET_KEY` | Generate a fresh one: `python -c "import secrets; print(secrets.token_hex(32))"` |

LLM selection:

- `GEMINI_ENABLED=true` + `GEMINI_API_KEY=…` to use Gemini, **or**
- leave `GEMINI_ENABLED=false` and point `OLLAMA_HOST` at your local Ollama
  (`http://host.docker.internal:11434` when running Ollama on the host).

### 3. Build the local service images

The three in-repo services are built locally. There's a makefile for this:

```bash
make                 # build chatbot, orchestrator-agent, retriever-agent, weather-agent, analyst-agent, crm-agent
# or individually:
make new-chatbot
make new-orchestrator
make new-retriever
make new-weather
make new-analyst
make new-crm
make new-drive-mcp   # optional, profile "drive" - outside the default build
make new-erp-mcp     # optional, profile "erp"   - outside the default build
```

> **Image-tag collision warning**: `iag-token-exchange` builds images with
> the SAME tags (`chatbot:latest`, `retriever-agent:latest`, ...). Running
> either app's `make` silently overwrites the other app's images - after
> switching demo apps, always re-run THIS app's `make` before
> `docker compose up -d`, or containers come up with the sibling app's code
> (symptom here: console features like the why? cards silently disappear).

### 4. Pin the Agent Gateway image tag

[`iag-base-docker.yaml`](iag-base-docker.yaml) pins the published release
`2.0.3` by digest:

```yaml
services:
  iag-base:
    image: ${AGENT_GATEWAY_IMAGE:-indykite/agent-gateway:2.0.3@sha256:8368f151cfec343f679dce6f320f43ab8c14cebb5465f683213de66c857aed33}
```

All gateways inherit this image. The demo relies on what it implements: MCP
proxying (`JARVIS_PROTECTED_AGENT_PROTOCOL: mcp`, used by `mcp-iag`,
`drive-mcp-iag` and `erp-mcp-iag`) with one MCP endpoint per gateway and
OAuth discovery, admission by one AuthZEN evaluation over the delegation
chain, the capability checks, and the agent cards of the A2A gateways. To
run another image, set `AGENT_GATEWAY_IMAGE` in `.env`. Never use the
`latest` tag: on Docker Hub it points at an older build.

If you are on Apple Silicon, add a `platform` attribute:

```yaml
services:
  iag-base:
    platform: linux/amd64
```

If you receive the following message from `docker compose up` then you likely need a `platform`
attribute.

> [!CAUTION]
> The requested image's platform (linux/amd64) does not match the detected host platform
> (linux/arm64/v8) and no specific platform was requested

Check
[the tag list on Docker Hub](https://hub.docker.com/r/indykite/agent-gateway/tags)
for different releases.

### 5. Start the stack

```bash
docker compose up
```

This brings up:

| Service | Port | Role |
| --- | --- | --- |
| `chatbot` | `3000` | Web UI + A2A client (log in via IdP Provider) |
| `orchestrator-iag` | `8881` | Agent Gateway protecting the orchestrator |
| `orchestrator` | `6001` | Orchestrator agent |
| `retriever-iag` | `8882` | Agent Gateway protecting the retriever |
| `retriever` | `6002` | Retriever agent (MCP client, via `mcp-iag`) |
| `weather-iag` | `8884` | Agent Gateway protecting the weather agent |
| `weather` | `6004` | Weather agent (Open-Meteo client + MCP client, via `mcp-iag`) |
| `analyst-iag` | `8885` | Agent Gateway protecting the analyst agent (`wf3` direct, `wf3-console`/`wf-drive-console` via the orchestrator) |
| `analyst` | `6005` | Analyst agent (multi-backend MCP client: `mcp-iag` + optionally `drive-mcp-iag`; reachable directly or via the orchestrator's `query_drive`) |
| `mcp-iag` | `8886` | Agent Gateway protecting the IndyKite MCP server (MCP proxy mode) |
| `drive-mcp-iag` | `8887` | *(profile `drive`)* Agent Gateway protecting the Google Drive MCP server (MCP proxy mode; workflows `wf-drive`/`wf-drive-analyst`/`wf-drive-console`) |
| `drive-mcp` | `8000` | *(profile `drive`)* Google Drive MCP server (stdio, wrapped by supergateway into Streamable HTTP) |

Open `http://localhost:3000` in a browser and log in as one of the demo users
(e.g. `leslie`, `roy`, …). Make sure you use the same hostname as
`CHATBOT_HOST` in `.env`: don't mix `localhost` and `127.0.0.1`, the OAuth
redirect URL has to match the Provider client.

### How a request is authorized

Every gateway runs the same pipeline on every request; the settings live in
[`iag-base-docker.yaml`](iag-base-docker.yaml), which all gateways extend.

1. **Tokens.** The IdP (Curity, `idsvr.indykite.one`) logs the users in and
   performs the RFC 8693 token exchange. The gateway introspects the bearer
   token, gets its own client-credentials token, and exchanges both for a
   **delegation token**: `sub` is the user, the nested `act` claims are the
   chain of gateway clients the request has passed. The gateway forwards
   that delegation token in `Authorization` to its agent, and the agent
   passes it on as the bearer token to the next gateway - so the chain grows
   by one client per hop.
2. **Subject.** The gateway asks ContX IQ `whoami` which graph node the
   bearer token belongs to. The node type comes from the project's Token
   Introspect configuration (`User` in canbank, `Person` in insurance), the
   id from the token.
3. **Admission.** ONE AuthZEN evaluation per request: subject = the whoami
   answer, action `CAN_TRIGGER`, resource type `Agent`, resource id = the
   gateway's own IdP client id (the newest hop of the delegation chain). The
   delegation token travels in `X-IK-Token` on that call, and the platform
   exposes its chain to the policy as `$ik_token.act_chain` (client ids,
   oldest first).

The decision is taken by one KBAC policy, `user-can-reach-agent` in canbank
(`person-can-reach-agent`, subject `Person`, in insurance):

```cypher
MATCH (subject:User)-[:WORKS_IN|CAN_TRIGGER*..3]->(wf:Workflow)
MATCH (wf)-[rels:INVOKES*]->(resource:Agent)
WHERE ALL(r IN rels WHERE r.workflow_name = wf.external_id AND endNode(r):Agent)
  AND [r IN rels | endNode(r).external_id] = $ik_token.act_chain
```

A subject reaches an agent when a workflow they can trigger - directly or
through their department - has exactly the delegation chain as its path of
agents. One policy serves all eight gateways and no gateway carries a
workflow setting; the `INVOKES` edges need their `workflow_name` property
because the policy filters on it.

Decisions are cached per subject and chain for `IAG_AUTHZEN_CACHE_TTL` (30 s
by default) - that is how fast a grant or revoke reaches the gateways.

The A2A gateways each set `JARVIS_SERVICE_BASE_URL` (e.g.
`http://orchestrator-iag:8881`) and serve `GET /.well-known/agent-card.json`:
without a token the public card (no skills), with a bearer token the
extended card with the skills the caller is permitted (see
[Capability checks](#capability-checks)).

Every gateway audit record carries the `action` (`ADMISSION`, `DISCOVERY`,
`INVOCATION`, or `noop` when the request is refused before any question is
asked), the `decision` (`AUTHORIZED`, `NOT_AUTHORIZED`, `ERROR`), the
`actorsChain`, a `reason` and `cached`. An admission reason reads like
"subject User/millicent may reach Agent/indykiteagent-2 through the
delegation chain" - it names the chain, not a workflow.

### Protecting MCP traffic (`mcp-iag`)

By default the `retriever` and `weather` agents reach the IndyKite MCP server
**through** a dedicated gateway, `mcp-iag`, instead of calling it directly. This
puts MCP traffic behind the same token introspection, AuthZEN authorization and
audit logging as the A2A flows.

How it is wired:

- `mcp-iag` extends `iag-base` (so it inherits the IdP / whoami / AuthZEN /
  audit config) and sets `JARVIS_PROTECTED_AGENT_PROTOCOL: mcp`, which switches
  the gateway from the default A2A proxy into MCP (Streamable HTTP) proxy mode.
- An MCP gateway serves **one endpoint**: only the path of
  `JARVIS_SERVICE_BASE_URL`, forwarded to exactly
  `JARVIS_PROTECTED_AGENT_BASE_URL`; any other path answers `404`.

  | Gateway | Serves | Forwards to |
  | --- | --- | --- |
  | `mcp-iag` | `http://mcp-iag:8886${MCP_SERVER_PATH}` | `${MCP_SERVER_ORIGIN}${MCP_SERVER_PATH}` |
  | `drive-mcp-iag` | `http://drive-mcp-iag:8887/mcp` | `http://drive-mcp:8000/mcp` |
  | `erp-mcp-iag` | `http://erp-mcp-iag:8889/mcp` | `http://erp-mcp:8010/mcp` |

- `MCP_SERVER_PATH` is `/mcp/v1/<PROJECT_GID>` with the gid **unencoded**
  (`gid:...`, not `gid%3A...`): a gateway does not start with a
  percent-encoded path. `MCP_SERVER_URL` and `ANALYST_MCP_SERVER_URLS` use
  the same form.
- The agents call `http://mcp-iag:8886${MCP_SERVER_PATH}` (set via `MCP_SERVER_URL`
  in [`docker-compose.yaml`](docker-compose.yaml)). The gateway introspects the
  caller's token, runs the admission check (resource `Agent` /
  `indykiteagent-mcp`), then exchanges for its own delegation token (via
  `MCP_IDP_CLIENT_ID` / `_SECRET`) for the downstream MCP server.
- Each MCP gateway answers `GET /.well-known/oauth-protected-resource<path>`
  without a token (RFC 9728: `resource`, and `authorization_servers` = the
  IdP issuer, `IDP_ISSUER`), and its `401` carries
  `WWW-Authenticate: Bearer resource_metadata=...` pointing there.
- The gateway forwards the MCP protocol revisions `2025-06-18`, `2025-11-25`
  and `2026-07-28`.

> [!NOTE]
> MCP calls carry **only** a Bearer token: the delegation token of the
> calling agent's gateway. `mcp-iag` runs the same admission check as every
> other gateway, inherited from `iag-base` (`JARVIS_AUTHZEN_ACTION:
> CAN_TRIGGER`, subject from whoami), so a user's own token sent straight to
> `mcp-iag` is refused: no workflow starts at the MCP agent. The downstream
> IndyKite MCP server resolves the App Agent it uses to call IndyKite APIs
> **server-side**, from the project's MCP server configuration
> (`app_agent_id`); callers send no App Agent token.

**To bypass the gateway** (talk to the MCP server directly), set
`MCP_SERVER_URL` to `${MCP_SERVER_URL}` in the `retriever`, `weather`, and
`analyst` service definitions in [`docker-compose.yaml`](docker-compose.yaml).

### Capability checks

Admission decides whether a caller reaches an agent or MCP server at all;
the capability checks decide **what** the caller may use there - per tool,
resource, prompt and skill. The switch is `IAG_CAPABILITY_CHECKS` in `.env`
(default `true`), which sets `JARVIS_AUTHZEN_CAPABILITIES_ENABLED`; `false`
leaves every request with the admission decision only and the lists
unfiltered.

- **MCP gateways** (`mcp-iag`, `drive-mcp-iag`, `erp-mcp-iag`): for
  `tools/list`, `resources/list`, `prompts/list` (discovery) and `tools/call`,
  `resources/read`, `prompts/get` (invocation) the gateway reads the caller's
  permitted set with one AuthZEN `search/resource` per kind (action
  `CAN_USE`, resource types `Tool`, `McpResource`, `Prompt`). It filters the
  server's lists to that set and refuses a call outside it with HTTP `403`
  and the JSON-RPC error `-31403`; the server is not called. Such a request
  gets the capability decision in place of the admission decision;
  `initialize` and all other requests get the admission decision.
- **A2A gateways**: filter the skills of the extended agent card (resource
  type `Skill`); discovery only.

The graph model, in both datasets:

```text
(:Agent)-[:HAS {workflow_name}]->(:Tool|:McpResource|:Skill {external_id})
```

The `external_id` is the tool name, the resource URI or the skill id, with
one `HAS` edge per workflow in which the capability may be used. The
policies `user-can-use-tool`, `user-can-use-mcp-resource`,
`user-can-use-prompt` and `user-can-use-skill` (canbank; `person-...` in
insurance) repeat the admission condition and add the agent's edge to the
capability, e.g. `(agent:Agent)-[h:HAS]->(resource:Tool)` with
`h.workflow_name = wf.external_id`.

What the datasets model:

| Agent | Capability | Workflows |
| --- | --- | --- |
| `indykiteagent-mcp` | tool `ciq_execute`, the `authzen_*` tools, one tool per knowledge query of the usecase, resource `indykite://knowledge-queries/` | `wf1`, `wf3`, `wf3-console` |
| `indykiteagent-mcp` | tool `get-hq-weather` | `wf2` |
| `indykiteagent-drive` | tool `search` | `wf-drive`, `wf-drive-analyst`, `wf-drive-console` |
| `indykiteagent-erp` | tools `list_invoices`, `get_invoice` | `wf-erp`, `wf-erp-analyst`, `wf-erp-console` |
| the A2A agents | their skills | per agent, per workflow |

No `Prompt` is modelled, so prompt lists come back empty. The visible
effect: the weather path (`wf2`) sees only `get-hq-weather` on the IndyKite
MCP server, and a caller admitted nowhere gets empty lists and a `403` on
calls.

`ciq_execute` is the server's generic executor: it runs any knowledge query
by id, and the gateway decides on the tool name, not on its arguments. A
workflow that is meant to reach only some queries therefore gets their named
tools and **not** `ciq_execute` - the weather agent calls the `get-hq-weather`
tool. On `wf1`, `wf3` and `wf3-console` the retriever and the analyst call
`ciq_execute`, so there the queries a caller can run are limited by the
ContX IQ policy of each query, not by the tool list.

The Drive gateway runs with the checks **off** (`IAG_DRIVE_CAPABILITY_CHECKS`,
default `false`): the Drive server serves one resource per file
(`gdrive:///<file id>`), URIs the graph does not list, so with the checks on
a file read is refused and only the modelled `search` tool passes. Admission
still decides who reaches Drive. Set it to `true` to show exactly that
refusal.

Capability audit records add `capabilities` (what an invocation named),
`permittedSet` and `discovery` (`serverCalled` / `listed` / `kept`) to the
fields of an admission record.

### Proxying a non-IndyKite MCP server: Google Drive (profile `drive`)

The gateway's MCP proxy mode is downstream-agnostic: it fronts the MCP
endpoint of any Streamable HTTP MCP server (SSE streaming, `Mcp-Session-Id`
passthrough). The `drive` compose profile proves it with a Google Drive MCP
server: the reference stdio server wrapped by
[supergateway](https://github.com/supercorp-ai/supergateway), protected by
`drive-mcp-iag` on its own workflow (`millicent -CAN_TRIGGER-> wf-drive
-INVOKES-> indykiteagent-drive`).

Actual steps to get Drive working end-to-end:

1. **Google Cloud setup** (once): create an OAuth client (**Desktop app**) in a
   GCP project, **enable the Google Drive API** for that project
   (`APIs & Services → Library → Google Drive API`; a disabled API fails every
   call with `403 … has not been used in project … before or it is disabled`),
   and download the client keys as `drive_mcp/.gdrive/gcp-oauth.keys.json`.
2. **Auth bootstrap** (on the host - opens a browser, so it can't run in
   Docker; re-run it whenever you replace the OAuth keys):

   ```bash
   cd drive_mcp
   GDRIVE_OAUTH_PATH=$PWD/.gdrive/gcp-oauth.keys.json \
   GDRIVE_CREDENTIALS_PATH=$PWD/.gdrive/.gdrive-server-credentials.json \
   npx -y @modelcontextprotocol/server-gdrive auth
   ```

   The vendored server refreshes the access token automatically afterwards
   (it loads the client id/secret from the keys file), so this is a one-time
   step per key set - not an hourly chore.
3. **Build + enable the profile**:

   ```bash
   make new-drive-mcp                  # build the image once
   # in .env: COMPOSE_PROFILES=drive  (leave empty to run the base demo)
   # in .env: ANALYST_MCP_SERVER_URLS=indykite=http://mcp-iag:8886/mcp/v1/<PROJECT_GID>,drive=http://drive-mcp-iag:8887/mcp
   docker compose up -d
   # equivalent one-off alternative: docker compose --profile drive up -d
   ```

The graph data (instant-stack datasets and the Bruno ingest) always includes
the Drive workflows, the `indykiteagent-drive` agent and its `search` tool
node (see [Capability checks](#capability-checks)): they are inert while
the profile is off, so the same provisioning works for both modes.

Three ways to exercise it, in increasing order of demo value:

- **Raw MCP**: `./test-drive.sh` (or the
  [`bruno/iag-demo/drive-mcp`](bruno/iag-demo/drive-mcp) folder) as millicent -
  initialize/tools/resources/search straight through `drive-mcp-iag`.
- **Prompt to the analyst**: `./demo-analyst-drive.sh`, or a `message/send` to
  `:8885` - the analyst turns natural language into Drive MCP calls.
- **Chatbot console**: log in as millicent at `:3000` and ask a "Google Drive"
  question (see the prompts in step 6).

Content notes: Drive `search` is **full-text over file contents**; reading a
file goes resource-list → `resources/read` by `gdrive:///` URI. Only
Google-native files (Docs/Sheets/Slides) export as readable text - uploaded
binaries (`.doc`, `.pdf`, …) come back as base64 blobs, so convert demo files
to Google Docs format (right-click → *Open with → Google Docs*).

See [`drive_mcp/README.md`](drive_mcp/README.md) for the auth-model
constraint: the gateway **replaces** the caller's `Authorization` with its IdP
delegation token, so the downstream must hold its own Google credentials:
Google's hosted MCP endpoints (which expect a Google OAuth token from the
caller) cannot sit behind the gateway.

#### Multi-backend analyst: IndyKite MCP + Drive at once

The analyst agent can connect to several MCP servers in one session. Set in
`.env` (see `.example.env`):

```bash
ANALYST_MCP_SERVER_URLS=indykite=http://mcp-iag:8886/mcp/v1/<PROJECT_GID>,drive=http://drive-mcp-iag:8887/mcp
```

When set, this overrides the single `MCP_SERVER_URL` and every backend's tool
names are prefixed with its alias (`indykite_ciq_execute`, `drive_search`,
`drive_list_resources`, ...), so one prompt to the analyst
(`http://localhost:8885`) can route to graph/AuthZEN data or Google Drive as
appropriate. A backend that is down is skipped with a warning instead of
failing the request.

A workflow in the IKG holds exactly **one** agent chain (the admission policy
compares the delegation chain with the workflow's path of agents), so each
call shape is its own workflow:

| Workflow | Chain | Used by |
| --- | --- | --- |
| `wf-drive` | `indykiteagent-drive` | `test-drive.sh`, bruno (direct) |
| `wf-drive-analyst` | `indykiteagent-4 -> indykiteagent-drive` | prompt to the analyst (`:8885`) |
| `wf-drive-console` | `indykiteagent -> indykiteagent-4 -> indykiteagent-drive` | chatbot console via `query_drive` |
| `wf3-console` | `indykiteagent -> indykiteagent-4 -> indykiteagent-mcp` | console-routed analyst reaching the IndyKite MCP |

Parallel `INVOKES` edges between the same agent pair are discriminated by the
`workflow_name` property (`discriminating_property: workflow_name`). The
admission policy admits whichever workflow has the request's delegation
chain as its path, so one policy serves every shape on the analyst and
drive gateways. The workflow data comes from instant-stack, or bruno
[`ingest/agent-workflow`](bruno/iag-demo/ingest/agent-workflow).

#### Deny → remediate → allow (grant button)

Authorization is data: when a drive prompt ends in a red DENY card, the card
offers a button that depends on who is looking (`/api/auth/status` returns
the subject and the role):

- **the denied person** sees **request access** on their own card, whatever
  their role - the denial is the proof they cannot grant it themselves. It
  files an access request (`/api/access-request`) that appears in the staff
  inbox on the Support page, where staff grant it.
- **people who can grant** see **grant access** on other people's cards.
  One click writes the missing `CAN_TRIGGER` edges (Capture API upsert to
  the three drive workflows), then opens the why? graph so the new path is
  visible immediately; re-running the prompt turns the chain green. The
  button flips to **revoke access** (Capture delete of the same edges) so
  the beat is repeatable.

Who can grant is decided by a live **AuthZEN self-check**: only someone who
can `CAN_TRIGGER` every workflow in a service's bundle themselves may grant
it. `/api/auth/status` runs the check once per login and returns the
services that pass as `can_grant`; the app shows the Support inbox and the
grant buttons only for those. `/api/grant` repeats the check on every call,
so millicent (trading, holds the drive workflows) passes while roy (also
staff, no drive path) or a customer calling it directly gets a clean 403.

Configured by `GRANT_WORKFLOW_MAP` in the usecase env
(`gateway=wf:wf:...,gateway=...`, empty = buttons off). The example maps
`drive-mcp-iag` to the `wf-drive*` bundle and `erp-mcp-iag` to `wf-erp*`, so
invoice requests (the `access: "request"` invoice cards) can be granted the
same way. `analyst-iag` is deliberately not mapped: it fronts both backends,
so its DENY cards have no single bundle; grants happen on the backend
gateway's card. A gateway missing from the map still takes requests, they
just show in the inbox without a Grant button. Grant rights are re-checked
by `/api/auth/status` at most once a minute and whenever a grant, revoke or
request event arrives. The chatbot also
needs `AUTHZEN_SUBJECT_TYPES` (insurance `Person`, canbank `User`) for the
self-check (policy `user-can-trigger-workflow` /
`person-can-trigger-workflow`, resource `Workflow`) and the edges it writes.
Only the chatbot reads this map - the gateways take no workflow setting.

Timing: each gateway caches its admission decisions and permitted sets per
subject and delegation chain for
`IAG_AUTHZEN_CACHE_TTL` (30 s by default, set in `.env`; see
[`iag-base-docker.yaml`](iag-base-docker.yaml)), so a grant or revoke reaches
gateway decisions within that window. The platform itself is consistent at
once. Restarting the gateways
(`docker compose restart orchestrator-iag analyst-iag drive-mcp-iag`) clears
the cache immediately.

The chatbot console reaches Drive through the orchestrator's `query_drive`
tool (enabled when `ANALYST_HOST` is set on the orchestrator, wired by
default): console -> orchestrator -> analyst -> `drive-mcp-iag` -> Drive, with
every hop introspected, authorized against the chains above, and audited.

### Salesforce cases (profile `crm`)

The `crm` compose profile (insurance usecase) adds a delegation story that
lands in a **real third-party SaaS**: a staff login asks the console to open
a case, and a Case appears in a Salesforce dev org, created on behalf of the
delegated person:

```text
console -> orchestrator-iag -> orchestrator --query_crm--> crm-iag (wf-crm) -> crm_agent
                                                                                |  RFC 7523 JWT (signed with the connected app's key)
                                                                                +-> Salesforce /services/oauth2/token -> Case via REST
```

The CRM agent receives the gateway's delegation token, reads its `sub` +
`act` chain, then exchanges a signed JWT Bearer assertion (RFC 7523) for a
Salesforce access token and creates the Case - the description records
"Filed on behalf of `<subject>` via agent chain ...". The audit terminal shows
**two** TOKEN cards per run: the delegation token from the IdP and the Salesforce
access token (redacted by default - it is a third-party credential; set
`SF_REPORT_FULL_TOKEN=true` to display it in full).
`wf-crm` is staff-only (`support`/`sales` hold `CAN_TRIGGER`;
`james` does not), so the same prompt from the customer login ends in a red
DENY - authorization by relationship.

Setup, once (details in the insurance
[`DEMO_SCRIPT.md`](usecases/insurance/DEMO_SCRIPT.md) prerequisites):

1. A Salesforce Developer Edition org with a **classic Connected App**
   (in new orgs first enable `Setup -> External Client App Settings ->
   Allow creation of connected apps`; the External Client App UI does not
   expose the JWT Bearer flow): Enable OAuth Settings, **Use digital
   signatures** + upload the certificate, scopes `api` **and**
   `refresh_token/offline_access`, policy "Admin approved users are
   pre-authorized" + assign the integration user's profile.
2. Generate the keypair
   (`openssl req -x509 -newkey rsa:2048 -nodes -keyout crm_agent/keys/sf-jwt.key
   -out sf-jwt.crt -days 365 -subj "/CN=iag-mcp-demo"`); upload `sf-jwt.crt`
   to the connected app. Both files are gitignored; the private key stays at
   `crm_agent/keys/sf-jwt.key` (mounted read-only). Make it readable by the
   container's non-root user: `chmod 644 crm_agent/keys/sf-jwt.key`.
3. Fill `.env`: `CRM_IDP_CLIENT_ID/SECRET` (`indykiteagent-crm`),
   `IAG_CRM_HOST=crm-iag`, `SF_CONSUMER_KEY`, `SF_USERNAME`, and add `crm` to
   `COMPOSE_PROFILES`. The dataset must carry the `wf-crm` workflow and the
   Token Introspect configuration for the `indykiteagent-crm` audience
   (instant-stack `data/insurance`).

The orchestrator registers `query_crm` only when `CRM_HOST` is set (empty in
canbank - the tool, containers, and story are absent there). Unlike the other
agents, the CRM agent's port is **not** published to the host: it trusts the
gateway-verified delegation token for the on-behalf-of attribution written
into Salesforce, so only `crm-iag` may reach it (internal network). Common errors:
`invalid_grant / invalid assertion` = certificate/key mismatch or the app not
yet propagated (2-10 min); `refresh_token scope is required ...` = missing
`refresh_token` scope or the pre-authorization policy; a "permission error"
reading credentials = the key file is not readable by uid 1001 (`chmod 644`).

### Explain the decision (why? cards)

Every AUTHORIZED / NOT AUTHORIZED card in the console's audit terminal can
carry a **why?** button that renders the authorization path behind the
decision, live from the graph:

```text
why? click -> chatbot GET /api/explain?subject=millicent&workflow=wf1
           -> POST {INDYKITE_BASE_URL}/contx-iq/v1/execute   (app-agent key)
              explain-staff-workflow-access  + explain-direct-workflow-access
           -> cytoscape modal: (millicent)-[WORKS_IN]->(Support)-[CAN_TRIGGER]->(wf1)
```

The two `_Application`-subject explain queries mirror the two path shapes
by which the KBAC policies link a subject to a workflow with every hop aliased (a `*..3` pattern cannot expose its
intermediate nodes): the staff leg `Subject-[WORKS_IN]->Department-
[CAN_TRIGGER]->Workflow` and the direct leg `Subject-[CAN_TRIGGER]->Workflow`.
A **denial renders as the two nodes with no connecting path** - the graph
shows *why not*. Since the query runs live, changing the graph mid-demo
(capture a new `CAN_TRIGGER` edge) changes the answer on the next click.

Setup: the queries ship in both datasets (instant-stack `data/canbank` slots
11/12, `data/insurance` slots 10/11 - subject `User` vs `Person`); provision
them and put the two gids in `.env` (`EXPLAIN_STAFF_QUERY_ID`,
`EXPLAIN_DIRECT_QUERY_ID`). The buttons appear only when both are set. The
audit records name the delegation chain, not a workflow, so the card takes
the workflow from a gateway->workflow map (`EXPLAIN_WORKFLOW_MAP`, assembled
in compose from the `*_WORKFLOW_ID` vars, which only the console reads),
else from the last workflow of the gateway's grant bundle
(`GRANT_WORKFLOW_MAP`). Cytoscape is vendored at
`chatbot/static/assets/js/cytoscape.min.js` (no CDN).

Each gateway card also shows what the record says about the decision: a
small tag with the action next to the gateway name (`admission`,
`discovery`, `invocation`, or `refused early` for `noop`), a **chain** line
with the delegation chain, the reason, and - where the record has them -
expandable **permitted set**, **named**, **discovery** and **cached**
details (see [Capability checks](#capability-checks)).

### Graph-filtered ERP rows (profile `erp`)

An ordinary Postgres "ERP" (one invoices table) whose rows are pre-filtered
per caller by the knowledge graph through the **AuthZEN search/resource**
API - the enumeration the gateways use for capability checks, here applied
to data rows:

```text
console -> orchestrator --query_erp--> analyst --erp_list_invoices--> erp-mcp-iag -> erp-mcp
                                                                                     |  POST /access/v1/search/resource (app-agent key)
                                                                                     |  -> allowed invoice ids for the token's subject
                                                                                     +-> SELECT ... WHERE external_id = ANY(ids)  @ erp-db
```

Before any SQL runs, the ERP MCP server asks AuthZEN which `Invoice` ids the
calling subject may `CAN_VIEW`; the graph decides via
`Department-[SERVES]->customer-[HAS_INVOICE]->Invoice` (staff) and the direct
`HAS_INVOICE` edge (customer). **Same prompt, different rows**: insurance -
millicent 5 households, rebecca the 5 others, james exactly his own invoice;
canbank - millicent 9 (support+trading), leslie 6, roy 3. The database knows
nothing about the graph, and search/resource (enumeration) complements the
evaluation API (yes/no) used elsewhere in the demo.

Setup: create the `indykiteagent-erp` IdP client; provision the dataset
additions (Invoice nodes, SERVES/HAS_INVOICE edges, `staff-can-view-invoice`
KBAC, the `wf-erp-*` workflow chains and the `list_invoices` / `get_invoice`
tool nodes of `indykiteagent-erp` - in both datasets); then per usecase
`.env`: add `erp` to `COMPOSE_PROFILES`, append
`,erp=http://erp-mcp-iag:8889/mcp` to `ANALYST_MCP_SERVER_URLS`, set
`ERP_TOOL_ENABLED=true` and the IdP secret; `make new-erp-mcp` and rebuild
orchestrator/analyst for the new prompts.

Trust boundary: like the CRM agent, `erp-mcp` (and `erp-db`) are **not**
published to the host - only `erp-mcp-iag` reaches them. The server reads the
subject from the gateway's delegation token; exposing it directly would
let a self-crafted JWT read other subjects' rows.

### Parallel multi-agent MCP (WF4)

Mirrors the jarvis-proto e2e suite *"12 IAG Tests / 06 WF4 - Parallel
Multi-Agent MCP"* (ENG-8855): several users and agents hold **concurrent MCP
sessions through the same `mcp-iag` gateway**, and authorization follows each
user's Bearer token: not the payload, and not the shared agent.

The graph models this with three workflows converging on the MCP agent node
(`indykiteagent-mcp`):

- `wf1`: `millicent`/`carol`/… → orchestrator (`indykiteagent`) → retriever
  (`indykiteagent-2`) → MCP
- `wf2`: `jane` → orchestrator (`indykiteagent`) → weather
  (`indykiteagent-3`) → MCP
- `wf3`: `millicent` (only) → analyst (`indykiteagent-4`) → MCP

Two ways to see it:

- **Bruno**: [`bruno/iag-demo/wf4-parallel-mcp`](bruno/iag-demo/wf4-parallel-mcp)
  shows the other side of the same rule: **skipped hops are refused**. Its
  requests carry a user's own token straight to `retriever-iag` or `mcp-iag`,
  so the delegation chain is that one agent, and no workflow starts there.
  The retriever gateway and an MCP `initialize` answer `403`, `tools/list`
  comes back empty, and a `tools/call` answers `403` with JSON-RPC error
  `-31403` - for millicent and carol alike. Only millicent's call of the
  analyst gateway passes: `indykiteagent-4` is the start of `wf3`. Fill the
  `user_token_millicent` / `user_token_carol` secrets in the Bruno
  environment first (see the folder docs).
- **Chatbot**: log in as two different users in two browsers (or a private
  window) and prompt both at the same time. Both ride the shared retriever
  into `mcp-iag`; the audit webhook stream in the chatbot UI (see
  [`audit-config.yaml`](audit-config.yaml)) shows the separate sessions and
  each user's own allow/deny decisions.

### 6. Try the demo prompts

See the active usecase's demo script for the scripted tour:
[`usecases/canbank/DEMO_SCRIPT.md`](usecases/canbank/DEMO_SCRIPT.md) or
[`usecases/insurance/DEMO_SCRIPT.md`](usecases/insurance/DEMO_SCRIPT.md).
Quick canbank prompts once you're logged in as **Leslie**:

- *"What policy documents pertain to refunds?"*
- *"Retrieve past decisions that incorporated the 'refund_policy' document."*
- *"Tell me how many shares of NVDA the user with the id: rebecca can purchase"*
- *"What's the weather in London?"* (routed to the `weather_agent` → direct Open-Meteo)
- *"What's the weather at CanBank HQ?"* (routed to the `weather_agent` → MCP tool `get-hq-weather` → `weather` + `weather-units` resolvers; requires `CIQ_QUERY_HQ_WEATHER` and the canbank EDR setup)

With capability checks on (the default), each path sees only the tools its
workflow has in the graph: the retriever path (`wf1`) lists `ciq_execute`,
the `authzen_*` tools and one tool per knowledge query, the weather path
(`wf2`) only `get-hq-weather`. The audit terminal shows
these as `discovery` and `invocation` cards with their permitted set; see
[Capability checks](#capability-checks).

Stock quotes and AuthZEN decisions (as **millicent**, who works in both
`support` and `trading` - the `trading` department holds the
`CAN_RETRIEVE -> stock_quote` edge):

- *"What is the price of META?"* (CIQ `get-stock-quote` → Yahoo Finance
  resolver; passes because millicent's department can retrieve the quote. An
  occasional `429 Too Many Requests` is Yahoo rate-limiting, not an
  authorization failure - retry after a few minutes)
- *"Am I allowed to retrieve a stock quote? Check with authzen."* (KBAC policy
  `user-can-retrieve-quote`: `User -WORKS_IN-> Department -CAN_RETRIEVE-> Quote`.
  Watch the retriever log: it loads the `authzen` + `canbank-authz` skills,
  resolves its own id via `get-self`, then sends one exact `authzen_evaluate`)
- *"Perform an authzen test with the following payload: subject_type User,
  subject_id millicent, resource_type Workflow, resource_id wf1, action_name
  CAN_TRIGGER. Report the raw decision."* (forces an exact `authzen_evaluate`
  call; decision `true` via the `user-can-trigger-workflow` policy)
- As **leslie** or **flo** (in `support` only): *"why can't I get a stock
  quote?"* - the negative path; the decision is `false` because their
  department has no `CAN_RETRIEVE` edge.

Note: evaluations through the chatbot carry the logged-in user's token, and
the platform binds them to that token's subject - asking `authzen_evaluate`
about a *different* user (e.g. subject `roy` from millicent's session) always
returns `false` by design. Evaluate other subjects from a service context
instead (Bruno `authzen` folder, `X-IK-ClientKey` app token).

**How the agents get the AuthZEN vocabulary right.** KBAC types, actions, and
ids are case-sensitive graph terms (`User`, `CAN_RETRIEVE`, `Quote`,
`stock_quote`); a made-up value (`user`, `view`, `stock`) evaluates to a
`false` that is indistinguishable from a real denial, and the MCP server does
not (yet) expose the policy vocabulary for discovery - `list_resources` only
returns the knowledge queries. The retriever and analyst therefore pair two
agent skills (`<agent>/skills/`):

- `authzen` - generic: the five AuthZEN operations, the never-guess-the-
  vocabulary rules, subject resolution via `get-self`, and the token-subject
  binding above. Dataset-independent.
- `canbank-authz` - dataset-owned: the exact policy triples of *this* project
  (`User -CAN_TRIGGER-> Workflow`, `User -CAN_RETRIEVE-> Quote` with known
  ids). Keep its table in sync with the `kbac` section of
  instant-stack `data/canbank/manifest.json` when policies change; remove it if the
  platform ever exposes policy vocabulary through the MCP server itself.

With the `drive` profile up, log in as **millicent** and mention "Google
Drive" (or "Drive") in the prompt - that's what routes it to the orchestrator's
`query_drive` tool instead of the retriever:

- *"Search Google Drive for canbank and list the matching files."* (full-text search)
- *"List the files in my Google Drive."*
- *"Read the file 'CanBank treasury desk summary' from Google Drive and summarize it."* (Google-native docs only; name the converted copy, not the `.doc`)
- *"According to the canbank retail onboarding notes in Google Drive, what steps are required to onboard a new customer?"*

Each one shows the full chain in the audit terminal: `orchestrator-iag` →
`analyst-iag` → `drive-mcp-iag` (workflow `wf-drive-console`), with the
delegation chain growing by one client on each card's **chain** line.

### 7. Troubleshooting

- **`manifest unknown` / `manifest for indykite/agent-gateway:<tag> not found`**:
  the image named in `AGENT_GATEWAY_IMAGE` doesn't exist for your platform.
  Remove the override to run the default `2.0.3` digest from
  [`iag-base-docker.yaml`](iag-base-docker.yaml) (never `latest`) and, on
  Apple Silicon, add `platform: linux/amd64`.
- **A gateway exits at start**: a setting is missing or invalid, and the
  gateway's log names it. Typical: a percent-encoded `MCP_SERVER_PATH`
  (write the gid as `gid:...`), or a leftover
  `JARVIS_CONTX_IQ_ALLOWED_WORKFLOW_ID` in a compose override.
- **OAuth redirect mismatch**: the Provider `console` client's redirect URL
  must exactly match `http://${CHATBOT_HOST}:${CHATBOT_PORT}/auth/callback`.
- **`401 Unauthorized` on every request**: the project has no Token
  Introspect configuration for the issuer + audience of the token the
  gateway presents - the bearer token (whoami) or the delegation token of
  that gateway's client (`X-IK-Token`). Each gateway client audience needs
  its own configuration; the datasets carry the full list.
- **`403 Forbidden` with an `ADMISSION` / `NOT_AUTHORIZED` audit record**:
  the admission policy (`user-can-reach-agent` / `person-can-reach-agent`)
  is missing, the record's chain differs from the workflow's `INVOKES` path,
  or the subject has no `CAN_TRIGGER` path to that workflow. Compare the
  card's **chain** line with the graph
  ([`bruno/iag-demo/ingest/agent-workflow`](bruno/iag-demo/ingest/agent-workflow))
  and check the policies in [`bruno/iag-demo/authzen`](bruno/iag-demo/authzen).
  Remember: one workflow holds exactly one chain.
- **A card tagged `refused early`** (action `noop`): the gateway refused the
  request before asking any question; the record's reason says why.
- **Empty tool lists, or `403` with JSON-RPC error `-31403`**: the capability
  nodes, their `HAS` edges or the `*-can-use-*` policy are missing for that
  workflow (see [Capability checks](#capability-checks)); to rule the checks
  out, set `IAG_CAPABILITY_CHECKS=false` and recreate the gateways.
- **`404` from an MCP gateway**: the client calls another path than the
  gateway's one endpoint (see
  [Protecting MCP traffic](#protecting-mcp-traffic-mcp-iag)).
- **`401` / `403` on MCP calls (retriever/weather data lookups)**: besides the
  causes above, confirm that the project has an **enabled MCP server
  configuration** with a valid `app_agent_id` (the App Agent is resolved
  server-side; a missing/disabled config rejects all MCP requests). A `401`
  whose `WWW-Authenticate` header points at
  `.well-known/oauth-protected-resource` means the Bearer token is
  missing/expired/wrongly-bound: not a missing App Agent key. To isolate
  whether the gateway is the cause, temporarily bypass it (set
  `MCP_SERVER_URL` to the direct URL).
- **`.env` changes don't take effect**: `docker compose restart` does **not**
  re-read `.env` - environment is baked at container creation. Recreate
  instead: `docker compose up -d --force-recreate <service>`.
- **Drive calls fail with `invalid_request` / Google `403 API not enabled`**:
  enable the Google Drive API in the OAuth client's GCP project (step 1 of the
  Drive section); `invalid_request` right after an auth run that *was* working
  means token refresh failed - re-run the auth bootstrap.
- **Drive file reads answer "binary document"**: the file is an uploaded
  binary, not a Google-native doc - convert it (*Open with → Google Docs*).
- **Console features silently missing (why? cards, table styling, ...)**
  after switching demo apps: the sibling `iag-token-exchange` app builds
  images with the same tags and has overwritten this app's - re-run this
  app's `make` and `docker compose up -d` (see the warning in the build
  section).
- **`Bind for 0.0.0.0:88xx failed: port is already allocated`**: the
  sibling `iag-token-exchange` stack shares the full port map - only one of
  the two demos can run at a time. Stop the other first (if its `.env` is
  broken and `compose down` fails:
  `docker ps -a --filter name=iag-token-exchange -q | xargs docker stop`).
- **A mounted file exists on the host but the container sees an empty
  directory** (e.g. `crm_agent/keys/`): the host directory was deleted and
  recreated while the container ran - a bind mount pins the old inode.
  `docker compose up -d --force-recreate <service>` rebinds it.
- **Grant button problems**: no `grant access` button on a DENY card = the
  card's gateway has no `GRANT_WORKFLOW_MAP` entry, you're not logged in,
  the card is your own denial (you get `request access` instead), or the
  AuthZEN self-check found no `CAN_TRIGGER` path of your own to that
  service's workflows (`can_grant` in `/api/auth/status`, re-checked within
  a minute or on the next grant/request event). As millicent it means the
  staff path isn't provisioned. A **403 on click** means the same check
  failed on the write. A **502** means the
  chatbot can't reach `INDYKITE_BASE_URL` / the app-agent key is invalid.
- **Invoices empty or `erp-db` exits after a postgres major upgrade**: the
  `erp_db_data` volume holds a cluster from the previous major version, which
  the new image will not open (it refuses to start, or initialises an empty
  cluster next to the old files). The volume only holds the seed rows from
  `erp_mcp/init.sql`, so reset it once:
  `docker compose rm -sf erp-db && docker volume rm iag-mcp-demo_erp_db_data`,
  then `docker compose up -d erp-db erp-mcp` re-seeds it.
- **ERP answers "authorization search failed"**: check
  `APP_AGENT_CREDENTIALS_TOKEN` / `INDYKITE_BASE_URL` in the erp-mcp
  container; "no invoices are visible" with 0 rows for a staff login means
  the `SERVES`/`HAS_INVOICE` edges or the invoice KBAC policies are not
  provisioned.
- **Gateway metrics**: every gateway serves Prometheus metrics at
  `:9090/metrics` inside its network (enabled via
  `JARVIS_SERVICE_TRACING_DETECTOR: local` in `iag-base-docker.yaml`; no host
  port is published - probe from a member container).
- **Tail gateway logs** to see the introspect / exchange / whoami / AuthZEN
  decisions:

  ```bash
  docker compose logs -f orchestrator-iag retriever-iag mcp-iag analyst-iag drive-mcp-iag crm-iag erp-mcp-iag
  ```

### 8. Stop

```bash
docker compose down
```
