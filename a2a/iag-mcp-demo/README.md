# iag-mcp-demo

Demonstrates the Indykite Agent Gateway, a smart, context-enabled gateway that secures A2A and MCP communication channels. This variant routes MCP traffic through a dedicated MCP-protecting gateway (`mcp-iag`); see [Protecting MCP traffic](#protecting-mcp-traffic-mcp-iag).

## Folders

This repo contains the following folders, refer to the README files in each of the folders for additional information.

## `agentgateway.dev`

Contains some configuration yaml files for the open-source AgentGateway tool. See [AgentGateway](https://github.com/agentgateway/agentgateway) for details.

## `chatbot`

A simple Web GUI app and  A2A client that enables Human-user prompting and interactions. The `chatbot` communicates directly with the `orchestrator_agent` (see below).

## `orchestrator_agent`

An A2A agent that manages the interactions with the end-users, and delegates the action to further agents down the workflow chain: `query_retriever` (data/IKG), `query_weather` (weather), and `query_drive` (Google Drive, via the `analyst_agent` - enabled when `ANALYST_HOST` is set).

## `retriever_agent`

An A2A agent that manages all data or IKG requests. It acts as an MCP client to the Indykite MCP Server.

## `weather_agent`

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

## `analyst_agent`

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

## `crm_agent`

An A2A agent that files **Salesforce cases on behalf of a person** (optional
`crm` compose profile, insurance usecase). It receives the gateway-minted
delegation token, then performs a second, standards-based token exchange - an
OAuth 2.0 JWT Bearer assertion (RFC 7523) signed with the connected app's
private key - for a Salesforce access token, and creates the Case via the
REST API. The Case description records the delegated subject and the agent
actor chain; both tokens surface in the console's audit terminal as TOKEN
cards. No LLM: the orchestrator's `query_crm` tool composes the
subject/description deterministically. Its workflow (`wf-crm`) is
staff-only - a customer's request ends in a red DENY. See
[Salesforce cases (profile `crm`)](#salesforce-cases-profile-crm).

## `bruno`

Bruno collection of sample data, ciq queries and kbac queries, plus the
[`wf4-parallel-mcp`](bruno/iag-demo/wf4-parallel-mcp) suite mirroring the
jarvis-proto "WF4 - Parallel Multi-Agent MCP" e2e tests.

## `usecases`

The demo's domain, packaged per usecase (`canbank`, `insurance`): a
`usecase.env` with the domain vocabulary, the per-agent skill files mounted
into the containers, and a `DEMO_SCRIPT.md` with the suggested prompts and
narrative. The agents, gateways, and compose topology stay usecase-agnostic;
see [`usecases/README.md`](usecases/README.md) for selecting and adding
usecases.

## `drive_mcp`

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

In short, the actual run sequence is:
provision the IndyKite project (step 1) → `cp .example.env .env.<usecase>`,
fill it, and `./switch-usecase.sh <usecase>` (step 2) → `make` (step 3) →
check the gateway image tag (step 4) → `docker compose up -d` (step 5) →
log in at `http://localhost:3000` and prompt (step 6). For Google Drive,
additionally follow the
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
  nodes/relationships, CIQ policies + knowledge queries incl.
  `get-agent-workflows`, KBAC policies, App Agent, Token Introspect, MCP
  server config, and external data resolvers. For canbank, the **Bruno
  collection** (`bruno/iag-demo`) mirrors the same data request-by-request
  as a manual alternative, as linked below.

  Required in either case:
    - the canbank graph ingested (instant-stack `data/canbank`, or Bruno:
    [`bruno/iag-demo/ingest/{canbank,customers,customer-docs}`](bruno/iag-demo/ingest)),
    - the agent-workflow graph ingested (Bruno:
    [`bruno/iag-demo/ingest/agent-workflow`](bruno/iag-demo/ingest/agent-workflow)):
    `User`s, `Workflow`s (`wf1`/`wf2`/`wf3` plus the Drive/console shapes
    `wf-drive`, `wf-drive-analyst`, `wf-drive-console`, `wf3-console`) and the
    `Agent` chains. The `INVOKES` edges between workflows and agents carry a
    `workflow_name` property (and `discriminating_property: workflow_name` on
    edges into shared agent nodes): the gateways' `get_agent_workflows`
    ContX IQ query (v2) filters on it at every hop, so ingest without these
    properties resolves **no** workflows,
    - a `CAN_TRIGGER` edge from the calling `User` to the workflow (see
    [`bruno/iag-demo/authzen/subject-can-trigger-workflow.yml`](bruno/iag-demo/authzen/subject-can-trigger-workflow.yml)),
    - a ContX IQ knowledge query + policy: pick any pair from
    [`bruno/iag-demo/ciq-context`](bruno/iag-demo/ciq-context),
    - an App Agent with a credentials token,
    - a Token Introspect config pointing at the Curity issuer,
    - the project's **MCP server configuration** `enabled` and bound to that App
    Agent (`app_agent_id`) and Token Introspect (`token_introspect_id`). This
    config is created together with the project (it can't be created from the
    demo); enable and configure it in the IndyKite console. The MCP server
    resolves the App Agent server-side from it, so MCP callers no longer send an
    App Agent token.
- **Provider clients** for `console` (chatbot login), `indykiteagent`
  (orchestrator), `indykiteagent-2` (retriever), `indykiteagent-3`
  (weather), and `indykiteagent-4` (analyst): each with its secret.
  The optional Drive MCP PoC additionally needs `indykiteagent-drive`, and
  the optional CRM profile needs `indykiteagent-crm`.
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
| `CIQ_QUERY_ID` | Knowledge query ID or name from your project |
| `WORKFLOW_ID` | The `external_id` of the single `Workflow` node to whitelist (sets `JARVIS_CONTX_IQ_ALLOWED_WORKFLOW_ID` in [`iag-base-docker.yaml`](iag-base-docker.yaml)). If unset/removed, all workflows defined in the IKG are considered when authorizing requests. |
| `APP_AGENT_CREDENTIALS_TOKEN` | App Agent credentials token used by the gateway for its ContX IQ calls (`JARVIS_CONTX_IQ_APP_AGENT_CREDENTIALS_TOKEN`) |
| `MCP_SERVER_ORIGIN` | Scheme + host of the MCP server, e.g. `https://us.mcp.indykite.com` / `https://eu.mcp.indykite.com`. Used as the downstream target of `mcp-iag`. |
| `MCP_SERVER_PATH` | MCP endpoint path, e.g. `/mcp/v1/<PROJECT_GID_URL_ENCODED>`. The compose file appends this to the `mcp-iag` host that the agents call. |
| `MCP_SERVER_URL` | Direct URL to the MCP server (`<MCP_SERVER_ORIGIN><MCP_SERVER_PATH>`). Kept for reference / bypassing `mcp-iag`; by default the agents are routed through the gateway instead. |
| `MCP_IDP_CLIENT_ID` / `_SECRET` | IdP Provider client `mcp-iag` uses to authenticate to the MCP server (e.g. `indykiteagent-mcp`). |
| `CHATBOT_IDP_CLIENT_ID` / `_SECRET` | IdP Provider `console` client |
| `ORCHESTRATOR_IDP_CLIENT_ID` / `_SECRET` | IdP Provider `indykiteagent` client |
| `RETRIEVER_IDP_CLIENT_ID` / `_SECRET` | IdP Provider `indykiteagent-2` client |
| `WEATHER_IDP_CLIENT_ID` / `_SECRET` | IdP Provider `indykiteagent-3` client (weather agent) |
| `ANALYST_IDP_CLIENT_ID` / `_SECRET` | IdP Provider `indykiteagent-4` client (analyst agent) |
| `ANALYST_WORKFLOW_ID` | Workflow the analyst gateway allows. Leave **empty** (default) so all analyst call shapes authorize (`wf3` direct, `wf3-console`/`wf-drive-*` via the console); `JARVIS_CONTX_IQ_ALLOWED_WORKFLOW_ID` takes a single value per gateway, so pinning breaks the other shapes. |
| `WEATHER_WORKFLOW_ID` | Workflow the weather gateway allows (default `wf2`). |
| `DRIVE_WORKFLOW_ID` | *(Drive profile)* Workflow the drive gateway allows. Leave **empty** so `wf-drive`, `wf-drive-analyst` and `wf-drive-console` all authorize. |
| `ANALYST_MCP_SERVER_URLS` | *(Drive profile)* Multi-backend analyst: `indykite=http://mcp-iag:8886/mcp/v1/<PROJECT_GID_URL_ENCODED>,drive=http://drive-mcp-iag:8887/mcp`. Required for Drive prompts; see the Drive section. |
| `CIQ_QUERY_HQ_WEATHER` | Optional. Name/GID of the `get-hq-weather` knowledge query used by the weather agent for HQ prompts (default: `get-hq-weather`). Create it in `canbank` (slot 9 + the `weather` / `weather-units` EDRs). Without it, all weather prompts go to Open-Meteo. |
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
```

### 4. Pin the Agent Gateway image tag

[`iag-base-docker.yaml`](iag-base-docker.yaml) pins a concrete version:

```yaml
services:
  iag-base:
    image: indykite/agent-gateway:2.21.1   # or any newer tag from Docker Hub
```

All gateways inherit this tag. `2.21.1` implements MCP proxying
(`JARVIS_PROTECTED_AGENT_PROTOCOL: mcp`), which the `mcp-iag` and
`drive-mcp-iag` services need - the published `2.0.x` tags ignore the protocol
and 404 every MCP method after the auth pipeline passes. Avoid floating tags
like `latest` so the demo behaviour is reproducible.

If you are on Apple Silicon, add a `platform` attribute:

```yaml
services:
  iag-base:
    image: indykite/agent-gateway:2.21.1
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
| `drive-mcp-iag` | `8887` | *(profile `drive`)* Agent Gateway protecting the Google Drive MCP server (workflows `wf-drive`/`wf-drive-analyst`/`wf-drive-console`) |
| `drive-mcp` | `8000` | *(profile `drive`)* Google Drive MCP server (stdio, wrapped by supergateway into Streamable HTTP) |

Open `http://localhost:3000` in a browser and log in as one of the demo users
(e.g. `leslie`, `roy`, …). Make sure you use the same hostname as
`CHATBOT_HOST` in `.env`: don't mix `localhost` and `127.0.0.1`, the OAuth
redirect URL has to match the Provider client.

### Protecting MCP traffic (`mcp-iag`)

By default the `retriever` and `weather` agents reach the IndyKite MCP server
**through** a dedicated gateway, `mcp-iag`, instead of calling it directly. This
puts MCP traffic behind the same token introspection, AuthZEN authorization and
audit logging as the A2A flows.

How it is wired:

- `mcp-iag` extends `iag-base` (so it inherits the IdP / AuthZEN / CIQ / audit
  config) and sets `JARVIS_PROTECTED_AGENT_PROTOCOL: mcp`, which switches the
  gateway from the default A2A proxy into MCP (Streamable HTTP) proxy mode.
- Its downstream target is `JARVIS_PROTECTED_AGENT_BASE_URL: ${MCP_SERVER_ORIGIN}`:
  only the origin is needed, because the gateway forwards the incoming request
  path (`${MCP_SERVER_PATH}`) on top of it.
- The agents call `http://mcp-iag:8886${MCP_SERVER_PATH}` (set via `MCP_SERVER_URL`
  in [`docker-compose.yaml`](docker-compose.yaml)). The gateway introspects the
  caller's token, runs the AuthZEN check, then mints its own token (via
  `MCP_IDP_CLIENT_ID` / `_SECRET`) for the downstream MCP server.

> [!IMPORTANT]
> The pinned `indykite/agent-gateway` tag must be **recent enough to support MCP
> proxying** (the `JARVIS_PROTECTED_AGENT_PROTOCOL: mcp` mode). Older images
> ignore the protocol and behave as an A2A proxy. Check
> [the tag list on Docker Hub](https://hub.docker.com/r/indykite/agent-gateway/tags).

<!-- -->

> [!NOTE]
> MCP calls now carry **only** the user's Bearer token: the same chatbot user
> token used by the A2A flows - so `mcp-iag` runs the same AuthZEN check inherited
> from `iag-base` (`JARVIS_AUTHZEN_ACTION: CAN_TRIGGER`, `JARVIS_AUTHZEN_SUBJECT_TYPES: User`),
> with the Bearer token's `sub` as the subject. The downstream IndyKite MCP server
> resolves the App Agent it uses to call IndyKite APIs **server-side**, from the
> project's MCP server configuration (`app_agent_id`) - callers no longer send an
> App Agent token (`IK_APP_AGENT_KEY` / `X-IK-ClientKey`), which the MCP server has
> removed.

**To bypass the gateway** (talk to the MCP server directly, the original
behaviour), set `MCP_SERVER_URL` back to `${MCP_SERVER_URL}` in the `retriever`,
`weather`, and `analyst` service definitions in
[`docker-compose.yaml`](docker-compose.yaml).

### Proxying a non-IndyKite MCP server: Google Drive (profile `drive`)

The gateway's MCP proxy mode is downstream-agnostic: it forwards any
Streamable HTTP MCP server (any method/path, SSE streaming, `Mcp-Session-Id`
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
the Drive workflows and the `indykiteagent-drive` agent: they are inert while
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
ANALYST_MCP_SERVER_URLS=indykite=http://mcp-iag:8886/mcp/v1/<PROJECT_GID_URL_ENCODED>,drive=http://drive-mcp-iag:8887/mcp
```

When set, this overrides the single `MCP_SERVER_URL` and every backend's tool
names are prefixed with its alias (`indykite_ciq_execute`, `drive_search`,
`drive_list_resources`, ...), so one prompt to the analyst
(`http://localhost:8885`) can route to graph/AuthZEN data or Google Drive as
appropriate. A backend that is down is skipped with a warning instead of
failing the request.

A workflow in the IKG holds exactly **one** agent chain (the gateway keeps one
chain per workflow id), so each call shape is its own workflow:

| Workflow | Chain | Used by |
| --- | --- | --- |
| `wf-drive` | `indykiteagent-drive` | `test-drive.sh`, bruno (direct) |
| `wf-drive-analyst` | `indykiteagent-4 -> indykiteagent-drive` | prompt to the analyst (`:8885`) |
| `wf-drive-console` | `indykiteagent -> indykiteagent-4 -> indykiteagent-drive` | chatbot console via `query_drive` |
| `wf3-console` | `indykiteagent -> indykiteagent-4 -> indykiteagent-mcp` | console-routed analyst reaching the IndyKite MCP |

Parallel `INVOKES` edges between the same agent pair are discriminated by the
`workflow_name` property (`discriminating_property: workflow_name`). Because
`allowed_workflow_id` is single-valued, the analyst and drive gateways run
unpinned (`ANALYST_WORKFLOW_ID=` / `DRIVE_WORKFLOW_ID=` empty, like `mcp-iag`)
so all shapes authorize. Re-ingest the workflow data after upgrading
(instant-stack, or bruno `ingest/agent-workflow`).

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

The CRM agent receives the gateway-minted delegation token, reads its `sub` +
`act` chain, then exchanges a signed JWT Bearer assertion (RFC 7523) for a
Salesforce access token and creates the Case - the description records
"Filed on behalf of `<subject>` via agent chain ...". The audit terminal shows
**two** TOKEN cards per run: the IndyKite delegation token and the Salesforce
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
   `COMPOSE_PROFILES`. The dataset must carry the `wf-crm` workflow
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

### Parallel multi-agent MCP (WF4)

Mirrors the jarvis-proto e2e suite *"12 IAG Tests / 06 WF4 - Parallel
Multi-Agent MCP"* (ENG-8855): several users and agents hold **concurrent MCP
sessions through the same `mcp-iag` gateway**, and authorization follows each
user's Bearer token: not the payload, and not the shared agent.

The graph models this with three workflows converging on the MCP agent node
(`indykiteagent-mcp`):

- `wf1`: `millicent`/`carol`/… → orchestrator (`indykiteagent`) → retriever
  (`indykiteagent-2`) → MCP
- `wf2`: `jane` → weather (`indykiteagent-3`) → MCP
- `wf3`: `millicent` (only) → analyst (`indykiteagent-4`) → MCP

Two ways to see it:

- **Bruno**: run
  [`bruno/iag-demo/wf4-parallel-mcp`](bruno/iag-demo/wf4-parallel-mcp) in
  order. It opens three MCP sessions against `mcp-iag` (millicent via retriever,
  carol via retriever, millicent via analyst), interleaves them round-robin
  (initialize A/B/C, list-tools A/B/C, tool-calls A/B/C), asserts the three
  `Mcp-Session-Id`s are distinct, and shows the per-user decisions: the same
  `authzen_evaluate` call on `wf3` returns `decision: true` for millicent and
  `decision: false` for carol, because the MCP subject is bound to the bearer
  token. Fill the `user_token_millicent` / `user_token_carol` secrets in the
  Bruno environment first (see the folder docs).
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
- *"What's the weather at CanBank HQ?"* (routed to the `weather_agent` → CIQ `get-hq-weather` → `weather` + `weather-units` resolvers; requires `CIQ_QUERY_HQ_WEATHER` and the canbank EDR setup)

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
`analyst-iag` (`wf-drive-console`) → `drive-mcp-iag`.

### 7. Troubleshooting

- **`manifest unknown` / `manifest for indykite/agent-gateway:<tag> not found`**:
  the pinned tag doesn't exist for your platform. Pick a valid one from
  [Docker Hub](https://hub.docker.com/r/indykite/agent-gateway/tags) (`2.21.1`
  or newer for MCP proxying) and, on Apple Silicon, add `platform: linux/amd64`.
- **OAuth redirect mismatch**: the Provider `console` client's redirect URL
  must exactly match `http://${CHATBOT_HOST}:${CHATBOT_PORT}/auth/callback`.
- **`401 Unauthorized` / `403 Forbidden` on every prompt**: the user you're
  logged in as isn't allowed to `CAN_TRIGGER` the workflow, or the CIQ query
  isn't returning rows. Verify with
  [`bruno/iag-demo/authzen/subject-can-trigger-workflow.yml`](bruno/iag-demo/authzen/subject-can-trigger-workflow.yml)
  and the matching CIQ query in `bruno/iag-demo/ciq-context`.
- **`401` / `403` on MCP calls (retriever/weather data lookups)**: the user's
  Bearer token isn't being accepted. Confirm it's introspectable and bound to the
  project's Token Introspect issuer/audience, that it passes the `mcp-iag` AuthZEN
  check (`CAN_TRIGGER` / `User`), and that the project has an **enabled MCP server
  configuration** with a valid `app_agent_id` (the App Agent is resolved
  server-side; a missing/disabled config rejects all MCP requests). A `401` that
  returns `.well-known/oauth-protected-resource` metadata means the Bearer token
  is missing/expired/wrongly-bound: not a missing App Agent key. To isolate
  whether the gateway is the cause, temporarily bypass it (set `MCP_SERVER_URL`
  back to the direct URL).
- **`.env` changes don't take effect**: `docker compose restart` does **not**
  re-read `.env` - environment is baked at container creation. Recreate
  instead: `docker compose up -d --force-recreate <service>`.
- **`403 {"message":"Authorization check failed"}` from a gateway, with
  `no workflow matches the actors chain` in its debug log**: the delegation
  chain of the call (visible as `actorsChain` in the log) has no matching
  workflow in the IKG - either the shape isn't ingested, or the gateway's
  `JARVIS_CONTX_IQ_ALLOWED_WORKFLOW_ID` pin excludes it (`skipping workflow
  not allowed by configuration`). Remember: one workflow holds exactly one
  chain.
- **Drive calls fail with `invalid_request` / Google `403 API not enabled`**:
  enable the Google Drive API in the OAuth client's GCP project (step 1 of the
  Drive section); `invalid_request` right after an auth run that *was* working
  means token refresh failed - re-run the auth bootstrap.
- **Drive file reads answer "binary document"**: the file is an uploaded
  binary, not a Google-native doc - convert it (*Open with → Google Docs*).
- **Tail gateway logs** to see the introspect / exchange / CIQ / AuthZen
  decisions:

  ```bash
  docker compose logs -f orchestrator-iag retriever-iag mcp-iag analyst-iag drive-mcp-iag
  ```

### 8. Stop

```bash
docker compose down
```
