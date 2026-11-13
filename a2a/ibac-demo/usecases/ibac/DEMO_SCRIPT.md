# CanBank demo script (ibac)

CanBank is a fictional retail bank adding AI agents to help its employees
support their customers. A console and a set of agents (orchestrator,
retriever, analyst, weather, CRM) are deployed, each behind an IndyKite
Agent Gateway; the retriever and analyst answer through the IndyKite MCP
server, the analyst also through Google Drive and the ERP. Every hop carries
the user's Auth0 token in `Authorization` and the Token Service delegation
in `X-IK-Token`, and every gateway checks the calling chain against the
graph.

What is different from the other CanBank demos: **people are Auth0 users**.
Every login is a `Person` whose `external_id` is the Auth0 subject
(`auth0|...`), staff and customers alike, and families are in the graph -
so parents, minors and guardians are part of the story.

For the same prompts as one continuous narrative see
[SCENARIO.md](SCENARIO.md).

## At a glance

| Act | Login | The beat |
| --- | --- | --- |
| 1 | Vivienne (support supervisor) | Documents, decisions, her slice of the invoices |
| 2 | Millicent (support + trading) | Stock quotes, AuthZEN allow AND deny, three invoice row counts |
| 3 | any | Weather: direct API vs graph-resolved HQ |
| 4 | Millicent + Cookie | Google Drive, then deny → remediate → allow live |
| 5 | Millicent + Marmaduke | Parallel multi-agent MCP through one gateway |
| 6 | Cashmere, Cornelius, Marmaduke | Customers and guardians: own data, a minor child's data, nothing else |
| 7 | Millicent, then Cornelius | Salesforce cases: staff may, a customer may not |

Watch the audit terminal throughout: every hop shows the gateway decision
(subject → actor, AUTHORIZED / NOT AUTHORIZED, reason) and the exchanged
delegation TOKEN card - a new token with the person as the subject and the
agent chain in `act`, so the agent provably acts on the person's behalf.
Subjects in the cards are Auth0 subjects; agents are the M2M clients
(`<client_id>@clients`).

The console opens on its **App** layer - the CanBank web app. **Home** has
a free-text assistant; the nav opens Accounts, Investments, Documents and
Support, each with action cards that send the prompts below (see
`app.json`). Log in as different people and the same cards show different
things: customers also get a **Family** group, staff get the Trading desk
and the Support inbox.

## The gateway on stage

Every agent sits behind an Agent Gateway, with a Token Service of the same
release (see the README). What a gateway does on each hop, and where the
audience sees it in this script:

| Gateway behaviour | Where to see it |
| --- | --- |
| Token Service mode | The TOKEN card of every hop (two in a row in Act 7); the **chain** line under every card; Bruno `chain-guard` |
| One admission question per hop | Every `admission` card; Act 4: deny, grant, allow with no gateway touched |
| Capability checks | Stagecraft, *the tools beat*: the `discovery` cards of `mcp-iag` with their permitted set; Act 3 (two tools) against Act 2 (sixteen). Off on `drive-mcp-iag`: Drive files are dynamic resources, admission alone decides there |
| Audit records name their question | The small tag on every card: `admission`, `discovery`, `invocation`, `refused early` |
| The intent in the delegated token | With `IBAC_ENABLED=true` only: a second TOKEN card after the INTENT card of `orchestrator-iag`; the token the orchestrator receives carries `act.intent` |
| The subject comes from the graph | "who am I" in every act; the subject on the cards is the Auth0 `sub` |
| Tokens reused | Stagecraft, *the token-reuse beat* |
| The agent card at the gateway | Stagecraft, *the card beat* |
| The delegated token extension | Stagecraft, *the card beat* and *the discovery beat*: `capabilities.extensions` and `mcp_extensions` |
| MCP front door | Stagecraft, *the discovery beat*; Act 5 (two people, one MCP gateway) |
| IBAC switch | Stagecraft, *the intent beat* |
| The chain in every audit record | The chain line under every card; the trace id is its tooltip |
| No clock on the answer | Act 2 step 2 and Act 4 step 3: slow composite answers arrive whole |

What each line means:

