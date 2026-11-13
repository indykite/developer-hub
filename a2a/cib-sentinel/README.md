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
- [Development](#development)

## The use case

Meridian is a bank with a private side (advisory, which holds confidential
deal information) and a public side (trading). The **information barrier**
between them is the Sentinel's job: when the bank takes a mandate on a
company, the company goes on the restricted list and open orders in its
securities are put on hold.

The Sentinel is stopped at these boundaries, each decided by the graph, in
the order a request meets them:

| Boundary | Who decides | The question | In the demo |
| --- | --- | --- | --- |
| Admission | each Agent Gateway, one AuthZEN call | Is this chain of agents a workflow the Sentinel may trigger? | The deal room's gateway refuses the Sentinel: only the advisory desk's workflow passes there. |
| Capability | the MCP gateway | Which tools does that workflow give the last agent? | `release-hold` and `get-deal-documents` are withheld from the Sentinel's workflow. |
| Intent (optional) | the MCP gateway, then the platform | Does the tool call fit what the task set out to do, and may the Sentinel do it to the resource the call names? | A write passes only if the task's intent names it; a hold is decided by the platform for that order before the query runs. See [Intent](#intent-optional). |
| Graph | the knowledge query's policy | Does the data sit inside the Sentinel's coverage? | A mandate booked in APAC is invisible to it; a hold on an order in an unrestricted company writes nothing. |

The upstream feed offers four events:

| Event | What the Sentinel does |
| --- | --- |
| New mandate: Project Aurora (EMEA) | Reads the mandate, restricts Brightwater Pharma, holds order `ord-7001`, and is refused the term sheet at the deal room. |
| New mandate: Project Monsoon (APAC) | Cannot read the mandate (outside its coverage) and escalates to a person. |
| New order in a restricted name | One task, the hold on condition that the company is restricted: it finds Halcyon Energy on the restricted list and holds the order. |
| New order in an unrestricted name | The same task: it finds no restriction on Kestrel Logistics and leaves the order alone. |

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

- **Header**: a link, *What this app does*, opens a summary in front of the
  page. The Sentinel's card shows its subject, audience, what decides its
  moves (a model or the written procedure), where its events come from, and
  how many tokens Auth0 issued against how many the bridge reused. A click on
  the card opens the Sentinel's own token.
- **Upstream feed**: the four events. A click writes the nodes and
  relationships to the graph through the Capture API.
- **The wire**: every message the Sentinel pulled from the subscription. The
  one that starts a mission is marked; a click opens the full body.
- **Missions**: per mission, each task with the Sentinel's reason, the hops it
  travelled (gateway decisions and delegations), what the control plane
  refused, and the answer. With the intent option, also the intent the task
  was read as and what the MCP gateway had the platform decide. Escalations
  to a person sit on top.
- **The wall**: the fleet on top, the graph below with the barrier between the
  private and the public side. It draws one task: its hops through the fleet,
  numbered in the order the gateways admitted them, then the graph nodes the
  task named or got back, down to the desk of an order. A click on a task
  card shows that task; otherwise the wall follows the latest. Next to the
  MCP gateway are the tools the Sentinel's workflow is granted and the ones
  withheld from it. What the feed wrote is amber, what the Sentinel wrote is
  green. The four tiles below (Admission, Capability, Intent, Graph) say
  which boundary the task passed or hit; Intent stays neutral when the
  intent option is off.
- **Audit**: the records of the gateways, the Token Service and the agents.
  *Open full view* covers the page with every record, untruncated, with
  filters and a text search; a click on a row opens the record with the
  decoded delegation.

The nodes a task touched are found by matching ids, company names and tickers
in the task and its answer: a reading aid, not a record of the platform.

The console has no login and is published on the host's loopback only. It
refuses any write a browser sends from another origin, and the three actions
of the page (feed, reset, wake) also need a header only the page sends, so a
site open in another tab cannot write through it.

## Folders

| Folder | Content |
| --- | --- |
| `sentinel/` | The autonomous agent: Pub/Sub pull, the decision loop, the A2A calls. |
| `console/` | The page and its backend (records, graph merge, Capture feed, reset). |
| `usecases/cib/` | `mission.json` (the brief, the goal and rules the model follows, and the written procedure for each kind of event), `events.json` (the feed), `graph.json` (the provisioned graph for the wall), the agents' skills. |
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

`token-service.yaml` holds the private signing key and is readable by its
owner only; the Token Service container runs as that user
(`TOKEN_SERVICE_RUN_AS`, the output of `id -u`).

### 4. Provision the project

In `instant-stack/.env`: `DATASET=cib`, `AUTH0_ISSUER`, `AUTH0_SUB_SENTINEL`,
`AUTH0_SUB_ORCHESTRATOR`, `AUTH0_SUB_RETRIEVER`, `AUTH0_SUB_ANALYST`,
`AUTH0_SUB_MCP` (each `<client_id>@clients`), `TOKEN_SERVICE_PUBLIC_JWK`, and
the `PUBSUB_*` values of the sink. Provision, then copy into this `.env`:
`INDYKITE_BASE_URL`, `APP_AGENT_CREDENTIALS_TOKEN`, the MCP server origin and
path, and optionally the ids of the knowledge queries `board-restrictions`
and `board-holds`.

The dataset holds thirteen KBAC policies with subject `Agent` (admission,
capabilities, coverage, and the six of the intent vocabulary) and nine
knowledge queries, three of which end their description with the
`x-coaz-mappings:` line the intent option uses. A mandate and a trade order
carry the id of their company as a property, and a deal document the id of
its mandate, next to the relationships that say the same: a task can then
name the company, and the Intent Agent can filter on it.

Provisioning creates; it does not update. To change a query or a policy that
exists, edit the manifest and update it in place through the Config API
(`PUT /configs/v1/knowledge-queries/{id}`, with the same fields as the create
and the id recorded in instant-stack's `.env`). Re-running its create step
answers `409 Conflict`. Two rules of the platform apply to any change: no
Cypher reserved word as a variable name, and parameter names of at most 20
characters.

### 5. Build and start

```sh
make                        # fleet images from ../ibac-demo, console, sentinel
docker compose up -d
```

The gateways and the Token Service run the images named in
`AGENT_GATEWAY_IMAGE` and `TOKEN_SERVICE_IMAGE`; both must be of the same
release. After changing either, recreate `token-service` and the four
gateways.

### 6. Try it

Open `http://localhost:3300`, click **New mandate: Project Aurora** and watch
the wire. **Wake now** makes the Sentinel pull immediately instead of waiting
for its schedule.

**Reset the board** deletes from the graph what the feed and the Sentinel
wrote, never the provisioned graph, and clears the page. It is refused while a
mission runs and when the Sentinel cannot confirm that it is idle; nothing is
deleted then. A successful reset drops the events that had not started a
mission yet.

## The wire

The event sink routes `indykite.audit.*` to the topic, so the subscription
carries the Capture upserts that feed the graph and the decisions the
Sentinel's own actions cause. Messages are CloudEvents; the event type is the
`ce-type` attribute, the body is JSON. A mission starts on
`indykite.audit.capture.upsert.node` of a node type that has a procedure in
`mission.json` (`Mandate`, `TradeOrder`). Events older than ten minutes are
shown and ignored, so a backlog (the provisioning ingest, for instance) starts
nothing.

The Sentinel pulls in batches and acknowledges a batch only after every
message of it was taken in. A failed pull loses nothing: what was not
acknowledged is delivered again, and a message seen twice is taken in once.

With `PUBSUB_SUBSCRIPTION` empty, the console hands each fed node to the
Sentinel directly; the header then says `direct feed` and the wire shows only
those.

## How the Sentinel decides

`SENTINEL_PLANNER=llm` (default): before each task the model receives the
brief, the event, the goal and rules for that kind of event, and what happened
so far, including what the gateways refused. It answers one move: a task, an
escalation, or the end. A refused task is never repeated.

`SENTINEL_PLANNER=scripted` follows the written steps of `mission.json`
instead, with no model call by the Sentinel: four tasks for a mandate (read
it, restrict its company, hold that company's orders, ask for the term
sheet), one for an order (hold it if its company is restricted).

A task names the records it acts on by type and id and nothing about the
records to create; the retriever names restrictions and holds itself.

When a model call fails or answers something unusable, the written procedure
takes that move, but only for a mission it ran from the start. A mission whose
tasks the model chose is handed over to a person instead of being continued by
position.

A mission ends in one of three ways:

| Status | When |
| --- | --- |
| `finished` | A finish decision: nothing is left to do. |
| `escalated` | A person has to take over: a step that must not be blocked was, the task budget (`SENTINEL_MAX_STEPS`) ran out before a finish decision, or no valid next move could be decided. |
| `failed` | An error inside the mission; it is closed under its own id. |

Each task is one A2A message to the orchestrator's gateway, and gets one of
these outcomes on its card: `executed`, `refused` (a gateway said no),
`graph said no` (an empty answer from a policy), `failed` (a technical error),
or `no intent` (with the intent option, see below). A mission costs one model
call per move in the Sentinel plus the fleet's own calls;
`SENTINEL_STEP_PAUSE_SECONDS` spaces the tasks for a model with a low
request-per-minute quota.

## Intent (optional)

With the `intent` profile and `IBAC_ENABLED=true`, two gateways use the intent
of each task.

**At the orchestrator's gateway**, where the Sentinel's task enters:

1. After admitting the task, the gateway sends its text and the subject it
   resolved (`Agent` and the Sentinel's id) to the Intent Agent.
2. The Intent Agent answers a list of AuthZEN evaluations in the vocabulary
   of the project's KBAC policies and data schema, such as *Agent sentinel,
   CAN_RESTRICT, Company*. A task it cannot put in those words is answered
   with a refusal and is not passed on; the mission card is stamped
   `no intent`.
3. The gateway exchanges the token a second time so the evaluations travel in
   the delegation (`act.intent`), and writes an `INTENT` audit record.

**At the MCP gateway**, three hops later, every tool call is checked against
that intent:

1. The MCP server publishes, for a tool, what a call to it stands for: the
   `x-coaz-mappings` of its knowledge query (subject type, resource type,
   action, and where in the call the resource id is).
2. A mapped tool passes only when the intent carries an evaluation with the
   same subject type, resource type and action. Otherwise the call is refused
   (`IBAC_USAGE`, `intent_mismatch`).
3. When the mapping names a resource id, the gateway asks the platform for a
   decision on it, with the Sentinel as the subject, before the call reaches
   the MCP server (`RESOLVED_EVALUATIONS`). A denied decision refuses the
   call.
4. A tool without a mapping passes or is refused according to
   `IBAC_UNMAPPED` (`allow` by default).

The retriever's gateway has no Intent Agent, so the intent the MCP gateway
checks is the one read from the Sentinel's task.

The mappings of the dataset, written at the end of each query's description
after `x-coaz-mappings:`:

| Tool | Stands for | Checked |
| --- | --- | --- |
| `raise-restriction` | Agent `CAN_RESTRICT` Company | Against the intent. |
| `hold-order` | Agent `CAN_HOLD` TradeOrder, the order id of the call | Against the intent, then by the platform for that order. |
| `get-deal-documents` | Agent `CAN_READ` DealDocument | Against the intent. |
| the lookups (`get-mandate`, `get-restricted-list`, `get-orders-in-company`) | no mapping | Not against the intent: the capability check and the query's own policy govern them. |

So a write happens only when the task said so, and a hold only on an order
the platform allows the Sentinel to hold: an order in a restricted company,
in a region it covers.

On the page, the fourth tile under the wall, **Intent**, turns green or red
for the task shown, and a mission card shows two blocks: the intent carried
in the delegation, with a decision per evaluation asked by the console
itself (`/access/v1/evaluation`, or `/access/v1/search/resource` when the
evaluation names no resource), and *Checked at the MCP gateway*, the
decisions the gateway obtained from the platform.

The vocabulary is six KBAC policies with subject `Agent` in the dataset:
`CAN_READ` on `Mandate`, `Restriction`, `TradeOrder` and `DealDocument`,
`CAN_RESTRICT` on `Company`, `CAN_HOLD` on `TradeOrder`. They state the same
rules as the knowledge-query policies.

To run it:

- Images: a gateway and a Token Service that carry the intent in the token
  and check tool calls against it, named in `AGENT_GATEWAY_IMAGE` and
  `TOKEN_SERVICE_IMAGE`. With the two published images of `.example.env` the
  gateway asks the Intent Agent and refuses a task without an intent, but the
  delegation carries no intent and nothing is checked at the MCP gateway.
- Auth0: an API `intent_agent`, with the orchestrator gateway's application
  authorized for it.
- `.env`: `COMPOSE_PROFILES=intent` and `IBAC_ENABLED=true`.
- `docker compose up -d` starts `intent-agent` and `intent-redis` with the
  rest.

How a task has to be worded for the Intent Agent to read it, as the written
procedure and the model's guidance in `mission.json` do:

- Name the record the task acts on by its type and id: *company
  brightwater-pharma*, *trade order ord-7004*.
- Do not describe a record through another one. Not *the company of mandate
  X*, but *company C. It is the company of mandate X.* The Intent Agent
  filters on a node's own properties only, which is why a mandate and an
  order carry their company as a property, and a deal document its mandate.
- Do not state the id or the reason of a record to create: a task that tells
  the Intent Agent to set a value it has no parameter for is refused whole.
  The retriever names restrictions and holds itself (`rst-<mandate id>`,
  `hold-<order id>`).
- Do not ask an open question about a whole list in a task of its own. The
  condition goes into the task that needs it: *Place a hold on trade order
  Y, which is in company C, if that company is on the restricted list.*

The Intent Agent keeps what it read from a prompt for a while, so the same
wording gets the same answer, a refusal included. Its time limits are
`INTENT_LLM_TIMEOUT_SECONDS` (one model call), `INTENT_REQUEST_TIMEOUT_SECONDS`
(the whole task) and `INTENT_VALIDATION_MAX_RETRIES`; why it refused a task
is in its log (`docker compose logs intent-agent`, the lines with
`failed validation`).

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
| `token-service` exits with `permission denied` on its configuration | `TOKEN_SERVICE_RUN_AS` is not the user that owns `token-service/token-service.yaml`. |
| Feed, reset or wake answers `403` | The request did not come from the console page itself: reload the page after a console rebuild. |
| `No access token` on a task | `SENTINEL_IDP_CLIENT_ID` / `SENTINEL_IDP_CLIENT_SECRET`, and the bridge's log line for the token request. |
| Every task is `502` with intent on | The Intent Agent is not running (`COMPOSE_PROFILES=intent`), or the orchestrator gateway's application is not authorized for the `intent_agent` API. |
| With intent on, no tool call is checked at the MCP gateway | The tools publish no mapping: the three query descriptions lack the `x-coaz-mappings:` line, or the MCP server does not publish it. The `mcp-iag` records then carry the reason code `missing_coaz_mapping`. |
| A hold is refused with "may not access the data the named tool uses" | The platform denied `CAN_HOLD` for that order: the intended refusal for an order in an unrestricted company or outside the Sentinel's coverage. |
| A write is refused with "does not match the intent" | The task's wording did not yield the evaluation the tool's mapping names (`CAN_RESTRICT` on `Company`, `CAN_HOLD` on `TradeOrder`): read the intent on the mission card. |
| Every task is stamped `no intent` | The six vocabulary policies are not provisioned, or the task's wording does not match their actions and resource types. |
| `docker compose up` stops with `error while removing network ... has active endpoints` | The networks of the compose file changed while the bridge is attached to the old ones: `docker compose down`, then `docker compose up -d`. |
| A mission ends `escalated` with "Task budget used" | It needed more tasks than `SENTINEL_MAX_STEPS`. |
| Reset answers "cannot confirm that the Sentinel is idle" | The Sentinel is not reachable; nothing was deleted. |
| The wall does not show restrictions or holds | `BOARD_RESTRICTIONS_QUERY_ID` and `BOARD_HOLDS_QUERY_ID` are not set; the header of the wall says `graph read-back off`. |

## Development

```sh
pip install -r console/requirements.txt -r sentinel/requirements.txt pytest
make test
```

The tests need no network: the Sentinel's event reading, decisions, task
sending and Pub/Sub intake, and the console's record handling, graph merge,
reset, intent check and cross-origin refusals. Both requirements files pin
exact versions.

To look at the page without a platform, start the console alone with
`USECASE_DIR=../usecases/cib python -m app.main` from `console/` and post
records to `/api/sentinel` and `/api/push-update`.
