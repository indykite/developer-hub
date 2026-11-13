# cib-sentinel

An autonomous agent as the **subject** of its own requests, in a corporate and
investment banking control room.

No person asks anything. The Control Room Sentinel wakes on a schedule, pulls
the project's events from a Google Cloud Pub/Sub subscription, and when a new
mandate or a new trade order appears in the knowledge graph it starts a
mission on its own: a language model decides the next task, the task goes
through the Agent Gateways with the Sentinel's own token, and the graph decides
what it may do. The console shows every message on the wire, every task, every
gateway decision and token exchange, and where the Sentinel was stopped.

```text
 upstream feed ──Capture API──▶ IndyKite graph ──event sink──▶ Pub/Sub topic
                                     ▲                             │ pull, every N seconds
                                     │                             ▼
   mcp-iag ◀─ retriever ◀─ retriever-iag ◀─ orchestrator ◀─ orchestrator-iag ◀── Sentinel
      │                                         │                                (subject: its own
      └─ IndyKite MCP server                    └─▶ analyst-iag ─▶ analyst        Auth0 token)
         (knowledge queries)                        (deal room: refused)
```

## Contents

- [The use case](#the-use-case)
- [An agent as the subject](#an-agent-as-the-subject)
- [What the console shows](#what-the-console-shows)
- [Folders](#folders)
- [Running it](#running-it)
- [The wire](#the-wire)
- [How the Sentinel decides](#how-the-sentinel-decides)
- [Intent (optional)](#intent-optional)
- [Troubleshooting](#troubleshooting)

## The use case

Meridian is a bank with a private side (advisory, which holds confidential
deal information) and a public side (trading). The **information barrier**
between them is the Sentinel's job: when the bank takes a mandate on a
company, the company goes on the restricted list and open orders in its
securities are put on hold.

The Sentinel is stopped at three boundaries, each decided by the graph:

| Boundary | Who decides | The question | In the demo |
| --- | --- | --- | --- |
| Admission | each Agent Gateway, one AuthZEN call | Is this chain of agents a workflow the Sentinel may trigger? | The deal room's gateway refuses the Sentinel: only the advisory desk's workflow passes there. |
| Capability | the MCP gateway | Which tools does that workflow give the last agent? | `release-hold` and `get-deal-documents` are withheld from the Sentinel's workflow. |
| Graph | the knowledge query's policy | Does the data sit inside the Sentinel's coverage? | A mandate booked in APAC is invisible to it; a hold on an order in an unrestricted company writes nothing. |

The upstream feed offers four events:

| Event | What the Sentinel does |
| --- | --- |
| New mandate: Project Aurora (EMEA) | Reads the mandate, restricts Brightwater Pharma, holds order `ord-7001`, and is refused the term sheet at the deal room. |
| New mandate: Project Monsoon (APAC) | Cannot read the mandate (outside its coverage) and escalates to a person. |
| New order in a restricted name | Finds Halcyon Energy on the restricted list and holds the order. |
| New order in an unrestricted name | Finds no restriction and places no hold. |

## An agent as the subject

Every piece that usually names a person names the agent here:

| Piece | Setting |
| --- | --- |
| The Sentinel's token | Auth0 client-credentials grant of its own Machine to Machine application, for the API `cib-autonomous`. Its `sub` is `<client_id>@clients`. |
| The graph | The Sentinel is an `Agent` node with that `sub` as `external_id`, with `CAN_TRIGGER` to its workflow and `COVERS` to its regions. |
| Token Introspect | `auth0-agent-introspect` maps issuer + audience `cib-autonomous` to node type `Agent`, so whoami answers `Agent` and the Sentinel's `sub`. |
| Agent Gateways | `protected_agent.authentication.audiences` is `cib-autonomous`: the only tokens admitted at the first hop are an agent's own. |
| Token Service | The `subject_token` matcher accepts the audience `cib-autonomous`; the delegation's `sub` is the Sentinel, `act` is the chain of gateways. |
| Policies | Every KBAC and knowledge-query policy has subject type `Agent` (`agent-can-reach-agent`, `agent-can-use-tool`, ...). |

The fleet (orchestrator, retriever, analyst, MCP gateway) is the one of
`a2a/ibac-demo`, built from its sources and given this app's usecase bundle.
Its orchestrator names its analyst tool `query_drive`, so the deal room is
presented to it as the mandate's document store in Google Drive.

## What the console shows

`http://localhost:3300`, one page:

- **Upstream feed**: the four events. A click writes the nodes and
  relationships to the graph through the Capture API.
- **The wire**: every message the Sentinel pulled from the subscription. The
  one that starts a mission is marked.
- **The wall**: the fleet on top, the graph below with the barrier between the
  private and the public side. Hops light up green or red as the gateways
  decide; what the feed wrote is amber, what the Sentinel wrote is green.
- **Missions**: per mission, each task with the Sentinel's reason, the hops it
  travelled (gateway decisions and delegations), what the control plane
  refused, and the answer. Escalations to a person sit on top.
- **Audit**: the records of the gateways, the Token Service and the agents.
  A click opens the record, including the decoded delegation.

The console has no login and is published on the host's loopback only.

## Folders

| Folder | Content |
| --- | --- |
| `sentinel/` | The autonomous agent: Pub/Sub pull, the decision loop, the A2A calls. |
| `console/` | The page and its backend (records, graph merge, Capture feed, reset). |
| `usecases/cib/` | `mission.json` (brief and procedures), `events.json` (the feed), `graph.json` (the provisioned graph for the wall), the agents' skills. |
| `token-service/` | Example configuration and `keygen.py`. |
| `../../instant-stack/data/cib/` | The dataset: nodes, relationships, policies, knowledge queries, token introspects, event sink. |

## Running it

### 1. Auth0

In the tenant:

- an **API** with identifier `cib-autonomous`;
- a **Machine to Machine application** "Sentinel", authorized for that API;
- one Machine to Machine application per gateway (orchestrator, retriever,
  analyst, mcp), authorized for the fleet's API (`ibac-agents`). The ibac-demo
  applications can be reused.

### 2. Pub/Sub

On the topic the event sink publishes to, create a **pull subscription** and a
service-account key with `roles/pubsub.subscriber` on it. Without one the app
still runs: see [The wire](#the-wire).

### 3. Configure

```sh
cp .example.env .env        # fill AUTH0_DOMAIN and the client ids and secrets
make keys                   # writes token-service/token-service.yaml, prints two values
```

`make keys` prints `TOKEN_SERVICE_CLIENT_SECRET` (for this `.env`) and
`TOKEN_SERVICE_PUBLIC_JWK` (for instant-stack's `.env`).

### 4. Provision the project

In `instant-stack/.env`: `DATASET=cib`, `AUTH0_ISSUER`, `AUTH0_SUB_SENTINEL`,
`AUTH0_SUB_ORCHESTRATOR`, `AUTH0_SUB_RETRIEVER`, `AUTH0_SUB_ANALYST`,
`AUTH0_SUB_MCP` (each `<client_id>@clients`), `TOKEN_SERVICE_PUBLIC_JWK`, and
the `PUBSUB_*` values of the sink. Provision, then copy into this `.env`:
`INDYKITE_BASE_URL`, `APP_AGENT_CREDENTIALS_TOKEN`, the MCP server origin and
path, and optionally the ids of the knowledge queries `board-restrictions`
and `board-holds`.

### 5. Build and start

```sh
make                        # fleet images from ../ibac-demo, console, sentinel
docker compose up -d
```

### 6. Try it

Open `http://localhost:3300`, click **New mandate: Project Aurora** and watch
the wire. **Wake now** makes the Sentinel pull immediately instead of waiting
for its schedule. **Reset the board** deletes from the graph what the feed and
the Sentinel wrote.

## The wire

The event sink routes `indykite.audit.*` to the topic, so the subscription
carries the Capture upserts that feed the graph and the decisions the
Sentinel's own actions cause. Messages are CloudEvents; the event type is the
`ce-type` attribute, the body is JSON. A mission starts on
`indykite.audit.capture.upsert.node` of a node type that has a procedure in
`mission.json` (`Mandate`, `TradeOrder`). Events older than ten minutes are
shown and ignored, so a backlog (the provisioning ingest, for instance) starts
nothing.

With `PUBSUB_SUBSCRIPTION` empty, the console hands each fed node to the
Sentinel directly; the header then says `direct feed` and the wire shows only
those.

## How the Sentinel decides

`SENTINEL_PLANNER=llm` (default): before each task the model receives the
brief, the event, the procedure's guidance and what happened so far, including
what the gateways refused, and answers one move: a task, an escalation, or the
end. A refused task is never repeated. `SENTINEL_PLANNER=scripted` follows the
written steps of `mission.json` instead; if a model call fails, the Sentinel
falls back to them for that move.

Each task is one A2A message to the orchestrator's gateway. A mission costs
one model call per move in the Sentinel plus the fleet's own calls;
`SENTINEL_STEP_PAUSE_SECONDS` spaces the tasks for a model with a low
request-per-minute quota.

## Intent (optional)

With the `intent` profile and `IBAC_ENABLED=true`, the orchestrator's gateway
asks the Intent Agent what each task intends, after admitting it:

1. The gateway sends the task's text and the subject it resolved (`Agent` and
   the Sentinel's id) to the Intent Agent.
2. The Intent Agent answers a list of AuthZEN evaluations in the vocabulary
   of the project's KBAC policies and data schema, such as *Agent sentinel,
   CAN_RESTRICT, Company*. A task it cannot put in those words is answered
   with a refusal and is not passed on; the mission card is stamped
   `no intent`.
3. The gateway exchanges the token a second time so the evaluations travel in
   the delegation (`act.intent`), and writes an `INTENT` audit record.

The gateway carries the intent; it does not decide on it. The console asks
the platform for a decision on each evaluation (`/access/v1/evaluation`, or
`/access/v1/search/resource` when the evaluation names no resource) and shows
`permitted` or `denied` beside it on the mission card. That check is the
console's, with the application's credentials.

The vocabulary is six KBAC policies with subject `Agent` in the dataset:
`CAN_READ` on `Mandate`, `Restriction`, `TradeOrder` and `DealDocument`,
`CAN_RESTRICT` on `Company`, `CAN_HOLD` on `TradeOrder`. They state the same
rules as the knowledge-query policies.

To run it:

- Auth0: an API `intent_agent`, with the orchestrator gateway's application
  authorized for it.
- `.env`: `COMPOSE_PROFILES=intent` and `IBAC_ENABLED=true`.
- `docker compose up -d` starts `intent-agent` and `intent-redis` with the
  rest.

The Token Service accepts at most ten evaluations per intent
(`idp.max_intent_evaluations`); a task that reads as more is answered as a
refusal.

## Troubleshooting

| Symptom | Look at |
| --- | --- |
| Header says `wire error` | The subscriber key: `PUBSUB_SUBSCRIBER_KEY_FILE` must be readable by the user `SENTINEL_RUN_AS` (`id -u`), and the service account needs the subscriber role on `PUBSUB_SUBSCRIPTION`. |
| Nothing on the wire after a feed | The project's event sink (provisioned only when instant-stack's `PUBSUB_*` values are set), and that the subscription belongs to its topic. |
| Every task is `401` at `orchestrator-iag` | whoami did not resolve the Sentinel: `AUTH0_ISSUER` or `AUTH0_SUB_SENTINEL` in instant-stack's `.env` differ from the tenant, or the Sentinel application is not authorized for `cib-autonomous`. |
| Every task is `403` at `orchestrator-iag` | The Agent nodes carry other subjects than the gateways' `<client_id>@clients`: check the `AUTH0_SUB_*` values and re-provision. |
| `No access token` on a task | `SENTINEL_IDP_CLIENT_ID` / `SENTINEL_IDP_CLIENT_SECRET`, and the bridge's log line for the token request. |
| Every task is `502` with intent on | The Intent Agent is not running (`COMPOSE_PROFILES=intent`), or the orchestrator gateway's application is not authorized for the `intent_agent` API. |
| Every task is stamped `no intent` | The six vocabulary policies are not provisioned, or the task's wording does not match their actions and resource types. |
| The wall does not show restrictions or holds | `BOARD_RESTRICTIONS_QUERY_ID` and `BOARD_HOLDS_QUERY_ID` are not set; the header of the wall says `graph read-back off`. |

## Development

```sh
pip install -r console/requirements.txt -r sentinel/requirements.txt pytest
make test
```
