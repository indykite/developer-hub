# intent-ibac-gateway

A visual, end-to-end showcase of the **IndyKite Agent Gateway** running
**Intent-Based Access Control** (IBAC) with the **IndyKite Intent Agent**, in
front of a fleet of six agents:

- the **Intent Agent** is the A2A service that turns a natural-language
  prompt into an AuthZEN-style intent - a list of
  `{subject, action, resource, context}` evaluations constrained to the
  vocabulary of your IndyKite Knowledge Graph and the caller's active KBAC
  policies;
- the **Agent Gateway** (release 2.99) sits in front of each agent of the
  fleet. With `ibac.enabled` it sends every prompt to the Intent Agent first
  and answers the caller itself when no intent comes back; then it resolves
  the caller (whoami), grows the delegation chain at the Token Service, asks
  the platform one admission decision over that chain, and forwards the
  message to the agent with the delegated token.

The demo is a copy of [intent-agent-demo](../intent-agent-demo) with the
gateways, the fleet and a console-side **mapping preview** added. Everything
runs locally with docker compose; only the IndyKite platform (schema,
policies, whoami, decisions) and Google Gemini (the agent's extraction model)
are remote.

```text
person ──Auth0 login──▶ access token (aud ibac-people)
console ──A2A SendMessage · Bearer = the person's token──▶ iag-<agent> (Agent Gateway)
    gateway ──introspect the token──▶ auth0-bridge (RFC 7662 over the tenant JWKS)
            ──client credentials (its own M2M token)──▶ auth0-bridge ──▶ Auth0
            ──whoami──▶ IndyKite platform
            ──SendMessage · subject in metadata──▶ intent-agent ──introspect──▶ auth0-bridge
            ──exchange persona + gateway token──▶ token-service ──▶ delegated token (X-IK-Token)
            ──AuthZEN evaluation · CAN_TRIGGER own Agent node · $ik_token.act_chain──▶ IndyKite platform
            ──forward · A2A-Extensions · X-IK-Token──▶ agent-<agent>
            every step: one audit record ──▶ console webhook
console ──intent──▶ mapping preview (the cards' x-coaz-mappings)
console ──AuthZEN evaluations / search──▶ IndyKite platform   (the decision step)
```

What the console shows, live:

- **Playground** - pick an identity and a gateway, send a prompt, watch the
  ten-step pipeline (issue, introspect, exchange, whoami, admission, intent,
  forward, then the preview's discover and confirm, then audit), read the
  gateway's answer and the agent's reply, the intent as tickets with the
  platform's decision, the delegation chain as a handoff lane, and the
  **board**: every card of the fleet, AUTHORIZED or NOT_AUTHORIZED in the
  preview with its reason, the matched skills lit, and for each matched skill
  the generated requests with their ALLOW / DENY. The card of the gateway the
  prompt went through comes first, with what that gateway did; "send through
  the other gateways" asks the rest of the fleet for real and fills each card
  with its gateway's answer.
- **Guardrails** - ambiguous and out-of-scope prompts, prompt injection,
  encoded payloads, SQL in filters, and three token failures. The gateway
  refuses them itself: nothing without an intent reaches an agent.
- **How IBAC works** - the mapping check explained in five steps with the
  demo's own examples: the two sides, a mapping field by field, discovery as
  three yes/no questions, usage as the step of a tool call with the
  references resolved, and where the check stops. On the board, every card carries a one-line verdict in plain
  words and every skill a *why?* fold.
- **IBAC fleet** - the six agent cards as their gateways serve them, every
  skill's mappings, a coverage map of the policy triples, and, once you are
  logged in, each card **as the gateway shows it to you**: the gateway
  answers the authenticated card with the skills you may use only, and the
  page marks the others as hidden.
- **Vocabulary** - the data schema and the ACTIVE policies per subject type:
  everything the agent is allowed to say, and the words the mappings use.
- **Audit** - every record the six gateways, the Intent Agent, the Token
  Service and the bridge's callers delivered to the webhook,
  colour-coded by source, streamed as they arrive.
- **Intent Agent fleet** (the Intent Agent, the Token Service and the console
  as they present themselves), **Ontology** (the schema graph),
  **Architecture** - as in intent-agent-demo, extended with the gateways.

Deep links replay a beat: `/?persona=auth0%7C6aee62c9f745e80a6c6d0455&agent=trading-desk&prompt=...&send=1`,
`/?view=fleet`.

## Contents

- [Folders](#folders)
- [What happens to one prompt](#what-happens-to-one-prompt)
- [IBAC in five steps](#ibac-in-five-steps)
- [The fleet](#the-fleet)
- [Run with docker compose](#run-with-docker-compose)
- [Run on minikube](#run-on-minikube)
- [How the tokens work](#how-the-tokens-work)
- [What the decision step can and cannot show](#what-the-decision-step-can-and-cannot-show)
- [Development](#development)
- [Troubleshooting](#troubleshooting)

## Folders

| Path | What it is |
| --- | --- |
| `console/` | The showcase (FastAPI + static UI): Auth0 login (`app/auth.py`), gateway client, **mapping preview** (`app/ibac.py`), **fleet** (`app/fleet.py`, `data/fleet.json`), decision mapper, audit webhook and stream. Tests in `console/tests`. |
| `auth0_bridge/` | The gateways' and the Intent Agent's identity-provider endpoint in front of Auth0: RFC 7662 introspection over the tenant JWKS and the token endpoint forwarded to Auth0. Copied from ibac-demo. |
| `agents/` | The fleet's stand-in A2A agent: one image, six containers. Serves its card from `fleet.json` and answers a message with a reply that names the skill and shows what the gateway forwarded. |
| `iag-base-docker.yaml` | The settings every gateway of the fleet shares; each `iag-<agent>` service in the compose file extends it. |
| `audit-config.template.yaml` | The gateways' audit delivery (webhook to the console); `make keys` renders it with the key. |
| `token-service/` | Template for the IndyKite Token Service configuration; `make keys` renders it. |
| `nginx/` | Balancer config for the optional `cluster` profile. |
| `chart/` | Helm chart of the whole stack for minikube: console, bridge, Token Service, Intent Agent, Redis, the six stand-in agents and their six gateways. |
| `docker-compose.yaml`, `makefile`, `.example.env` | The compose stack and its knobs. |

Neither product is vendored. The Intent Agent is the published
[`indykite/intent-agent`](https://hub.docker.com/r/indykite/intent-agent)
image (source: [github.com/indykite/intent-agent](https://github.com/indykite/intent-agent)).
The Agent Gateway is the release image you load locally (see
[Run with docker compose](#run-with-docker-compose)). The graph and policies
they work against are the `instant-stack` dataset `data/intent-ibac`: the
`intent` dataset of intent-agent-demo, unchanged, plus what the gateways need.

## What happens to one prompt

The console sends the prompt as an A2A `SendMessage` to the gateway of the
chosen agent, with the person's Auth0 access token as Bearer. The gateway
then does, in order, and audits each step:

1. **Introspects** the Bearer at its identity provider, the `auth0-bridge`
   (RFC 7662 over the tenant's JWKS). Only a token issued for the people's
   API `ibac-people` is accepted; the three token guardrails fail here
   with 401.
2. **Resolves the caller** with the platform's whoami: the token's subject
   becomes a `Person` node of the graph. Every login is a Person; whether that
   person is staff or a customer is found on the graph, never in the token.
3. **Asks the Intent Agent** (`ibac.enabled`): the prompt, with the resolved
   subject in the message metadata and the gateway's own client-credentials
   token as Bearer. No intent, and the gateway answers the caller itself with
   the agent's reason (the `metadata.intent_error` of the answer); the agent
   behind it is never called. The audit record is `INTENT_FOUND` or
   `INTENT_FAILED`, with the prompt.
4. **Exchanges** the person's token and its own at the Token Service (RFC
   8693): the delegated token carries `sub` = the persona and
   `act` = the gateway, so the chain grows by one hop per gateway.
5. **Asks for admission**: one AuthZEN evaluation, subject = the whoami
   result, action `CAN_TRIGGER`, resource = the gateway's own `Agent` node,
   with the chain in `$ik_token.act_chain`. The policies
   `person-can-reach-agent` compares that chain
   with the `Workflow` → `INVOKES` → `Agent` path in the graph. `false` is a
   403 to the caller.
6. **Forwards** the message to the agent with the user's token untouched in
   `Authorization`, the delegated token in `X-IK-Token`, and the delegated
   token extension named in `A2A-Extensions`. The stand-in agent echoes what
   it received, so the trace shows the chain arriving.

The console then adds what the gateway does not do yet: the **mapping
preview** (discovery and usage over the cards' `x-coaz-mappings`, next
section) and the **decision step** (the intent's evaluations and the
preview's generated requests sent to AuthZEN). Separately, once you are
logged in, it asks every gateway for its extended card with your token and
shows which skills each gateway lets you use (see [The fleet](#the-fleet)).

## IBAC in five steps

For a first reading, before the details. The console has the same five steps
as a page (**How IBAC works**, item 07 in the rail), with the examples drawn.

1. **Two sides of one question.** The Intent Agent has turned the prompt
   into an *intent*: evaluations of the form subject → action → resource,
   in the words of the graph's policies ("Person auth0|6aee62c9f745e80a6c6d0455 → GET_PRICE → Quote
   stock_quote"). Every agent publishes an *agent card* with its skills.
   IBAC adds one thing to each skill: a declaration of which subjects may do
   which actions on which resources through it. The check never reads the
   prompt; it compares the intent with the declarations.
2. **A mapping, field by field.** The declaration is a list under the key
   `x-coaz-mappings` on the skill. Each mapping has `subject` (types of
   caller, the id is `$token.sub`: whoever is in the token), `resource`
   (types of thing, the id literal when the skill is bound to one resource,
   a `$reference` when the prompt names it, empty when the skill works on
   "whichever you may"), and `action` (names exactly as the policies spell
   them; none means any).
3. **Discovery: who could serve this?** For each evaluation, every skill of
   every card is asked three yes/no questions: same subject type? same
   resource type? action listed? Three yes on one mapping and the skill
   matches; ids are ignored at this stage. One matching skill makes the card
   AUTHORIZED; none makes it NOT_AUTHORIZED (`intent_mismatch`). A card
   without mappings is settled by the gateway's setting before the engine is
   asked: *allow* (the default, the `no mapping` switch next to the composer)
   skips the check and KBAC alone decides; *deny* hands it to the engine,
   which answers `missing_coaz_mapping`. An intent carries at most 10
   evaluations; a larger one is refused and the caller asked to split it.
4. **Usage: what exactly must be checked?** The step of a tool call (an A2A
   message has none; the console runs it as a preview of what a tool call on
   the matched skill would be checked against). For that skill the ids are
   resolved from the call, never from the intent: `$token.sub` from the
   delegated token, `$request.arguments.tier` from the call's arguments,
   which the preview stands in with the parameter the Intent Agent
   extracted. One AuthZEN request per (subject, resource)
   pair: "Person auth0|69c4129b… → HAS → AccountTier tier_1"
   (`intent_match_authzen_required`). An id left empty is a *search match*
   (`intent_match_search`): nothing is generated, the intent's own evaluation
   decides. A reference that does not resolve drops the entity and the
   mapping does not match.
5. **Where the check stops.** It answers whether the agent declares the
   skill the intent needs and which permission that comes down to. It makes
   no decision: the generated requests go to the platform's AuthZEN API,
   which answers ALLOW or DENY.

The released gateway does not read the cards' mappings yet, so in this demo
the check runs in the console, as a preview, first against the card of the
gateway the prompt went through, then against the other cards. The gateway on
the path does the rest for real. In the product there is no central engine and
nothing routes: each gateway checks its own card and answers for its own
agent. The board keeps that shape: "compare across the fleet" sends the same
prompt through the other gateways and shows what each one actually did next to
the preview of its card.

## The fleet

`console/data/fleet.json` holds six A2A agent cards. Each runs as a stand-in
agent behind its own gateway; the gateway serves the card (interface URL
rewritten to itself, the delegated token extension declared) and the agent
answers with the skill it would have used. The mappings use only (subject
type, action, resource type) triples that exist as policies in the dataset -
a unit test enforces it - with subjects bound to `$token.sub` and resources
literal, empty or `$request.arguments.<name>`:

| Agent | Gateway | Skills | Serves |
| --- | --- | --- | --- |
| Trading Desk Agent | `127.0.0.1:9901` | `quote-lookup`, `core-banking` | Person → Quote `stock_quote` (CAN_RETRIEVE, GET_PRICE); Person → Customer (CAN_LIST_CUSTOMER, CAN_ACCESS_CUSTOMER) |
| Customer Support Agent | `127.0.0.1:9902` | `policy-library`, `customer-360`, `case-decisions` | Person → Folder `cs_policy`, Document; Person → Invoice (CAN_VIEW), Customer (CAN_LIST_CUSTOMER); Person → Decision |
| Billing Agent | `127.0.0.1:9903` | `my-invoices`, `my-accounts` | Person → Invoice; Person → Account, AccountTier (`$request.arguments.tier` for HAS, empty for HAS_TIER), InvestmentCategory, Customer (CAN_QUERY_CUSTOMER) |
| People Directory Agent | `127.0.0.1:9904` | `org-chart` | Person → Person (REPORTS_TO, REPORTS_TO_INDIRECTLY, CAN_QUERY_USER), Department (WORKS_IN, WORKS_UNDER, WORKS_UNDER_DEPARTMENT with `$request.arguments.department`) |
| Workflow Runner Agent | `127.0.0.1:9905` | `run-workflow` | Person → Workflow (CAN_TRIGGER) |
| Weather Agent | `127.0.0.1:9906` | `forecast` | no mappings - passes under the *allow* setting (KBAC alone decides), `missing_coaz_mapping` under *deny* |

Three policy actions are left unmapped on purpose - CAN_DISCLOSE Document,
AFFECTED Account, VIEW_PRODUCT_DOCUMENT Document - so the
"Outside the fleet" gallery shows valid intents no agent may serve.

Who may reach which gateway is in the graph, not in the console: each agent
has a `Workflow` (`wf-<agent>`) that `INVOKES` its `Agent` node, and
`CAN_TRIGGER` edges from the trading and support departments and from the
customers say who may start it. Which skills a person may use is in the
graph too: one `Skill` node per card skill, `HAS` edges from the `Agent`
node with the workflow's name, and the policy `person-can-use-skill`. A
gateway reads that set with one AuthZEN `search/resource` (action `CAN_USE`,
type `Skill`, the chain in `X-IK-Token`) and answers the authenticated
extended card (`GetExtendedAgentCard`) with those skills only, recording
the filtering as a `DISCOVERY` record. One skill is deliberately narrower
than its agent: `case-decisions` hangs on the supervisor's own workflow, so
Vivienne sees three skills on the Support desk and Percival two. The trading desk is for the trading
department, billing for customers, support for both, the weather for
everyone. A persona outside the workflow is refused at admission with 403,
whatever the intent.

## Run with docker compose

The stack uses its own compose project name, image names
(`intent-ibac-console`, `intent-ibac-agent`), Token Service client id
(`intent-ibac`) and host ports (console 3300, Token Service 8302, gateways
9901-9906), so it runs side by side with intent-agent-demo. It shares the
Auth0 tenant and the console port with ibac-demo, so those two do not run at
the same time.

### 1. The Agent Gateway image

The gateways run the Agent Gateway release 2.99 or later from a locally
loaded image; compose never pulls it (`pull_policy: never`). Load the
release and tag it `agent-gateway:latest`, or name it in `.env` as
`AGENT_GATEWAY_IMAGE`. What the demo relies on, all in that release: Token
Service mode, `ibac.enabled`, admission over `$ik_token.act_chain`, the
`INTENT` audit records, the delegated token extension on the card.

### 2. The Auth0 tenant

The same tenant as ibac-demo, whose users are the dataset's people. Three
APIs (Applications → APIs; the identifier is the audience):

| API identifier | Audience of | Why |
| --- | --- | --- |
| `ibac-people` | the people's access tokens | the gateways accept only tokens issued for it; the dataset's `auth0-people` introspect matches on it |
| `ibac-agents` | the gateways' own client-credentials tokens (the bridge adds the audience) | the Token Service matches actor tokens on it |
| `intent_agent` | the token a gateway presents to the Intent Agent | the agent's `EXPECTED_TOKEN_AUDIENCE` |

Applications:

| Application | Type | Settings |
| --- | --- | --- |
| trading-desk, support-desk, billing, people-directory, workflow-runner | Machine to Machine, one each | authorized for the APIs `ibac-agents` **and** `intent_agent`; client id and secret → `<AGENT>_IDP_CLIENT_ID` / `_SECRET` in `.env` |
| Weather | the tenant's existing Machine to Machine application | same authorizations; → `WEATHER_IDP_CLIENT_ID` / `_SECRET` |
| intent-ibac-introspect | Machine to Machine | the Intent Agent's client at the bridge; → `INTENT_INTROSPECT_CLIENT_ID` / `_SECRET` |
| Console | the tenant's existing Regular Web Application | callback `http://localhost:3300/auth/callback`; → `CONSOLE_IDP_CLIENT_ID` / `_SECRET` (used once the console's login moves to Auth0) |

An Auth0 client-credentials token has `sub = <client_id>@clients`: that value
is the agent's identity in the delegation chain, in the gateways' audit
records and in the graph (the Agent nodes' `external_id`, supplied to
instant-stack as `AUTH0_SUB_<AGENT>`).

### 3. Configure and generate the key material

```sh
cp .example.env .env     # fill in AUTH0_DOMAIN and the client ids and secrets above
make images              # console (which carries the generator), stand-in agent, auth0-bridge
make keys
```

`make keys` runs the console's `app.keygen` once and writes two gitignored
files:

- `token-service/token-service.yaml` - from the template, with a fresh
  private RSA signing key for the Token Service, the tenant domain in the
  matchers (from `AUTH0_DOMAIN` in `.env`), a client secret and an audit
  webhook key;
- `audit-config.yaml` - the gateways' audit delivery, with the webhook key.

It prints two secrets, `TOKEN_SERVICE_CLIENT_SECRET` and
`AUDIT_WEBHOOK_API_KEY`: paste them into `.env`. It also prints the Token
Service public key, `TOKEN_SERVICE_PUBLIC_JWK`, for `instant-stack/.env`.
Everything must match the rendered files, which is why the generator refuses
to overwrite them without `--force`.

Then fill in `INDYKITE_BASE_URL` and `GEMINI_API_KEY` in `.env`;
`APP_AGENT_CREDENTIALS_TOKEN` comes from the next step.

### 4. Provision the project (once)

The dataset is [`instant-stack/data/intent-ibac`](../../instant-stack/data/intent-ibac):
the CanBank graph and KBAC policies of `intent` with one identity type:
every login is a `Person` whose `external_id` is an Auth0 subject (the
people of the `ibac` dataset, such as `auth0|69c4975a…`). Staff `WORKS_IN` a
`Department`, customers `IS_CUSTOMER` a `Customer` record (`cust-<name>`),
so being a customer is found on the graph, never in the token; every other
node and relationship of `intent` is kept. On top: six `Agent` nodes, six
`Workflow` nodes with their `INVOKES` and `CAN_TRIGGER` edges, the admission
policy, one `Skill` node per card skill with its `HAS` edges and the
`person-can-use-skill` policy, and two token-introspect configurations (the Auth0 tenant for the
people's access tokens, the Token Service for the delegated tokens, both
typed `Person`). The second needs the Token Service public key, which is
why `make keys` comes first.

1. In `instant-stack/.env` set `DATASET=intent-ibac`, `AUTH0_ISSUER` to
   `https://<tenant>/`, the six `AUTH0_SUB_<AGENT>` to the gateways' client
   subjects (`<client_id>@clients`), and paste `TOKEN_SERVICE_PUBLIC_JWK`
   from `make keys` (keep `SA_TOKEN`, `URL_ENDPOINTS`, `ORGANIZATION_ID` as
   for any other dataset).
2. Start instant-stack and run **Provision everything**. It creates the
   project, the application, the app agent (with `Authorization`,
   `ReadAuthZConfigs`, `ReadDataSchema` and `ContXIQ`), the token introspects, captures
   the nodes and relationships and creates the policies.
3. Copy the app agent credential (`APP_TOKEN` in instant-stack's `.env`)
   into `APP_AGENT_CREDENTIALS_TOKEN` in this demo's `.env`.

Wait a minute after provisioning: the data schema is persisted at most once a
minute and the Intent Agent refuses prompts until it can read it.

### 5. Start

```sh
make up          # docker compose up -d --wait
docker compose ps
```

Everything is published on loopback only (console `127.0.0.1:3300`, Token
Service `:8302`, gateways `:9901`-`:9906`), because the console's API has no
caller authentication and acts with the app agent credential. To present
from another device, set `CONSOLE_BIND=0.0.0.0` in `.env` first.

All services should read `healthy` (the gateways report healthy once their
agent's card is read). Open <http://localhost:3300> and check: the dots at
the bottom of the left rail are green, including the gateways; the **IBAC
fleet** view lists six cards; the **Vocabulary** view lists the Person
policies. Then **log in**: click Harshvardhan Malhotra among the demo's
people (the console sends his email as login hint to Auth0) and sign in with
that user of the tenant. The first card of the **Playground** ("What is the
price of the quote stock_quote?", through the Trading Desk) comes back with
the gateway's answer, a chain of two, an admission AUTHORIZED, one ticket,
and the Trading Desk lit on the board. To ask as someone else, click another
person: the console logs you in again as them. The first
prompt may take a few seconds longer while the agent fetches the schema and
policies.

A gateway's card is one curl away:
`curl -s http://127.0.0.1:9901/.well-known/agent-card.json | jq .`.

To see the shared Redis cache with two Intent Agent instances behind a
balancer, set `COMPOSE_PROFILES=cluster` and
`INTENT_AGENT_ENTRY=intent-balancer` in `.env` and run `make up` again; the
gateways follow `INTENT_AGENT_ENTRY` too.

### 6. Everyday operations

| Need | Command |
| --- | --- |
| Pick up a change to the console (code, static files, `prompts.json`) | `make images` then `docker compose up -d console`, and hard-reload the page (Ctrl+Shift+R) so the browser drops its cached script |
| Pick up a change to `fleet.json` | `docker compose restart` the six `agent-*` and `iag-*` services and the console (the agents mount the file; the gateways cache the card) |
| Pick up a change to `.env` or `iag-base-docker.yaml` | `docker compose up -d` (recreates only the containers whose configuration changed) |
| Switch the gateway image after editing `AGENT_GATEWAY_IMAGE` | `docker compose up -d` |
| Logs | `make logs`, or `docker compose logs --tail 50 iag-trading-desk` |
| Stop | `make down` |

## Run on minikube

The chart deploys the same seventeen workloads on a local Kubernetes cluster
and reuses what the compose run produced: the local images, `.env`, the two
generated files and `fleet.json`. The console is reached through a loopback
port-forward on 3300, the port the tenant's Console application has as
callback, so the login works unchanged. All commands run from
`a2a/intent-ibac-gateway`.

1. Start the cluster and load the local images (the Intent Agent, the Token
   Service and Redis are public on Docker Hub and the cluster pulls them;
   the gateway image is never pulled):

   ```sh
   minikube start
   minikube image load intent-ibac-console:local
   minikube image load intent-ibac-agent:local
   minikube image load intent-ibac-auth0-bridge:local
   minikube image load agent-gateway:latest
   ```

2. Create the namespace, the two Secrets and the fleet ConfigMap. The chart
   reads secrets only from Kubernetes Secrets, never from values. The first
   Secret is `.env` as is. The second holds the two generated files, whose
   audit webhook URL must name the console's *cluster* service, so make
   copies with that one line changed rather than editing the files compose
   uses:

   ```sh
   kubectl create namespace intent-ibac
   kubectl -n intent-ibac create secret generic intent-ibac-secrets --from-env-file=.env
   mkdir -p /tmp/intent-ibac-k8s
   sed 's#http://console:3400#http://intent-ibac-console:3400#' token-service/token-service.yaml > /tmp/intent-ibac-k8s/token-service.yaml
   sed 's#http://console:3400#http://intent-ibac-console:3400#' audit-config.yaml > /tmp/intent-ibac-k8s/audit-config.yaml
   kubectl -n intent-ibac create secret generic intent-ibac-files \
     --from-file=token-service.yaml=/tmp/intent-ibac-k8s/token-service.yaml \
     --from-file=audit-config.yaml=/tmp/intent-ibac-k8s/audit-config.yaml
   kubectl -n intent-ibac create configmap intent-ibac-fleet --from-file=fleet.json=console/data/fleet.json
   rm -r /tmp/intent-ibac-k8s
   ```

   `intent-ibac-console` is `<release name>-console`; change it if you
   install under another release name in step 3.

3. Install the chart, naming your platform region and your tenant:

   ```sh
   helm install intent-ibac chart -n intent-ibac \
     --set platform.indykiteBaseUrl=<your INDYKITE_BASE_URL> \
     --set auth0.domain=<tenant>.eu.auth0.com
   ```

   `helm lint chart --set auth0.domain=x` and `make helm-template` check the
   chart renders; `chart/values.schema.json` rejects unknown or malformed
   values.

4. Watch the pods come up:

   ```sh
   kubectl -n intent-ibac get pods -w
   ```

   Redis, the Token Service and the bridge first, then the Intent Agent, the
   stand-in agents and the console, then the gateways once they have read
   their agent's card. All seventeen should reach `1/1 Running`. If one
   stays in `CrashLoopBackOff`, `kubectl -n intent-ibac logs deploy/<name>`
   shows why; the usual cause is a missing key in one of the Secrets.

5. Open the console through the loopback port-forward (the Service is a
   ClusterIP on purpose) and log in:

   ```sh
   kubectl -n intent-ibac port-forward svc/intent-ibac-console 3300:3400
   ```

   Open <http://localhost:3300/> (stop the compose console first if it holds
   the port) and check the same things as for compose. To expose the console
   on the nodes deliberately, install with `--set console.service.type=NodePort`
   and set `console.publicUrl` to the URL the browser will use; that URL must
   also be an allowed callback of the Console application.

6. Tear down when done:

   ```sh
   helm uninstall intent-ibac -n intent-ibac
   kubectl delete namespace intent-ibac
   minikube stop
   ```

After re-creating a Secret or the ConfigMap, bump `secrets.revision` on the
next `helm upgrade` (`--set-string secrets.revision=2`): every pod that
reads them rolls and picks up the new contents.

## How the tokens work

Two issuers take part, and every token is a JWT:

1. **Auth0** (`iss https://<tenant>/`). It issues the person's access token
   (`aud ibac-people`; every login is a Person) and each gateway's own
   client-credentials token (`sub <client_id>@clients`, `aud ibac-agents`,
   or `intent_agent` for the call to the Intent Agent). Two facts shape the
   wiring: the gateway validates the caller's token only by RFC 7662
   introspection, under the same base URL as the client-credentials endpoint
   it gets its own token from, and Auth0 publishes no introspection endpoint
   and needs an `audience` the gateway does not send. So the gateways'
   identity provider is the `auth0-bridge` service: `POST /oauth/introspect`
   verifies the JWT against the tenant JWKS and answers the RFC 7662 shape,
   `POST /oauth/token` forwards to Auth0 adding `audience=ibac-agents`.
   Callers of the bridge authenticate with their own M2M credentials, which
   the bridge verifies at Auth0; no client list lives in it.
2. **The IndyKite Token Service** (`iss https://token-service.intent-ibac.local`).
   Each gateway exchanges the person's token (subject) and its own token
   (actor) for a delegated token with `aud intent-ibac-agents`: `sub` = the
   person, `act.sub` = the gateway's client subject. The Token Service
   verifies both incoming Auth0 tokens against the tenant JWKS; the platform
   validates the delegated token through the `token-service-intent-ibac`
   introspect, which is how `$ik_token.act_chain` reaches the admission
   policy. The exchange also accepts an `intent` parameter, a JSON list of
   evaluations (at most 10), which the Token Service writes as
   `act.intent.evaluations` of that hop; the released gateway does not send
   it yet, and the token card says so until it does.

The platform reads the person's token in whoami through the `auth0-people`
introspect, against the tenant's keys.

The Intent Agent authenticates its **caller**, the gateway, by introspecting
the gateway's token at the bridge (`INTROSPECT_URL`, with its own M2M client
`INTENT_INTROSPECT_CLIENT_ID`) and requires `aud intent_agent`. The
end user is **not** identified by that token: the gateway states
`subject_type` and `subject_id` in `message.metadata` from whoami, and the
agent binds every evaluation to that subject, so prompt text can never
impersonate anyone.

**The console's own login.** The console is the tenant's "Console" Regular
Web Application: authorization code with PKCE, scope `openid profile email`,
audience `ibac-people`. The access token Auth0 answers is exactly what the
gateways accept, so the console sends it as it is; the id token gives the
name shown in the header. Both live in a server-side session behind an
HttpOnly cookie, never in the page's scripts. Clicking one of the demo's
people starts a new login with that person's email as `login_hint`.

The three token guardrails in the console are the failures the gateway
catches at introspection: the id token in place of the access token (a real
token of the tenant, but issued for the console and not for the people's
API: 401, not issued for this agent), the access token with its signature
broken (401, inactive at the bridge), and no token (401, missing bearer).

## What the decision step can and cannot show

The gateway makes one decision, admission: may this caller reach this agent
through this chain. Neither the Intent Agent nor the mapping preview makes
one; the console adds a decision step so the intent is seen to mean
something. Each evaluation of the intent, and each request the preview
generated, is mapped to the AuthZEN endpoint the platform has today:

| Evaluation shape | Call |
| --- | --- |
| subject id, action, resource id | `POST /access/v1/evaluations` → ALLOW / DENY |
| subject type only (Subject Search) | `POST /access/v1/search/subject` → who |
| resource type only (Resource Search) | `POST /access/v1/search/resource` → which |
| no action (Action Search) | `POST /access/v1/search/action` → what |

`context.parameters` are sent as `input_params` only when the granting
policy's Cypher declares them (`$department`, `$system`, `$tier` in this
dataset). The AuthZEN API reads `input_params` and `policy_tags` and silently
ignores anything else, so `filters` (CEL), `qualifiers` and undeclared
parameters are shown as **carried, not enforced**.

## Development

```sh
make test     # console unit tests: preview, fleet, gateway client, decision mapper, issuer, audit, A2A parsing (the bridge has its own: cd auth0_bridge && pytest)
make lint     # ruff, inheriting the repository's rules via ruff.toml
```

Conventions: Python 3.13, FastAPI, a validated `Settings` object that fails at
startup on a blank secret, no SDK between the console and the gateways (the
JSON on the wire is the point), non-root images with healthchecks, nothing
internal in the tree. Prompts and personas are data (`console/data/prompts.json`),
the fleet is data (`console/data/fleet.json`); the personas are identities
of the dataset, nobody invented: Persons with Auth0 subjects, some of them
holding a `cust-<name>` record, as in the `ibac` dataset. Adding an agent to the fleet
takes a card in `fleet.json`, an `agent-<id>` / `iag-<id>` pair in the
compose file with a new port, a Machine-to-Machine application in the tenant
(its client subject as `AUTH0_SUB_<AGENT>`), and an `Agent` node with its
workflow in the dataset.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| A gateway stays `unhealthy` or restarts | Its agent's card could not be read, or `audit-config.yaml` is missing: run `make keys` (it renders the file), then `docker compose up -d`. |
| The login ends on "Auth0 answered an opaque access token" | The Console application could not issue a token for `ibac-people`: the API does not exist in the tenant, or its identifier differs from `AUTH0_PEOPLE_AUDIENCE`. |
| The login ends on "Auth0 refused the code" or a callback mismatch | `CONSOLE_PUBLIC_URL` (default `http://localhost:3300`) plus `/auth/callback` is not among the Console application's allowed callback URLs, or the client secret is wrong. |
| Every prompt is 401 "not issued for this agent" | The token's audience is not the gateway's `PROTECTED_AGENT_AUTHENTICATION_AUDIENCES` (`ibac-people`): the console requested another API, or the tenant answered an opaque token because no audience was requested. |
| The bridge answers `401 invalid_client` | A gateway's or the Intent Agent's M2M client id or secret is wrong, or that application is not authorized for `ibac-agents` and `intent_agent` in the tenant. |
| Every prompt is 403 at admission | whoami did not resolve the person (`auth0-people` introspect missing or on another issuer), or the Agent nodes carry other subjects than the gateways' `<client_id>@clients`: re-provision. A person outside the agent's workflow is 403 by design. |
| Admission is NOT_AUTHORIZED for a persona inside the workflow | The platform could not read `X-IK-Token`: the `token-service-intent-ibac` introspect carries another public key than `token-service.yaml`. Re-provision with the `TOKEN_SERVICE_PUBLIC_JWK` printed by the same `make keys` run. |
| Exchange fails `401 invalid_client` | `TOKEN_SERVICE_CLIENT_SECRET` in `.env` differs from `idp.client_auth.client_secret` in `token-service.yaml`. |
| The Intent Agent answers 401 to the gateway | The agent's introspection client (`INTENT_INTROSPECT_CLIENT_*`) is refused by the bridge, or the gateway's M2M application is not authorized for the `intent_agent` API. |
| Every prompt is refused with "Access policies ... unavailable" | No ACTIVE policy for the persona's subject type: the app agent lacks `ReadAuthZConfigs`, or the `intent-ibac` dataset is not provisioned in the project the credential belongs to. Check the Vocabulary view. |
| The console does not start, log names `fleet agent '...'` | A card in `fleet.json` has a mapping the preview refuses (missing subject or resource, a type that is not a node type, an action without a name) or two skills share an id. |
| The board says NOT_AUTHORIZED everywhere for a prompt that should route | The intent's action or resource type is not in any mapping - compare the ticket with the IBAC fleet page; the "Outside the fleet" gallery does this on purpose. |
| A generated request shows `platform error ... 422` | The policy behind it declares a `$name` the intent did not carry (for example `$system` for CAN_ACCESS_CUSTOMER); rephrase the prompt to name it. |
| No gateway record next to the prompt | `audit-config.yaml` was rendered with another key than `AUDIT_WEBHOOK_API_KEY` in `.env`; the Audit view still shows the other sources. |
| Port 3300, 8302 or 9901-9906 already in use | Another copy of this stack, or ibac-demo on 3300; stop it, or set `CONSOLE_PORT`, `TOKEN_SERVICE_HOST_PORT` or `IAG_<AGENT>_PORT` in `.env`. |