- **Token Service mode**: the person's Auth0 token stays in `Authorization`;
  the delegation travels in `X-IK-Token` and nests the chain hop by hop; one
  shared audience; a workflow starts only at the agent the token was issued
  for, so a person's token sent to a later gateway directly is refused.
- **One admission question per hop**: may this subject `CAN_TRIGGER` *this*
  agent over *this* chain? The gateway asks AuthZEN once; the dataset's
  `person-can-reach-agent` policy compares `$ik_token.act_chain` with a
  workflow path in the graph. The gateway keeps no workflow list.
- **Capability checks**: once admitted, a caller used to see and use
  everything the MCP server exposed. Now the gateway asks the platform which
  tools (and resources, prompts) this person may `CAN_USE` over this chain,
  filters `tools/list` to that set and refuses a `tools/call` outside it with
  `403` and JSON-RPC `-31403`, before the server is called. The set comes
  from the graph: `Agent -HAS {workflow_name}-> Tool` edges and the
  `person-can-use-*` policies, so the same MCP server shows different tools
  to different workflows. In front of an A2A agent the gateway filters the
  skills of the extended card; a message names no skill, so that part is
  discovery only (the next hop's capability check bounds what a hidden skill
  can reach).
- **Audit records name their question**: `action` is `ADMISSION` (may the
  subject reach the agent over this chain), `DISCOVERY` (what may the caller
  see), `INVOCATION` (may the caller use what it named), `INTENT` or `noop`
  (refused before any question), with the chain, the permitted set and
  whether the answer was cached. One record per decision, never two.
- **The subject comes from the graph**: whoami resolves the token through the
  Token Introspect to a `Person`; no subject type is configured on a gateway.
- **Tokens reused**: a gateway keeps its client-credentials token until
  shortly before `expires_in`, one Auth0 grant per audience per lifetime.
- **The agent card at the gateway**: `/.well-known/agent-card.json` with the
  interfaces pointing at the gateway and Auth0 as security scheme; the skills
  only with a token.
- **The delegated token extension**: a gateway with a Token Service tells
  callers that the target takes an `X-IK-Token`. On the agent cards it serves
  it is `https://indykite.com/extensions/delegated-token/v1` in
  `capabilities.extensions` (not required: a caller without a delegation
  still gets in with its own token), and every call it forwards names it in
  `A2A-Extensions`. In front of an MCP server it is
  `com.indykite/delegated-token` in the server's capabilities and in
  `mcp_extensions` of the protected resource metadata. The declaration is
  the contract behind the chain line: an agent that sees it knows which
  header carries the delegation to pass on.
- **MCP front door**: one endpoint at `service.base_url`, protocol revisions
  enforced (2025-06-18 to 2026-07-28), RFC 9728 protected-resource metadata
  and a `WWW-Authenticate` that points at it; the server's `initialize`
  answer is checked too.
- **IBAC switch**: with `ibac.enabled` every prompt goes to the Intent Agent
  first; a prompt without an intent is answered by the gateway itself, with
  `intent_error`, and audited as `INTENT`. A prompt with an intent gets a
  second exchange: the Token Service writes the evaluations into
  `act.intent` of the token the agent receives. An intent of more than ten
  evaluations is answered by the gateway, asking to split the request.
- **The chain in every audit record**: `actorsChain`, `traceID` and the
  `tokenID` it concerns.
- **No clock on the answer**: a long agent answer is never cut by the gateway.

Not on stage: a gateway with `ibac.enabled` checks an intent against
capability mappings, for MCP calls (`IBAC_USAGE` records) and for the skills
of the extended agent card (`IBAC_DISCOVERY` records), and only when the
delegated token of the request carries an intent. Here the entry gateway
alone has it, and the console reaches it with the person's token only, so
neither check runs; the console's "IBAC discovery preview" line shows what
the card check would decide.

Each act below ends with a **Spot the gateway** line: the one thing on the
terminal that is the gateway's work in that act.

## Prerequisites

