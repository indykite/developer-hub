# intent-ibac-demo

A visual, end-to-end showcase of two things working together:

- the **IndyKite Intent Agent**, the A2A service that turns a natural-language
  prompt into an AuthZEN-style intent - a list of
  `{subject, action, resource, context}` evaluations constrained to the
  vocabulary of your IndyKite Knowledge Graph and the caller's active KBAC
  policies;
- the **IBAC engine** (Intent-Based Access Control), which compares that
  intent with what an agent *declares* it can do - the `x-coaz-mappings`
  published on the skills of its agent card - to decide which agent and skill
  may serve the prompt, and which exact AuthZEN request to check before it is
  forwarded.

The demo is a copy of [intent-agent-demo](../intent-agent-demo) with the
engine added. The Intent Agent runs as the published image; the engine is the
console's own implementation of the rules the IndyKite Agent Gateway applies
in front of an agent (same reason codes, same audit record fields), run over a
**fleet** of six agent cards at once so the routing can be watched. Everything
runs locally with docker compose or on minikube; only the IndyKite platform
(schema, policies, decisions) and Google Gemini (the agent's extraction model)
are remote.

```text
console ──mint persona + actor JWT──▶ token-service ──▶ delegation token (aud intent_agent)
console ──A2A SendMessage · Bearer delegation──▶ intent-agent ──introspect──▶ token-service
                                                   │ data schema + policies ──▶ IndyKite platform
                                                   │ extraction ──▶ Gemini
                                                   └ audit record ──▶ console webhook
console ──intent──▶ IBAC engine ──▶ discovery over every card of the fleet
                                 ──▶ usage on every matched skill → generated AuthZEN requests
console ──AuthZEN evaluations / search──▶ IndyKite platform   (the intent's evaluations and the generated requests)
```

What the console shows, live:

- **Playground** - pick an identity, send a prompt, watch the ten-step
  pipeline (seven for the Intent Agent, two for the engine, one for the audit),
  read the evaluations as intent tickets with the platform's decision, then
  the **routing board**: every agent of the fleet, AUTHORIZED or
  NOT_AUTHORIZED with its reason code, the matched skills lit, and for each
  matched skill the usage result - the generated requests with their ALLOW /
  DENY, or the search match - plus the values the `$references` resolved
  from. Click any skill, matched or not, to run usage on it.
- **Guardrails** - ambiguous and out-of-scope prompts, prompt injection,
  encoded payloads, SQL in filters, and three token failures. Nothing refused
  ever reaches the engine.
- **How IBAC works** - the engine explained in five steps with the demo's
  own examples: the two sides, a mapping field by field, discovery as three
  yes/no questions, usage with the references resolved, and where the engine
  stops. On the board, every card carries a one-line verdict in plain words
  and every skill a *why?* fold that answers the three questions.
- **IBAC fleet** - the six agent cards with every skill's mappings, and the three
  rules of the engine.
- **Vocabulary** - the data schema and the ACTIVE policies per subject type:
  everything the agent is allowed to say, and the words the mappings use.
- **Audit** - every record the Intent Agent and the Token Service delivered
  to the console's webhook, and one record per engine call (`IBAC_DISCOVERY`
  per card, `IBAC_USAGE` per matched skill), streamed as they arrive.
- **Intent Agent fleet** (the Intent Agent, the Token Service and the console as they present themselves), **Ontology** (the schema graph), **Architecture** - as in intent-agent-demo, the
  architecture page extended with the engine.

Deep links replay a beat: `/?persona=roy&prompt=...&send=1`, `/?view=fleet`.

## Contents

