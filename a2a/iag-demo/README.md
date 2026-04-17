# iag-demo

Demonstrates the Indykite Agent Gateway, a smart, context-enabled gateway that secures A2A and MCP communication channels.

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

## `bruno`

Bruno collection of sample data, ciq queries and kbac queries.

## Running the demo

The stack boots two Agent Gateway instances (protecting the orchestrator and
retriever respectively) plus three in-repo services (`chatbot`,
`orchestrator_agent`, `retriever_agent`) wired together via Docker Compose.

### 1. Prerequisites

- **Docker + Docker Compose v2**.
- **Python 3.11+** with a package/environment manager — the three in-repo
  services (`chatbot`, `orchestrator_agent`, `retriever_agent`) each ship a
  `Pipfile`, so
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
    - a Token Introspect config pointing at the Curity issuer.
- **Provider clients** for `console` (chatbot login), `indykiteagent`
  (orchestrator) and `indykiteagent-2` (retriever) — each with its secret.
- **(Optional) Gemini API key**, otherwise an **Ollama** instance reachable
  from Docker (default `http://host.docker.internal:11434`).

### 2. Configure `.env`

```bash
cp .example.env .env
```

Fill in, at a minimum:

| Variable | Where to get it |
| --- | -------------------------------------------------------------------------------------------------------------------------------- |
| `INDYKITE_BASE_URL` | `https://api.eu.indykite.com` or `https://api.us.indykite.com` |
| `CIQ_QUERY_ID` | Knowledge query ID or name from your project |
| `WORKFLOW_ID` | The `external_id` of the `Workflow` node (default `wf1`) |
| `APP_AGENT_CREDENTIALS_TOKEN` / `IK_APP_AGENT_KEY` | App Agent credentials token |
| `MCP_SERVER_URL` | `https://us.mcp.indykite.com/mcp/v1/<PROJECT_GID_URL_ENCODED>`  `https://eu.mcp.indykite.com/mcp/v1/<PROJECT_GID_URL_ENCODED>` |
| `CHATBOT_IDP_CLIENT_ID` / `_SECRET` | IdP Provider `console` client |
| `ORCHESTRATOR_IDP_CLIENT_ID` / `_SECRET` | IdP Provider `indykiteagent` client |
| `RETRIEVER_IDP_CLIENT_ID` / `_SECRET` | IdP Provider `indykiteagent-2` client |
| `FLASK_SECRET_KEY` | Generate a fresh one: `python -c "import secrets; print(secrets.token_hex(32))"` |

LLM selection:

- `GEMINI_ENABLED=true` + `GEMINI_API_KEY=…` to use Gemini, **or**
- leave `GEMINI_ENABLED=false` and point `OLLAMA_HOST` at your local Ollama
  (`http://host.docker.internal:11434` when running Ollama on the host).

### 3. Build the local service images

The three in-repo services are built locally. There's a makefile for this:

```bash
make                 # build chatbot, orchestrator-agent, retriever-agent
# or individually:
make new-chatbot
make new-orchestrator
make new-retriever
```

### 4. Pin the Agent Gateway image tag

The public `indykite/agent-gateway` repository on Docker Hub **does not publish
a `latest` tag** — pulling it will fail with `manifest unknown`. Before
running compose, pin a concrete version in
[`iag-base-docker.yaml`](iag-base-docker.yaml):

```yaml
services:
  iag-base:
    image: indykite/agent-gateway:1.783.1   # or any tag from Docker Hub
```

Check
[the tag list on Docker Hub](https://hub.docker.com/r/indykite/agent-gateway/tags)
for newer releases.

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
| `retriever` | `6002` | Retriever agent (MCP client) |

Open `http://localhost:3000` in a browser and log in as one of the demo users
(e.g. `leslie`, `roy`, …). Make sure you use the same hostname as
`CHATBOT_HOST` in `.env` — don't mix `localhost` and `127.0.0.1`, the OAuth
redirect URL has to match the Provider client.

### 6. Try the demo prompts

See [`CANBANK_DEMO_SCRIPT.md`](CANBANK_DEMO_SCRIPT.md) for the scripted tour.
Quick prompts once you're logged in as **Leslie**:

- *"What policy documents pertain to refunds?"*
- *"Retrieve past decisions that incorporated the 'refund_policy' document."*
- *"Tell me how many shares of NVDA the user with the id: rebecca can purchase"*

### 7. Troubleshooting

- **`manifest for indykite/agent-gateway:latest not found`** — you skipped
  step 4. Pin a real tag.
- **OAuth redirect mismatch** — the Provider `console` client's redirect URL
  must exactly match `http://${CHATBOT_HOST}:${CHATBOT_PORT}/auth/callback`.
- **`401 Unauthorized` / `403 Forbidden` on every prompt** — the user you're
  logged in as isn't allowed to `CAN_TRIGGER` the workflow, or the CIQ query
  isn't returning rows. Verify with
  [`bruno/iag-demo/authzen/subject-can-trigger-workflow.yml`](bruno/iag-demo/authzen/subject-can-trigger-workflow.yml)
  and the matching CIQ query in `bruno/iag-demo/ciq-context`.
- **Tail gateway logs** to see the introspect / exchange / CIQ / AuthZen
  decisions:

  ```bash
  docker compose logs -f orchestrator-iag retriever-iag
  ```

### 8. Stop

```bash
docker compose down
```