1. **Dataset provisioned**: instant-stack `data/ibac` (`DATASET=ibac`) with
   `AUTH0_ISSUER`, the eight `AUTH0_SUB_<AGENT>` values and
   `TOKEN_SERVICE_PUBLIC_JWK` set in instant-stack's `.env` before
   provisioning. It holds the people, departments, customers, accounts,
   invoices, documents, decisions, agent workflows, the capabilities of the
   agents (`Tool`, `McpResource` and `Skill` nodes on `HAS {workflow_name}`
   edges), the 40 Person policies (incl. `person-can-reach-agent`, the
   gateways' admission check over the delegation chain, the four
   `person-can-use-*` capability policies, and `person-can-view-profile` /
   `person-can-read-weather`, which give "who am I" and the weather prompts
   a vocabulary for the Intent Agent) and the 13 CIQ queries (`get-self`, `get-stock-quote`,
   `get-stock-trade-threshold`, `get-internal-documents`,
   `get-customer-facing-documents`, `get-regulatory-agreements`,
   `get-decisions`, `get-hq-weather`, `store-decision`, the two explain
   queries, `get-internal-documents-chained`, `get-my-family`) and the three
   external data resolvers.
2. **Auth0 tenant**: the APIs `ibac-console`, `ibac-agents`, `intent_agent`,
   the console application (callback `http://localhost:3300/auth/callback`,
   logout URL `http://localhost:3300/`) and the eight M2M applications
   authorized for `ibac-agents` and `intent_agent`; the twelve demo people
   as users. See the app README.
3. **Presenting setup**: a split browser with the console
   (`http://localhost:3300`) and a second browser or private window for the
   second login of Acts 4-7; a terminal tailing the retriever
   (`docker logs -f ibac-demo-retriever-1`).
