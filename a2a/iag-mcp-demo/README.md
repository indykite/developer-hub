# iag-mcp-demo

Demonstrates the Indykite Agent Gateway, a smart, context-enabled gateway that secures A2A and MCP communication channels. This variant routes MCP traffic through a dedicated MCP-protecting gateway (`mcp-iag`); see [Protecting MCP traffic](#protecting-mcp-traffic-mcp-iag).

## Folders

This repo contains the following folders, refer to the README files in each of the folders for additional information.

## `agentgateway.dev`

Contains some configuration yaml files for the open-source AgentGateway tool. See [AgentGateway](https://github.com/agentgateway/agentgateway) for details.

## `chatbot`

A simple Web GUI app and  A2A client that enables Human-user prompting and interactions. The `chatbot` communicates directly with the `orachestrator_agent` (see below).

## `orchestrator_agent`

An A2A agent that manages the interactions with the end-users, and delegates the action to further agents down the workflow chain. For example to the `retriever_agent` (see below).

## `retriever_agent`

An A2A agent that manages all data or IKG requests. It acts as an MCP client to the Indykite MCP Server.

## `weather_agent`

An A2A agent that returns the current weather for a requested city. It calls the public
[Open-Meteo](https://open-meteo.com) geocoding + forecast APIs (no API key required) and
is wired into the stack as an additional downstream agent the `orchestrator_agent` can
delegate to — useful for demonstrating multi-agent routing behind the Indykite Agent
Gateway.

When the prompt mentions CanBank's headquarters (`HQ`, `headquarters`, `office`) **and**
`MCP_SERVER_URL` is configured, the agent takes a different path: it calls the canbank
`get-hq-weather` knowledge query through the IndyKite MCP server. That query reads the
`hq_weather` Weather node, whose `current` and `units` properties are populated live by
the canbank `weather` and `weather-units` external data resolvers (open-meteo). All
other cities still go through the direct Open-Meteo path. See
[`canbank/README.md`](../../canbank/README.md#external-data-resolvers) for the resolver
setup.

## `bruno`

Bruno collection of sample data, ciq queries and kbac queries.

## Running the demo

The stack boots four Agent Gateway instances — three protecting the
orchestrator, retriever, and weather agents (A2A), plus one (`mcp-iag`)
protecting the IndyKite MCP server (MCP) — alongside four in-repo services
(`chatbot`, `orchestrator_agent`, `retriever_agent`, `weather_agent`) wired
together via Docker Compose. See
[Protecting MCP traffic](#protecting-mcp-traffic-mcp-iag) for the MCP gateway.

### 1. Prerequisites

- **Docker + Docker Compose v2**.
- **Python 3.11+** with a package/environment manager — the four in-repo
  services (`chatbot`, `orchestrator_agent`, `retriever_agent`,
  `weather_agent`) each ship a `Pipfile`, so
  [`pipenv`](https://pipenv.pypa.io) is the default. Any equivalent tool
  (`uv`, `poetry`, `venv` + `pip`) works if you prefer. You only need this
  locally if you plan to run or debug the services outside Docker; the
  `docker compose up` path installs everything inside the images.
- **An IndyKite project** with:
    - the canbank graph ingested (run the Bruno collection
    [`bruno/iag-demo/ingest/{canbank,customers,customer-docs}`](bruno/iag-demo/ingest)),
    - a `Workflow` node with `external_id=wf1` and a `CAN_TRIGGER` edge from the
    calling `User` (see
    [`bruno/iag-demo/authzen/subject-can-trigger-workflow.yml`](bruno/iag-demo/authzen/subject-can-trigger-workflow.yml)),
    - a ContX IQ knowledge query + policy — pick any pair from
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
  (orchestrator), `indykiteagent-2` (retriever), and `indykiteagent-3`
  (weather) — each with its secret.
- **(Optional) Gemini API key**, otherwise an **Ollama** instance reachable
  from Docker (default `http://host.docker.internal:11434`).

### 2. Configure `.env`

```bash
cp .example.env .env
```

The root `.env` (this file) is always required — `docker compose` loads it for
every service. The per-service `.env` files under `chatbot/`,
`orchestrator_agent/`, `retriever_agent/`, and `weather_agent/` are only used
when running those services directly on the host (outside Docker); they are
**not** needed for the `docker compose up` path.

Fill in, at a minimum:

| Variable | Where to get it |
| --- | -------------------------------------------------------------------------------------------------------------------------------- |
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
| `CIQ_QUERY_HQ_WEATHER` | Optional. Name/GID of the `get-hq-weather` knowledge query used by the weather agent for HQ prompts (default: `get-hq-weather`). Create it in `canbank` (slot 9 + the `weather` / `weather-units` EDRs). Without it, all weather prompts go to Open-Meteo. |
| `FLASK_SECRET_KEY` | Generate a fresh one: `python -c "import secrets; print(secrets.token_hex(32))"` |

LLM selection:

- `GEMINI_ENABLED=true` + `GEMINI_API_KEY=…` to use Gemini, **or**
- leave `GEMINI_ENABLED=false` and point `OLLAMA_HOST` at your local Ollama
  (`http://host.docker.internal:11434` when running Ollama on the host).

### 3. Build the local service images

The three in-repo services are built locally. There's a makefile for this:

```bash
make                 # build chatbot, orchestrator-agent, retriever-agent, weather-agent
# or individually:
make new-chatbot
make new-orchestrator
make new-retriever
make new-weather
```

### 4. Pin the Agent Gateway image tag

[`iag-base-docker.yaml`](iag-base-docker.yaml) pins a concrete version:

```yaml
services:
  iag-base:
    image: indykite/agent-gateway:2.0.1   # or any newer tag from Docker Hub
```

`2.0.1` is the first release that supports MCP proxying
(`JARVIS_PROTECTED_AGENT_PROTOCOL: mcp`), which the `mcp-iag` service needs.
Avoid floating tags like `latest` so the demo behaviour is reproducible.

If you are on Apple Silicon, add a `platform` attribute:

```yaml
services:
  iag-base:
    image: indykite/agent-gateway:2.0.1
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
| --- | --- | ----------------------------------------------- |
| `chatbot` | `3000` | Web UI + A2A client (log in via IdP Provider) |
| `orchestrator-iag` | `8881` | Agent Gateway protecting the orchestrator |
| `orchestrator` | `6001` | Orchestrator agent |
| `retriever-iag` | `8882` | Agent Gateway protecting the retriever |
| `retriever` | `6002` | Retriever agent (MCP client, via `mcp-iag`) |
| `weather-iag` | `8884` | Agent Gateway protecting the weather agent |
| `weather` | `6004` | Weather agent (Open-Meteo client + MCP client, via `mcp-iag`) |
| `mcp-iag` | `8886` | Agent Gateway protecting the IndyKite MCP server (MCP proxy mode) |

Open `http://localhost:3000` in a browser and log in as one of the demo users
(e.g. `leslie`, `roy`, …). Make sure you use the same hostname as
`CHATBOT_HOST` in `.env` — don't mix `localhost` and `127.0.0.1`, the OAuth
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
- Its downstream target is `JARVIS_PROTECTED_AGENT_BASE_URL: ${MCP_SERVER_ORIGIN}`
  — only the origin is needed, because the gateway forwards the incoming request
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
> MCP calls now carry **only** the user's Bearer token — the same chatbot user
> token used by the A2A flows — so `mcp-iag` runs the same AuthZEN check inherited
> from `iag-base` (`JARVIS_AUTHZEN_ACTION: CAN_TRIGGER`, `JARVIS_AUTHZEN_SUBJECT_TYPES: User`),
> with the Bearer token's `sub` as the subject. The downstream IndyKite MCP server
> resolves the App Agent it uses to call IndyKite APIs **server-side**, from the
> project's MCP server configuration (`app_agent_id`) — callers no longer send an
> App Agent token (`IK_APP_AGENT_KEY` / `X-IK-ClientKey`), which the MCP server has
> removed.

**To bypass the gateway** (talk to the MCP server directly, the original
behaviour), set `MCP_SERVER_URL` back to `${MCP_SERVER_URL}` in the `retriever`
and `weather` service definitions in [`docker-compose.yaml`](docker-compose.yaml).

### 6. Try the demo prompts

See [`CANBANK_DEMO_SCRIPT.md`](CANBANK_DEMO_SCRIPT.md) for the scripted tour.
Quick prompts once you're logged in as **Leslie**:

- *"What policy documents pertain to refunds?"*
- *"Retrieve past decisions that incorporated the 'refund_policy' document."*
- *"Tell me how many shares of NVDA the user with the id: rebecca can purchase"*
- *"What's the weather in London?"* (routed to the `weather_agent` → direct Open-Meteo)
- *"What's the weather at CanBank HQ?"* (routed to the `weather_agent` → CIQ `get-hq-weather` → `weather` + `weather-units` resolvers; requires `CIQ_QUERY_HQ_WEATHER` and the canbank EDR setup)

### 7. Troubleshooting

- **`manifest unknown` / `manifest for indykite/agent-gateway:<tag> not found`** —
  the pinned tag doesn't exist for your platform. Pick a valid one from
  [Docker Hub](https://hub.docker.com/r/indykite/agent-gateway/tags) (≥ `2.0.1`
  for MCP proxying) and, on Apple Silicon, add `platform: linux/amd64`.
- **OAuth redirect mismatch** — the Provider `console` client's redirect URL
  must exactly match `http://${CHATBOT_HOST}:${CHATBOT_PORT}/auth/callback`.
- **`401 Unauthorized` / `403 Forbidden` on every prompt** — the user you're
  logged in as isn't allowed to `CAN_TRIGGER` the workflow, or the CIQ query
  isn't returning rows. Verify with
  [`bruno/iag-demo/authzen/subject-can-trigger-workflow.yml`](bruno/iag-demo/authzen/subject-can-trigger-workflow.yml)
  and the matching CIQ query in `bruno/iag-demo/ciq-context`.
- **`401` / `403` on MCP calls (retriever/weather data lookups)** — the user's
  Bearer token isn't being accepted. Confirm it's introspectable and bound to the
  project's Token Introspect issuer/audience, that it passes the `mcp-iag` AuthZEN
  check (`CAN_TRIGGER` / `User`), and that the project has an **enabled MCP server
  configuration** with a valid `app_agent_id` (the App Agent is resolved
  server-side; a missing/disabled config rejects all MCP requests). A `401` that
  returns `.well-known/oauth-protected-resource` metadata means the Bearer token
  is missing/expired/wrongly-bound — not a missing App Agent key. To isolate
  whether the gateway is the cause, temporarily bypass it (set `MCP_SERVER_URL`
  back to the direct URL).
- **Tail gateway logs** to see the introspect / exchange / CIQ / AuthZen
  decisions:

  ```bash
  docker compose logs -f orchestrator-iag retriever-iag mcp-iag
  ```

### 8. Stop

```bash
docker compose down
```
