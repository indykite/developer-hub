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

## Prerequisites

1. **Dataset provisioned**: instant-stack `data/ibac` (`DATASET=ibac`) with
   `AUTH0_ISSUER`, the eight `AUTH0_SUB_<AGENT>` values and
   `TOKEN_SERVICE_PUBLIC_JWK` set in instant-stack's `.env` before
   provisioning. It holds the people, departments, customers, accounts,
   invoices, documents, decisions, agent workflows, the 34 Person policies
   (incl. `person-can-reach-agent`, the gateways' admission check over the
   delegation chain) and the 13 CIQ queries (`get-self`, `get-stock-quote`,
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

## Act 3 - Weather: two paths (any login)

1. **"What's the weather in London?"** → the weather agent, direct
   Open-Meteo path - no graph.
2. **"What's the weather at CanBank HQ?"** → the HQ keyword routes to
   `get-hq-weather` through the IndyKite MCP server: the `hq_weather` node's
   `current` and `units` are populated live by the external data resolvers.

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

## Act 5 - Parallel multi-agent MCP (Millicent + Marmaduke)

Two people, two agents, one MCP gateway, at the same time: in two browsers
ask both **"Perform an authzen test with the following payload: subject_type
Person, subject_id [your own subject], resource_type Workflow, resource_id wf3,
action_name CAN_TRIGGER. Report the raw decision."** (each with their own
subject from "who am I"). Millicent is **allowed**, Marmaduke **denied** -
same retriever, same `mcp-iag`, separate MCP sessions, decisions bound to
each token.

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
- **The intent beat** (`IBAC_ENABLED=true` + the `intent` profile): the
  orchestrator gateway asks the Intent Agent what each prompt *means* -
  subject, action, resource - before forwarding it. The terminal shows the
  gateway's INTENT card (found / failed, duration, prompt) and the Intent
  Agent's own card with the evaluations and the IBAC discovery preview
  ("matches query-erp"). A prompt with no intent is answered by the gateway
  itself - "Unable to extract intent from prompt." plus the obligation - and
  never reaches the orchestrator: greetings, weather and Drive prompts have
  no policy vocabulary, so run those beats with IBAC off, or show the
  refusal as the beat.
- The first prompt per login is slower (MCP sessions open); warm for
  `MCP_SESSION_TTL` afterwards. Gateways cache each subject's workflow set
  for `IAG_AUTHZEN_CACHE_TTL` (30 s), which paces the grant/revoke beats.