- [Folders](#folders)
- [IBAC in five steps](#ibac-in-five-steps)
- [How IBAC works here](#how-ibac-works-here)
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
| `console/` | The showcase (FastAPI + static UI): demo issuer, Token Service client, raw A2A client, **IBAC engine** (`app/ibac.py`), **fleet** (`app/fleet.py`, `data/fleet.json`), AuthZEN decision mapper, audit webhook and live stream. Tests in `console/tests`. |
| `token-service/` | Template for the IndyKite Token Service configuration; `make keys` renders it. |
| `nginx/` | Balancer config for the optional `cluster` profile. |
| `chart/` | Helm chart: console, token-service, intent-agent, redis. |
| `docker-compose.yaml`, `makefile`, `.example.env` | The compose stack and its knobs. |

The Intent Agent itself is not vendored: it is the published
[`indykite/intent-agent`](https://hub.docker.com/r/indykite/intent-agent)
image from Docker Hub (source: [github.com/indykite/intent-agent](https://github.com/indykite/intent-agent)).
The graph and policies it works against are the `instant-stack` dataset
`data/intent`, unchanged from intent-agent-demo.

## IBAC in five steps

For a first reading, before the details. The console has the same five steps
as a page (**How IBAC works**, item 07 in the rail), with the examples drawn.

1. **Two sides of one question.** The Intent Agent has turned the prompt
   into an *intent*: evaluations of the form subject → action → resource,
   in the words of the graph's policies ("User roy → GET_PRICE → Quote
   stock_quote"). Every agent publishes an *agent card* with its skills.
   IBAC adds one thing to each skill: a declaration of which subjects may do
   which actions on which resources through it. The engine never reads the
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
   AUTHORIZED; none makes it NOT_AUTHORIZED (`intent_mismatch`); a card
   without mappings answers `missing_coaz_mapping` and can never match.
4. **Usage: what exactly must be checked?** For the matched skill the ids
   are resolved: `$token.sub` from the delegation token, `$parameters.tier`
   from what the Intent Agent extracted. One AuthZEN request per (subject,
   resource) pair: "Customer bob → HAS → AccountTier tier_1"
   (`intent_match_authzen_required`). An id left empty is a *search match*
   (`intent_match_search`): nothing is generated, the intent's own evaluation
   decides. A reference that does not resolve drops the entity and the
   mapping does not match.
5. **Where the engine stops.** It answers whether the agent declares the
   skill the intent needs and which permission that comes down to. It makes
   no decision: the generated requests go to the platform's AuthZEN API,
   which answers ALLOW or DENY. Every call is audited, `IBAC_DISCOVERY` per
   card and `IBAC_USAGE` per confirmed skill.

## How IBAC works here

An agent card can carry, on each skill, a list of **coarse-authorization
mappings** under the key `x-coaz-mappings` (an MCP tool, resource or prompt
carries them in its `_meta`). A mapping names subject types, resource types
and the actions the skill serves; an id is a literal, empty, or a
`$reference`:

```json
{
  "id": "quote-lookup",
  "name": "Quote lookup",
  "x-coaz-mappings": [
    {
      "subject": [{"type": "User", "id": "$token.sub"}],
      "resource": [{"type": "Quote", "id": "stock_quote"}],
      "action": [{"name": "CAN_RETRIEVE"}, {"name": "GET_PRICE"}]
    }
  ]
}
```

The engine has two flows, and the console runs both for every prompt that
yields an intent:

1. **Discovery** - the intent against every skill of a card. A skill matches
   an evaluation when one of its mappings agrees on the **subject type**, the
   **resource type** and the **action** (a mapping without actions allows
   every action); ids are not compared. One match anywhere makes the decision
   positive (`intent_match_search`); none makes it `intent_mismatch`; a card
   without any mapping is `missing_coaz_mapping`. The response lists, for
   each evaluation, the skills that matched it, in card order.
2. **Usage** - the intent against the one skill discovery picked. The
   matching mapping's ids are resolved: a literal stays, `$token.sub` is read
   from the delegation token's claims, `$parameters.<name>` from the
   `context.parameters` of the intent, dotted paths walk nested maps. One
   AuthZEN request is built per (subject, resource) pair with the
   evaluation's action and `context.input_params` set to the whole params
   map: `intent_match_authzen_required`. An id that stays empty means a
   **search** match, `intent_match_search`: nothing is generated and the
   intent's own evaluation (shown above the board) is what the platform
   decides. A reference that does not resolve drops the entity, and the
   mapping does not match.

The console then sends the generated requests to the platform through the
same mapper as the intent's evaluations (declared `$name` parameters become
`input_params`, the rest is carried) and shows ALLOW / DENY next to each.
Note what the engine does with a **second subject**: the subject of a
generated request comes from the mapping (`$token.sub`, the caller), not from
the evaluation, so "does ted have a tier_3 account?" asked by rebecca
generates `rebecca HAS AccountTier tier_3`; the board says so.

Every engine call writes one audit record in the gateway's shape:
`IBAC_DISCOVERY` with `capabilityName`, `skillIDs` and the `matchedSkills`
per evaluation; `IBAC_USAGE` with `capabilityName`, `skillID` and
`reasonCode`; both with the prompt, the subject and the actor chain.

In production the engine runs inside the IndyKite Agent Gateway in front of
each agent and reads that agent's own card, so one gateway sees one card.
The console runs the same rules over six cards so that one prompt shows which
agents would let it through. The engine makes no authorization decision: it
answers whether an agent declares the skill the intent needs, and names the
check; the platform decides.

## The fleet

`console/data/fleet.json` holds six A2A agent cards; none of them runs. Their
mappings use only (subject type, action, resource type) triples that exist as
policies in the `intent` dataset - a unit test enforces it - with subjects
bound to `$token.sub` and resources literal, empty or `$parameters.<name>`:

| Agent | Skills | Serves |
| --- | --- | --- |
| Trading Desk Agent | `quote-lookup`, `core-banking` | User → Quote `stock_quote` (CAN_RETRIEVE, GET_PRICE); User → Customer (CAN_LIST_CUSTOMER, CAN_ACCESS_CUSTOMER) |
| Customer Support Agent | `policy-library`, `customer-360`, `case-decisions` | User → Folder `cs_policy`, Document; User → Invoice (CAN_VIEW), Customer (CAN_LIST_CUSTOMER); Customer → Decision |
| Billing Agent | `my-invoices`, `my-accounts` | Customer → Invoice; Customer → Account, AccountTier (`$parameters.tier` for HAS, empty for HAS_TIER), InvestmentCategory, Customer (CAN_QUERY_CUSTOMER) |
| People Directory Agent | `org-chart` | User → User (REPORTS_TO, REPORTS_TO_INDIRECTLY, CAN_QUERY_USER), Department (WORKS_IN, WORKS_UNDER, WORKS_UNDER_DEPARTMENT with `$parameters.department`) |
| Workflow Runner Agent | `run-workflow` | User → Workflow (CAN_TRIGGER) |
| Weather Agent | `forecast` | no mappings - `missing_coaz_mapping` for every intent |

Three policy actions are left unmapped on purpose - User CAN_DISCLOSE
Document, User AFFECTED Account, Customer VIEW_PRODUCT_DOCUMENT - so the
"Outside the fleet" gallery shows valid intents no agent may serve.

## Run with docker compose

The stack uses its own compose project name, image name (`intent-ibac-console`),
Token Service client id (`intent-ibac`) and host ports (console 3400, Token
Service 8302), so it runs side by side with intent-agent-demo.

### 1. Provision the project (once)

Same project as intent-agent-demo: the dataset in
[`instant-stack/data/intent`](../../instant-stack/data/intent), the CanBank
graph plus 24 KBAC policies. If that project already exists, reuse its app
agent credential and skip to step 2.

1. In `instant-stack/.env` set `DATASET=intent` (keep `SA_TOKEN`,
   `URL_ENDPOINTS`, `ORGANIZATION_ID` as for any other dataset).
2. Start instant-stack and run **Provision everything**. It creates the
   project, the application, the app agent (with `ReadAuthZConfigs` and
   `ReadDataSchema`, which the Intent Agent needs), captures the nodes and
   relationships and creates the 24 policies.
3. Copy the app agent credential (`APP_TOKEN` in instant-stack's `.env`) - it
   becomes `APP_AGENT_CREDENTIALS_TOKEN` below.

Wait a minute after provisioning: the data schema is persisted at most once a
minute and the Intent Agent refuses prompts until it can read it.

### 2. The Intent Agent image

The stack runs the published image, `indykite/intent-agent:1.1.0` from
Docker Hub, pinned by digest in `docker-compose.yaml` and in the chart.
Nothing to do: `make up` pulls it. To test an unreleased agent instead, build
it from a clean checkout of the public repo and name it in `.env`:

```sh
make intent-agent-image INTENT_AGENT_SRC=/path/to/intent-agent INTENT_AGENT_IMAGE=intent-agent:<version>
```

then set `INTENT_AGENT_IMAGE=intent-agent:<version>` in `.env` (step 3).

### 3. Configure

```sh
cp .example.env .env
```

Fill in `INDYKITE_BASE_URL`, `APP_AGENT_CREDENTIALS_TOKEN` and
`GEMINI_API_KEY`. Leave `INTENT_AGENT_IMAGE` on the published image unless
step 2 built a local one, and leave the two generated values for the next
step.

### 4. Generate the key material

```sh
make images   # builds the console image, which carries the generator
make keys
```

`make keys` runs the console's `app.keygen` once and writes two gitignored
files:

- `token-service/token-service.yaml` - from
  `token-service/token-service.template.yaml`, with a fresh private RSA
  signing key for the Token Service, the demo issuer's public key, a client
  secret and an audit webhook key;
- `console/keys/demo-idp.key` - the demo issuer's private key.

It prints `TOKEN_SERVICE_CLIENT_SECRET=...` and `AUDIT_WEBHOOK_API_KEY=...`:
paste both into `.env`. They must match the rendered config, which is why the
generator refuses to overwrite existing files without `--force`.

### 5. Start

```sh
make up          # docker compose up -d --wait
docker compose ps
```

The console and the Token Service are published on loopback only
(`127.0.0.1:3400`, `127.0.0.1:8302`), because the console's API has no caller
authentication and acts with the app agent credential. To present from another
device on the network, set `CONSOLE_BIND=0.0.0.0` in `.env` first.

All four services should read `healthy`. Open <http://localhost:3400> and
check four things: the dots at the bottom of the left rail are green, the
**IBAC fleet** view lists six cards, the **Vocabulary** view lists policies for
both User and Customer, and the first card of the **Playground** ("What is
the price of the quote stock_quote?" as Roy) comes back with one ticket, the
Trading Desk Agent lit on the board and a generated request decided ALLOW.
The first prompt may take a few seconds longer while the agent fetches the
schema and policies.

To see the shared Redis cache with two agent instances behind a balancer, set
`COMPOSE_PROFILES=cluster` and `INTENT_AGENT_ENTRY=intent-balancer` in `.env`
and run `make up` again.

### 6. Everyday operations

| Need | Command |
| --- | --- |
| Pick up a change to the console (code, static files, `fleet.json`, `prompts.json`) | `make images` then `docker compose up -d console`, and hard-reload the page (Ctrl+Shift+R) so the browser drops its cached script |
| Pick up a change to `.env` (image name, secrets) | `docker compose up -d` (recreates only the containers whose configuration changed) |
| Restart the console without changes | `docker compose restart console` |
| Switch the agent image after editing `INTENT_AGENT_IMAGE` | `docker compose up -d intent-agent`, then remove old images with `docker rmi <name:tag>` once nothing uses them |
| Logs | `make logs`, or `docker compose logs --tail 50 intent-agent` |
| Stop | `make down` |

## Run on minikube

The chart deploys the same four workloads on a local Kubernetes cluster. It
reuses what the compose run already produced: the local image, `.env`, and
the two generated files. All commands run from `a2a/intent-ibac-demo`.

1. Start the cluster:

   ```sh
   minikube start
   ```

2. Load the console image (the Intent Agent, the Token Service and Redis are
   public on Docker Hub and the cluster pulls them itself):

   ```sh
   minikube image load intent-ibac-console:local
   ```

   If you are testing a local agent build, load it too
   (`minikube image load intent-agent:<tag>`) and pass its name to
   `helm install` in step 4 with `--set intentAgent.image.repository=intent-agent --set intentAgent.image.tag=<tag>`.

3. Create the namespace and the two Secrets. The chart reads secrets only from
   Kubernetes Secrets, never from values. The first is `.env` as is. The
   second holds the two generated files, but the Token Service config must
   point its audit webhook at the console's *cluster* service name, so make a
   copy with that one line changed rather than editing the file compose uses:

   ```sh
   kubectl create namespace intent-ibac
   kubectl -n intent-ibac create secret generic intent-ibac-secrets --from-env-file=.env
   sed 's#http://console:3400#http://intent-ibac-console:3400#' token-service/token-service.yaml > /tmp/token-service-k8s.yaml
   kubectl -n intent-ibac create secret generic intent-ibac-files \
     --from-file=token-service.yaml=/tmp/token-service-k8s.yaml \
     --from-file=demo-idp.key=console/keys/demo-idp.key
   rm /tmp/token-service-k8s.yaml
   ```

   `intent-ibac-console` is `<release name>-console`; change it if you install
   under another release name in step 4.

4. Install the chart, naming your platform region:

   ```sh
   helm install intent-ibac chart -n intent-ibac \
     --set platform.indykiteBaseUrl=<your INDYKITE_BASE_URL>
   ```

   `helm lint chart` and `make helm-template` check the chart renders;
   `chart/values.schema.json` rejects unknown or malformed values.

5. Watch the pods come up:

   ```sh
   kubectl -n intent-ibac get pods -w
   ```

   Redis and the Token Service first, then the agent (its readiness probe
   needs the Token Service), then the console. All four should reach
   `1/1 Running`. If one stays in `CrashLoopBackOff`,
   `kubectl -n intent-ibac logs deploy/intent-ibac-<name>` shows why; the
   usual cause is a missing key in one of the Secrets.

6. Open the console through a loopback port-forward (the Service is a
   ClusterIP on purpose):

   ```sh
   kubectl -n intent-ibac port-forward svc/intent-ibac-console 3400:3400
   ```

   Open <http://127.0.0.1:3400/> (stop the compose console first if it holds
   the port, or forward to another local port) and check the same four things
   as for compose. To expose the console on the nodes deliberately, install
   with `--set console.service.type=NodePort` and open
   `minikube service intent-ibac-console -n intent-ibac --url`.

7. Tear down when done:

   ```sh
   helm uninstall intent-ibac -n intent-ibac
   kubectl delete namespace intent-ibac
   minikube stop
   ```

After re-creating either Secret, bump `secrets.revision` on the next
`helm upgrade` (`--set-string secrets.revision=2`): the console, the Token
Service and the agent pods roll together and pick up the new contents.

## How the tokens work

The Intent Agent authenticates its **caller** - the client sending the A2A
message, which in production is the IndyKite Agent Gateway - through RFC 7662
token introspection. The end user is **not** identified by that token: the
caller states `subject_type` and `subject_id` in `message.metadata`, and the
agent binds every evaluation to that subject, so prompt text can never
impersonate anyone.

This demo keeps that split and uses the **IndyKite Token Service** as the
issuer and the introspection endpoint:

1. The console's built-in demo issuer signs two RS256 JWTs: the persona's
   subject token (`iss https://demo-idp.intent-ibac.local`,
   `aud intent-ibac-console`) and the console's own actor token
   (`aud intent_agent`). In production an IdP issues both.
2. The console exchanges them at the Token Service (`POST /oauth2/token`,
   RFC 8693, `audience=intent_agent`, HTTP Basic `intent-ibac:<secret>`). The
   result is a delegation token: `sub` = the persona, `act.sub` = the console,
   `aud` = `intent_agent`, 5 minutes to live.
3. The console sends the A2A `SendMessage` with that delegation token as
   Bearer. The agent introspects it at the Token Service
   (`POST /oauth2/introspect`, same client pair) and requires `aud` to
   contain `intent_agent` (`EXPECTED_TOKEN_AUDIENCE`).

The same delegation token's claims are what the IBAC engine resolves
`$token.<claim>` from, so `$token.sub` in a mapping is the persona - the
subject a gateway would also have bound.

The three token guardrails in the console are exactly the failures this
design catches: exchanging for another audience (401 "not valid for this
agent"), presenting the raw subject token the Token Service never issued
(401 "not active"), and sending no token (401 "Authorization required").

## What the decision step can and cannot show

Neither the agent nor the engine makes an authorization decision; the console
adds one so the intent is seen to mean something. Each evaluation of the
intent, and each request the engine generated, is mapped to the AuthZEN
endpoint the platform has today:

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
parameters are shown as **carried, not enforced**. The engine puts its whole
params map (token claims, subject, parameters) into `context.input_params` of
a generated request; the console forwards the declared scalars, as it does
for the intent, and says so under each request.

## Development

```sh
make test     # console unit tests: engine (ported case by case from the gateway's), fleet, decision mapper, issuer, audit, A2A parsing
make lint     # ruff, inheriting the repository's rules via ruff.toml
```

Conventions: Python 3.13, FastAPI, a validated `Settings` object that fails at
startup on a blank secret, no SDK between the console and the agent (the JSON
on the wire is the point), non-root images with healthchecks, nothing
internal in the tree. Prompts and personas are data (`console/data/prompts.json`),
the fleet is data (`console/data/fleet.json`); the personas are the CanBank
identities from the dataset, nobody invented. Add an agent to the fleet by
adding a card to `fleet.json`: the loader validates every mapping at startup,
and the test suite checks each one is a policy triple of the dataset.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `token-service` restarts, log says a configuration is invalid | `token-service.yaml` still has a placeholder - run `make keys` (or `--force` after deleting the two generated files). |
| Exchange fails `401 invalid_client` | `TOKEN_SERVICE_CLIENT_SECRET` in `.env` differs from `idp.client_auth.client_secret` in `token-service.yaml`. |
| Exchange fails `400 invalid_grant` | The Token Service could not verify the demo issuer's JWT: `console/keys/demo-idp.key` and the public key in `token-service.yaml` are from different `make keys` runs. |
| Agent answers 401 for the delegation token | `EXPECTED_TOKEN_AUDIENCE` and the exchanged `audience` differ, or the agent's `INTROSPECT_CLIENT_*` pair is wrong (then it is 502 "rejected our own client credentials"). |
| Every prompt is refused with "Access policies ... unavailable" | No ACTIVE policy for the persona's subject type: the app agent lacks `ReadAuthZConfigs`, or the `intent` dataset is not provisioned in the project the credential belongs to. Check the Vocabulary view. |
| The console does not start, log names `fleet agent '...'` | A card in `fleet.json` has a mapping the engine refuses (missing subject or resource, a type that is not a node type, an action without a name) or two skills share an id. |
| The board says NOT_AUTHORIZED everywhere for a prompt that should route | The intent's action or resource type is not in any mapping - compare the ticket with the IBAC fleet page; the "Outside the fleet" gallery does this on purpose. |
| A generated request shows `platform error ... 422` | The policy behind it declares a `$name` the intent did not carry (for example `$system` for CAN_ACCESS_CUSTOMER); rephrase the prompt to name it. |
| No audit record next to the prompt | The webhook key differs between `.env` and `token-service.yaml`, or `AUDIT_ASYNC` delivery is slower than 5 s; the Audit view still shows it when it lands. |
| Port 3400 or 8302 already in use | Another copy of this stack; set `CONSOLE_PORT` / `TOKEN_SERVICE_HOST_PORT` in `.env`. |
