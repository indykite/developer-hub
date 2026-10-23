# intent-agent-demo

A visual, end-to-end showcase of the **IndyKite Intent Agent**: the A2A service
that turns a natural-language prompt into an AuthZEN-style intent - a list of
`{subject, action, resource, context}` evaluations an authorization engine can
decide on - constrained to the vocabulary of your IndyKite Knowledge Graph
and the caller's active KBAC policies.

The demo runs everything locally with docker compose or on minikube with the
Helm chart in `chart/`. Only the IndyKite platform (schema, policies,
decisions) and Google Gemini (the agent's extraction model) are remote.

```text
console ──mint persona + actor JWT──▶ token-service ──▶ delegation token (aud intent_agent)
console ──A2A SendMessage · Bearer delegation──▶ intent-agent ──introspect──▶ token-service
                                                   │ data schema + policies ──▶ IndyKite platform
                                                   │ extraction ──▶ Gemini
                                                   └ audit record ──▶ console webhook
console ──AuthZEN evaluations / search──▶ IndyKite platform   (the decision step)
```

What the console shows, live:

- **Playground** - pick an identity (users and customers of the CanBank
  graph), send a prompt, watch the pipeline light up, read the evaluations as
  "intent tickets" (subject → action → resource, filters, parameters,
  qualifiers, search and second-subject markers), see the platform's decision
  per ticket, and open the exact JSON-RPC request, task envelope, tokens and
  audit record.
- **Guardrails** - ambiguous and out-of-scope prompts, prompt injection,
  encoded payloads, SQL in filters, and three token failures (wrong audience,
  non-delegation token, no token).
- **Vocabulary** - the data schema and the ACTIVE policies per subject type:
  literally everything the agent is allowed to say.
- **Audit** - every record the Intent Agent and the Token Service delivered to
  the console's webhook, streamed as it arrives.
- **Architecture** - the diagram and the eight numbered steps.

## Contents

- [Folders](#folders)
- [Run with docker compose](#run-with-docker-compose)
- [Run on minikube](#run-on-minikube)
- [How the tokens work](#how-the-tokens-work)
- [What the decision step can and cannot show](#what-the-decision-step-can-and-cannot-show)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Phase 2: behind the Agent Gateway](#phase-2-behind-the-agent-gateway)

## Folders

| Path | What it is |
| --- | --- |
| `console/` | The showcase (FastAPI + static UI): demo issuer, Token Service client, raw A2A client, AuthZEN decision mapper, audit webhook and live stream. Tests in `console/tests`. |
| `token-service/` | Template for the IndyKite Token Service configuration; `make keys` renders it. |
| `nginx/` | Balancer config for the optional `cluster` profile. |
| `chart/` | Helm chart: console, token-service, intent-agent, redis. |
| `docker-compose.yaml`, `makefile`, `.example.env` | The compose stack and its knobs. |

The Intent Agent itself is not vendored: it is the public
[`indykite/intent-agent`](https://github.com/indykite/intent-agent) image,
built locally until the Docker Hub tag is published. The graph and policies it
works against are the `instant-stack` dataset `data/intent`.

## Run with docker compose

### 1. Provision the project (once)

The agent's whole vocabulary comes from the platform, so a project with the
right schema and policies must exist first. The dataset lives in
[`instant-stack/data/intent`](../../instant-stack/data/intent): the CanBank
graph (users, departments, customers, accounts, invoices, documents,
workflows) plus 24 KBAC policies chosen so that every intent pattern the agent
supports has a policy to land on.

1. In `instant-stack/.env` set `DATASET=intent` (keep `SA_TOKEN`,
   `URL_ENDPOINTS`, `ORGANIZATION_ID` as for any other dataset).
2. Start instant-stack and run **Provision everything**. It creates the
   project, the application, the app agent (with `ReadAuthZConfigs` and
   `ReadDataSchema`, which the Intent Agent needs), captures the nodes and
   relationships and creates the 24 policies. No token introspect, MCP server
   or CIQ query is provisioned: the Intent Agent does not use them.
3. Copy the app agent credential (`APP_TOKEN` in instant-stack's `.env`) - it
   becomes `APP_AGENT_CREDENTIALS_TOKEN` below.

Wait a minute after provisioning: the data schema is persisted at most once a
minute and the Intent Agent refuses prompts until it can read it.

### 2. Get the Intent Agent image

The demo runs whatever image `INTENT_AGENT_IMAGE` in `.env` names. Until the
image is on Docker Hub, use one of these:

- **From a registry you have access to** (the build an existing environment
  runs). Pull it, then give it a short local name you can type:

  ```sh
  docker pull <registry>/intent-agent:<tag>
  docker tag <registry>/intent-agent:<tag> intent-agent:<tag>
  ```

  and set `INTENT_AGENT_IMAGE=intent-agent:<tag>` in `.env` (step 3). If you
  pulled by digest the tag column shows `<none>`: tag it by image ID instead,
  `docker tag <image id> intent-agent:<tag>`. If a pull answers "unsupported
  media type application/vnd.cncf.helm.config.v1+json", that reference is the
  Helm chart artifact, not the container image.

- **From the public source** (a clean checkout of a release tag, never your
  working copy):

  ```sh
  make intent-agent-image INTENT_AGENT_SRC=/path/to/intent-agent INTENT_AGENT_IMAGE=intent-agent:<version>
  ```

When the published image is available, set
`INTENT_AGENT_IMAGE=indykite/intent-agent:<version>` and skip this step.

### 3. Configure

```sh
cp .example.env .env
```

Fill in `INDYKITE_BASE_URL`, `APP_AGENT_CREDENTIALS_TOKEN`, `GEMINI_API_KEY`
and `INTENT_AGENT_IMAGE` (the name from step 2). Leave the two generated
values for the next step.

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
(`127.0.0.1:3200`, `127.0.0.1:8102`), because the console's API has no caller
authentication and acts with the app agent credential. To present from another
device on the network, set `CONSOLE_BIND=0.0.0.0` in `.env` first.

All four services should read `healthy`. Open <http://localhost:3200> and
check three things: the two dots at the bottom of the left rail are green
(Intent Agent, Token Service), the **Vocabulary** view lists policies for
both User and Customer, and the first prompt on the **Playground** comes back
with a ticket. The first prompt may take a few seconds longer while the agent
fetches the schema and policies.

To see the shared Redis cache with two agent instances behind a balancer, set
`COMPOSE_PROFILES=cluster` and `INTENT_AGENT_ENTRY=intent-balancer` in `.env`
and run `make up` again.

### 6. Everyday operations

| Need | Command |
| --- | --- |
| Pick up a change to the console (code or static files) | `make images` then `docker compose up -d console`, and hard-reload the page (Ctrl+Shift+R) so the browser drops its cached script |
| Pick up a change to `.env` (image name, secrets) | `docker compose up -d` (recreates only the containers whose configuration changed) |
| Restart the console without changes | `docker compose restart console` |
| Switch the agent image after editing `INTENT_AGENT_IMAGE` | `docker compose up -d intent-agent`, then remove old images with `docker rmi <name:tag>` once nothing uses them |
| Logs | `make logs`, or `docker compose logs --tail 50 intent-agent` |
| Stop | `make down` |

## Run on minikube

The chart deploys the same four workloads on a local Kubernetes cluster. It
reuses what the compose run already produced: the local images, `.env`, and
the two generated files. The compose stack may keep running; the two do not
share ports. All commands run from `a2a/intent-agent-demo`.

1. Start the cluster:

   ```sh
   minikube start
   ```

2. Load the two local images. Minikube has its own image store and no access
   to your registry, so you hand it the images you already have (the Token
   Service and Redis images are public on Docker Hub and the cluster pulls
   them itself). Use the agent name you set as `INTENT_AGENT_IMAGE`:

   ```sh
   minikube image load intent-agent:<tag>
   minikube image load intent-demo-console:local
   ```

3. Create the namespace and the two Secrets. The chart reads secrets only from
   Kubernetes Secrets, never from values. The first is `.env` as is (the chart
   uses `GEMINI_API_KEY`, `APP_AGENT_CREDENTIALS_TOKEN`,
   `TOKEN_SERVICE_CLIENT_SECRET` and `AUDIT_WEBHOOK_API_KEY` from it). The
   second holds the two generated files, but the Token Service config must
   point its audit webhook at the console's *cluster* service name, so make a
   copy with that one line changed rather than editing the file compose uses:

   ```sh
   kubectl create namespace intent-demo
   kubectl -n intent-demo create secret generic intent-demo-secrets --from-env-file=.env
   sed 's#http://console:3200#http://intent-demo-console:3200#' token-service/token-service.yaml > /tmp/token-service-k8s.yaml
   kubectl -n intent-demo create secret generic intent-demo-files \
     --from-file=token-service.yaml=/tmp/token-service-k8s.yaml \
     --from-file=demo-idp.key=console/keys/demo-idp.key
   rm /tmp/token-service-k8s.yaml
   ```

   `intent-demo-console` is `<release name>-console`; change it if you install
   under another release name in step 4.

4. Install the chart, naming your platform region and the agent image you
   loaded (the chart defaults are `intent-agent:local`):

   ```sh
   helm install intent-demo chart -n intent-demo \
     --set platform.indykiteBaseUrl=<your INDYKITE_BASE_URL> \
     --set intentAgent.image.repository=intent-agent \
     --set intentAgent.image.tag=<tag>
   ```

   The release name is what the service names, and therefore the audit URL in
   step 3, are built from. `helm lint chart` and `make helm-template` check
   the chart renders; `chart/values.schema.json` rejects unknown or malformed
   values.

5. Watch the pods come up:

   ```sh
   kubectl -n intent-demo get pods -w
   ```

   Redis and the Token Service first, then the agent (its readiness probe
   needs the Token Service), then the console. All four should reach
   `1/1 Running`. If one stays in `CrashLoopBackOff`,
   `kubectl -n intent-demo logs deploy/intent-demo-<name>` shows why; the
   usual cause is a missing key in one of the Secrets.

6. Open the console through a loopback port-forward (the Service is a
   ClusterIP on purpose: the console's API has no caller authentication, so it
   is not exposed on the cluster's nodes by default):

   ```sh
   kubectl -n intent-demo port-forward svc/intent-demo-console 3200:3200
   ```

   Open <http://127.0.0.1:3200/> (stop the compose console first if it holds
   the port, or forward to another local port) and check the same three things
   as for compose: green dots in the rail, policies in the Vocabulary view, one
   prompt from the Playground. On the actor strip, hops 3 and 6 filled in prove
   the in-cluster webhook URL from step 3 is right. To expose the console on
   the nodes deliberately, install with `--set console.service.type=NodePort`
   and open `minikube service intent-demo-console -n intent-demo --url`.

7. Tear down when done:

   ```sh
   helm uninstall intent-demo -n intent-demo
   kubectl delete namespace intent-demo
   minikube stop
   ```

After re-creating either Secret (new `.env` values or regenerated files), bump
`secrets.revision` on the next `helm upgrade` (`--set-string secrets.revision=2`;
the value is a string): the console, the Token Service and the agent pods roll
together and pick up the new contents.
A Secret's name never changes, so nothing would roll otherwise.

Inside the cluster the hostnames are `<release>-console`,
`<release>-token-service`, `<release>-intent-agent` and `<release>-redis`; the
introspection URL, the Redis URL and the agent's audit URL are derived by the
chart from the release name. Both stacks talk to the same platform project,
but each console's Audit view only shows the records delivered to its own
webhook.

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
   subject token (`iss https://demo-idp.intent-demo.local`,
   `aud intent-demo-console`) and the console's own actor token
   (`aud intent_agent`). In production an IdP issues both.
2. The console exchanges them at the Token Service (`POST /oauth2/token`,
   RFC 8693, `audience=intent_agent`, HTTP Basic `intent-demo:<secret>`). The
   result is a delegation token: `sub` = the persona, `act.sub` = the console,
   `aud` = `intent_agent`, 5 minutes to live. The Token Service verifies both
   inputs offline against the demo issuer's public key, which `make keys`
   placed inline in `token-service.yaml` - no network call, no external IdP.
3. The console sends the A2A `SendMessage` with that delegation token as
   Bearer. The agent introspects it at the Token Service
   (`POST /oauth2/introspect`, same client pair) and requires `aud` to
   contain `intent_agent` (`EXPECTED_TOKEN_AUDIENCE`).

The three token guardrails in the console are exactly the failures this
design catches: exchanging for another audience (401 "not valid for this
agent"), presenting the raw subject token the Token Service never issued
(401 "not active"), and sending no token (401 "Authorization required"). The
Token Service audits every exchange to the console's webhook, so the Audit
view shows the exchange next to the agent's own record for the same prompt.

## What the decision step can and cannot show

The agent makes no authorization decision; the console adds one so the
intent is seen to mean something. Each evaluation is mapped to the AuthZEN
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
parameters are shown as **carried, not enforced** rather than pretended to be
part of the decision. That is the honest state of the product today: the
agent produces the question in the AuthZEN shape; enforcing a CEL filter on
the resources is the caller's job until the engine consumes it.

## Development

```sh
make test     # console unit tests (decision mapper, issuer, audit hub, A2A parsing)
make lint     # ruff, inheriting the repository's rules via ruff.toml
```

Conventions: Python 3.13, FastAPI, a validated `Settings` object that fails at
startup on a blank secret, no SDK between the console and the agent (the JSON
on the wire is the point), non-root images with healthchecks, nothing
internal in the tree. Prompts and personas are data (`console/data/prompts.json`);
the personas are the CanBank identities from the dataset, nobody invented.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `token-service` restarts, log says a configuration is invalid | `token-service.yaml` still has a placeholder - run `make keys` (or `--force` after deleting the two generated files). |
| Exchange fails `401 invalid_client` | `TOKEN_SERVICE_CLIENT_SECRET` in `.env` differs from `idp.client_auth.client_secret` in `token-service.yaml`. |
| Exchange fails `400 invalid_grant` | The Token Service could not verify the demo issuer's JWT: `console/keys/demo-idp.key` and the public key in `token-service.yaml` are from different `make keys` runs. |
| Agent answers 401 for the delegation token | `EXPECTED_TOKEN_AUDIENCE` and the exchanged `audience` differ, or the agent's `INTROSPECT_CLIENT_*` pair is wrong (then it is 502 "rejected our own client credentials"). |
| Every prompt is refused with "Access policies ... unavailable" | No ACTIVE policy for the persona's subject type: the app agent lacks `ReadAuthZConfigs`, or the `intent` dataset is not provisioned in the project the credential belongs to. Check the Vocabulary view. |
| "Data schema unavailable" | The project has no data yet, or the schema was persisted less than a minute ago. |
| No audit record next to the prompt | The webhook key differs between `.env` and `token-service.yaml`, or `AUDIT_ASYNC` delivery is slower than 5 s; the Audit view still shows it when it lands. |
| `make keys` fails with "refusing to overwrite" | Deliberate: a new config must come with a new `.env` pair. Delete both generated files first, or pass `--force` through the command in the makefile. |

## Phase 2: behind the Agent Gateway

The IndyKite Agent Gateway has an optional `intent_agent` section
(`base_url`, `audience`, `timeout`). With it set, a gateway protecting an A2A
agent resolves the caller with ContX IQ `whoami`, obtains its own token for
audience `intent_agent` and sends the same `SendMessage` this console sends,
before forwarding the request; the intent is logged alongside the
authorization decision. Wiring that up needs a real IdP for the user's token
and a platform Token Introspect configuration that trusts it - the setup the
[iag-token-exchange](../iag-token-exchange) demo already has. Putting that
gateway in front of this stack is the next step; the console's prompt path
would then be `console → gateway → protected agent`, with the gateway calling
the Intent Agent on the way.