4. *(Optional)* the `drive` compose profile for Act 4, `erp` for the invoice
   beats (`make new-erp-mcp`, `erp` in `COMPOSE_PROFILES`,
   `,erp=http://erp-mcp-iag:9889/mcp` appended to `ANALYST_MCP_SERVER_URLS`,
   `ERP_TOOL_ENABLED=true`), `crm` for Act 7 (Salesforce connected app, see
   the README's CRM section).

## The cast

Auth0 users (logins) - the subjects are listed in the `ibac-authz` skill:

- **Millicent Contextsworth** - CSR 2 in `support` **and** `trading`; direct
  grants on wf1, wf3, wf3-console and the Drive workflows; mother of
  Tokenina (11). The stock-quote, Drive and grant beats are hers.
- **Vivienne Boardroom** - Customer Service Supervisor, `support` only;
  Millicent's and Cookie's manager; made `decision_001`.
- **Cookie Malhotra** - Trader, `trading` only; also a customer
  (`cust-cookie`, chequing, investing, a late credit card); mother of
  Cashmere (20, an adult).
- **Marmaduke Doomington** - Operations Officer, `ops`; direct grant on wf1
  only (the denied-on-wf3 persona); customer (`cust-marmaduke`, 401k, loan);
  father of Berengar (14) and Humperdinck (15), who hold youth accounts and
  Berengar an invoice.
- **Preston Capitalworth** (private banking), **Mild Concern** (risk),
  **BetaTest McAllister** and **ApiCall Jackson-Lee** (tech) - staff without
  a beat of their own; useful as "a department that cannot".
- **Bambi Stardust** - customer (`cust-bambi`, chequing + mortgage).
- **Cashmere WiFi Malhotra** - customer (`cust-cashmere`, investing + paid
  credit card); opened `ticket_001`, closed by Vivienne's decision.
- **Cornelius Pickleworth** (14) and **Eustorgio Rodriguez** (15) - minor
  customers with youth accounts.

In the graph only (no Auth0 user yet): the relatives - Tokenina, Berengar,
Humperdinck, Synergy and Quarterly-Returns (youth accounts), Percival,
Harshvardhan, Beulah, Mortimer, Wilfred, Filogonio, Petronila, Major Issue,
Murong Feixue Tianyu. Their subjects have the Auth0 shape; create the user
with that id in the tenant and they can log in.

## Act 1 - Support: documents and decisions (log in as Vivienne)

1. **"who am I"**
   → `get-self`: Vivienne, Customer Service Supervisor, `support`.
2. **"What policy documents pertain to refunds?"**
   → `get-internal-documents` (`taxonomy_external_id=policy`): the internal
   policy library, refund policy included.
3. **"Retrieve past decisions that incorporated the 'refund_policy' document."**
   → `get-decisions`: `decision_001` - Vivienne waived Cashmere's late fee.
4. **"What customer-facing documents do we have?"** and
   **"What regulatory agreements do we have?"**
5. **"Show me the invoices"** *(erp profile)*
   → **6 rows**: the invoices of the customers `support` `SERVES` (Bambi 2,
   Cornelius 1, Eustorgio 1, Marmaduke 1, Berengar 1), pre-filtered by
   AuthZEN `search/resource` before any SQL runs.
6. Click **why?** on a green card
   → `Vivienne → support → CAN_TRIGGER → wf1`, drawn live from the graph.

**Spot the gateway**: the first card of the day. The subject is Vivienne's
Auth0 `sub` (whoami resolved it to her Person node), the chain line reads
`orchestrator`, and the badge is one AuthZEN answer: `person-can-reach-agent`
found `wf1 -INVOKES-> orchestrator` behind her department.

## Act 2 - Trading desk: quotes and authorization (log in as Millicent)

1. **"What is the price of META?"**
   → `get-stock-quote` via the Yahoo Finance resolver; passes because
   `trading` can retrieve quotes. (A `429` is Yahoo rate-limiting, retry.)
2. **"Tell me how many shares of NVDA the customer cust-cashmere can purchase"**
   → the retriever's composite max-purchase tool:
   `get-stock-trade-threshold` (Cashmere's tier 3 limit) +
   `get-stock-quote` (NVDA price), floor(limit / price).
3. **"Am I allowed to retrieve a stock quote? Check with authzen."**
   → `person-can-retrieve-quote`
   (`Person -WORKS_IN-> Department -CAN_RETRIEVE-> Quote`): `true`. Watch the
   retriever log: it loads `authzen` + `ibac-authz`, resolves its own
   subject via `get-self`, then sends one exact `authzen_evaluate`.
4. Log in as **Vivienne** for the negative path:
   **"why can't I get a stock quote?"**
   → `false` - `support` has no `CAN_RETRIEVE` edge. Authorization working
   is best shown by a denial.
5. **"Show me the invoices"** *(erp profile)*
   → Millicent (support **and** trading) sees **all 9**; Vivienne → **6**;
   Cookie → **3** (trading serves Cookie and Cashmere). The exact same
   prompt, three row counts - decided by `WORKS_IN`/`SERVES` edges.

Note: evaluations through the console carry the logged-in person's token
and the platform binds them to that token's subject - asking about a
*different* person always returns `false` by design.

**Spot the gateway**: on the retriever's card the chain line reads
`orchestrator → retriever`; on the MCP gateway's, `orchestrator → retriever
→ mcp`. Each hop asked its own question, for its own agent, with the chain
one hop longer - the same person, three decisions.

## Act 3 - Weather: two paths (any login)

1. **"What's the weather in London?"** → the weather agent, direct
   Open-Meteo path - no graph.
2. **"What's the weather at CanBank HQ?"** → the HQ keyword routes to
   `get-hq-weather` through the IndyKite MCP server: the `hq_weather` node's
   `current` and `units` are populated live by the external data resolvers.

**Spot the gateway**: two prompts, two chains. The London prompt ends at
`weather`; the HQ prompt ends one hop further, at `mcp`. A chain is a
different resource for the admission question, so the graph needs a
workflow path for each.

## Act 4 - Google Drive and the grant (profile `drive`; Millicent + Cookie)

Mention "Google Drive" so the orchestrator routes to `query_drive`; the
audit terminal shows orchestrator → analyst → drive-mcp (`wf-drive-console`).

1. Millicent: **"Search Google Drive for canbank and list the matching files."**
2. Millicent: **"List the files in my Google Drive."**
3. Millicent: **"Read the file 'CanBank treasury desk summary' from Google
   Drive and summarize it."**

The deny → remediate → allow beat: second browser logged in as **Cookie**
(trading, no Drive grant).

1. Cookie: **"List the files in my Google Drive."**
   → red **NOT AUTHORIZED** from `drive-mcp-iag` in both consoles. Cookie
   sees **why?** and **request access**; Millicent sees **why?** and
   **grant access** on the same card.
2. Cookie clicks **request access** → it lands in Millicent's Support inbox.
   The grant endpoint is AuthZEN-guarded: Cookie cannot self-serve (403).
3. Millicent clicks **grant access** → Capture writes
   `Cookie -CAN_TRIGGER-> wf-drive*`; the why? graph shows the new edge.
4. Cookie, ~30 s later (gateway cache, `IAG_AUTHZEN_CACHE_TTL`): the same
   prompt → **green**. *Authorization is data.*
5. Millicent clicks **revoke access**; Cookie's retry is red again.

**Spot the gateway**: between Cookie's red card and her green one no
gateway was touched. One edge was written, and the next admission question
got another answer once the 30 s decision cache (`IAG_AUTHZEN_CACHE_TTL`)
had turned over.

## Act 5 - Parallel multi-agent MCP (Millicent + Marmaduke)

Two people, two agents, one MCP gateway, at the same time: in two browsers
ask both **"Perform an authzen test with the following payload: subject_type
Person, subject_id [your own subject], resource_type Workflow, resource_id wf3,
action_name CAN_TRIGGER. Report the raw decision."** (each with their own
subject from "who am I"). Millicent is **allowed**, Marmaduke **denied** -
same retriever, same `mcp-iag`, separate MCP sessions, decisions bound to
each token.

**Spot the gateway**: one MCP gateway, one endpoint, two sessions whose
chains both end at `mcp`. Decisions are cached per subject and chain, so
Millicent's allow never leaks to Marmaduke.

## Act 6 - Customers and guardians

1. Log in as **Cashmere** (20): **"who am I"** → her customer record
   `cust-cashmere`, no department. **"Show me the invoices"** → **1 row**
   (`inv-cb-009`). **"Show me invoice inv-cb-001"** → an authorization
   error: naming an id is not enough. **"Show my family"** → her parents;
   no accounts of others.
2. Log in as **Cornelius** (14): **"Show me the invoices"** → **1 row**
   (`inv-cb-003`, paid). **"What is the price of META?"** → denied: no
   department, no quote.
3. Log in as **Marmaduke** (parent of two minors): **"Show my family"** →
   Berengar (14) and Humperdinck (15) with their youth savings accounts.
   **"Show me the invoices"** → **2 rows**: his own `inv-cb-005` and
   Berengar's `inv-cb-006` - the second one through the guardian policy
   (`PARENT_OF` + `IS_AGE_GROUP minor`), not through his department.
   **"Which accounts of my children am I allowed to view as a guardian?
   Check with authzen."** → `search/resource` on `VIEW_DEPENDENT_ACCOUNT`.
4. Log in as **Cookie** (parent of an adult): **"Show my family"** →
   Cashmere listed as an adult, **no** account of hers. The age band is a
   node (`IS_AGE_GROUP`), so the same policy says yes to Marmaduke and no
   to Cookie.

## Act 7 - Salesforce cases (profile `crm`)

1. Millicent: **"Open a Salesforce case for Cashmere WiFi Malhotra about her
   late payment fee."**
   → orchestrator → `crm-iag` (`wf-crm`, staff-only) → the CRM agent. Watch
   the AUTHORIZED hop, the delegation TOKEN card, then a **second TOKEN
   card**: the Salesforce access token from the RFC 7523 exchange. The Case
   description records "Filed on behalf of ... via agent chain ...".
2. Cornelius (or Cashmere): **"Open a Salesforce case about the late fee on
   my credit card"**
   → red **NOT AUTHORIZED** on `crm-iag`: no `CAN_TRIGGER` path from a
   customer to `wf-crm`. The why? modal draws the two disconnected nodes.

**Spot the gateway**: `crm-iag`'s card carries the chain `orchestrator →
crm`, the delegation the gateway minted. The second TOKEN card is the CRM
agent's own exchange at Salesforce (RFC 7523), a trust domain the gateway
never enters.

## Stagecraft

- **The contrast shot** (erp): the database hides nothing -

  ```bash
  docker exec ibac-demo-erp-db-1 psql -U erp -d erp \
    -c "SELECT external_id, customer_name, amount, status FROM invoices;"
  ```

  shows every row; the graph decides what each login sees, before the SQL.
- **The why? beat**: with `EXPLAIN_STAFF_QUERY_ID` / `EXPLAIN_DIRECT_QUERY_ID`
  set, **why?** on any card asks the live graph for the path (staff leg
  `Person -WORKS_IN-> Department -CAN_TRIGGER-> Workflow`, direct leg
  `Person -CAN_TRIGGER-> Workflow`) or shows the gap.
- **The intent beat** (`IBAC_ENABLED=true` + the `intent` profile; run every
  other act with the switch **off**, it changes nothing in them except
  refusing prompts outside the policy vocabulary): the
  orchestrator gateway asks the Intent Agent what each prompt *means* -
  subject, action, resource - before forwarding it. The terminal shows the
  gateway's INTENT card (found / failed, duration, prompt) and the Intent
  Agent's own card with the evaluations and the IBAC discovery preview
  ("matches query-erp"). A prompt with no intent is answered by the gateway
  itself - "Unable to extract intent from prompt." plus the obligation - and
  never reaches the orchestrator. The Intent Agent reads only KBAC policies,
  so "who am I" and the HQ weather have their own (`person-can-view-profile`,
  `person-can-read-weather`) and pass; greetings, web search and Drive
  prompts still have no policy vocabulary, so run those beats with IBAC off,
  or show the refusal as the beat. A prompt the model refuses although a
  policy exists is rephrased closer to the action ("Retrieve the stock quote
  for META").
- **The card beat**: the gateway serves the protected
  agent's card itself -

  ```bash
  curl -s http://localhost:9881/.well-known/agent-card.json | jq .
  ```

  the orchestrator's card with every interface URL rewritten to the
  gateway, Auth0 as the security scheme and **no skills**: the public card.
  Under `capabilities.extensions` sits the delegated token extension
  (`https://indykite.com/extensions/delegated-token/v1`): the
  gateway's promise that this agent takes an `X-IK-Token`. Add
  `-H "Authorization: Bearer <a person's token>"` (copied from the console
  session, as for Bruno) and the skills appear - after the same admission
  question as any prompt.
- **The discovery beat**: the MCP gateway tells a client
  where to get a token, as the MCP authorization specification asks -

  ```bash
  curl -s http://localhost:9886/.well-known/oauth-protected-resource/mcp/v1/gid:<PROJECT_GID> | jq .
  curl -si -X POST http://localhost:9886/mcp/v1/gid:<PROJECT_GID> \
    -H 'Content-Type: application/json' -H 'Mcp-Protocol-Version: 2025-11-25' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head -8
  ```

  The first answers the RFC 9728 metadata without any token: `resource` is
  the gateway's `service.base_url`, `authorization_servers` the Auth0
  issuer, and `mcp_extensions` lists `com.indykite/delegated-token`. The second, tokenless, is a `401` whose `WWW-Authenticate` names
  that metadata URL. Any other path on that port is a `404` before a token
  is read: the gateway serves one MCP endpoint.
- **The tools beat**: the same MCP server, two workflows, two
  tool lists. After Act 2 step 1 (Millicent, a stock quote, `wf1`) open the
  `discovery` card of `mcp-iag`: *permitted set* lists `ciq_execute`, the
  four `authzen_*` tools and the retriever's knowledge queries, and
  *discovery* says how many tools the server listed and how many the
  retriever got. After Act 3 step 2 (the HQ weather, `wf2`) the next
  `discovery` card of the same gateway permits two: `ciq_execute` and
  `get-hq-weather`. Nothing was configured on the gateway: the `HAS` edges
  of the `mcp-gateway` agent carry a `workflow_name`, and the policy
  `person-can-use-tool` walks the chain of the call. An `invocation` card
  appears when a tool is called; a red one with `-31403` is a tool outside
  the set. The Bruno `wf4-parallel-mcp` folder calls `mcp-iag` directly: its
  `tools/list` is the fastest way to show the list change by hand.
- **The token-reuse beat**:

  ```bash
  docker logs ibac-demo-auth0-bridge-1 2>&1 | grep "token caller"
  ```

  one `grant=client_credentials` line per gateway per token lifetime. Send
  three more prompts: cards appear, no new line - the gateway kept its
  token.
- The first prompt per login is slower (MCP sessions open); warm for
  `MCP_SESSION_TTL` afterwards. Gateways cache each admission decision per
  subject and chain for `IAG_AUTHZEN_CACHE_TTL` (30 s), which paces the
  grant/revoke beats.
