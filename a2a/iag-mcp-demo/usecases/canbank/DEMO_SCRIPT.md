# CanBank demo script

The banking flavor of the demo: CanBank is a fictional retail bank adding AI
agents to help its employees support their customers. A chatbot and two
agents (an orchestrator and a retriever) are deployed, each protected by the
IndyKite Agent Gateway; the retriever answers questions through the IndyKite
MCP server. Throughout the demo the gateways check the calling chain from
the original requester to the agent at every hop.

Switch with `./switch-usecase.sh canbank` (relinks `.env` to `.env.canbank`
and recreates the stack; see `usecases/README.md`).

For a fully performable narrative version of this script - every
prompt as a beat in one continuous story - see [SCENARIO.md](SCENARIO.md).

## At a glance

| Act | Login | The beat |
| --- | --- | --- |
| 1 | leslie (CSR) | Documents, decisions, and her slice of the invoices |
| 2 | millicent (trading) | Stock quotes, AuthZEN allow AND deny, three invoice row counts |
| 3 | any | Weather: direct API vs graph-resolved HQ |
| 4 | millicent + roy | Google Drive, then deny → remediate → allow live |
| 5 | two users | Parallel multi-agent MCP through one gateway |

Watch the audit terminal throughout: every hop shows the gateway decision
(subject → actor, AUTHORIZED / NOT AUTHORIZED, reason) and the exchanged
delegation TOKEN card - a new token with the user as the subject and the
agent as the actor, so the agent provably acts on the user's behalf.

## The story - one customer, one morning at CanBank

The acts play as a single narrative around **rebecca**, a customer who
holds both a credit card and a trading account:

1. **Rebecca calls support about a fee she wants refunded.** leslie
   (Act 1) pulls the refund policy from the internal library, retrieves
   the past decisions that used it (follow `decision_001` in the data
   explorer), and sees rebecca's invoices among the six her department
   serves - and only those six.
2. **Rebecca also asks how many NVDA shares she could buy.** Support
   can't answer that - run leslie's *"why can't I get a stock quote?"*
   denial - so the call moves to millicent on the trading desk (Act 2):
   a live quote, rebecca's purchase threshold, and the share count. Same
   bank, same agents, same prompts - a different `WORKS_IN` edge decides.
3. **The desk does its research in Google Drive** (Act 4, millicent):
   onboarding notes, the treasury summary. roy from the trading floor
   tries the same and hits the red DENY - the deny → remediate → allow
   beat: millicent grants him access with one click, his retry goes
   green, the revoke resets the world.
4. **Meanwhile the same gateways serve everyone at once** (Act 5):
   millicent allowed and carol denied on the identical workflow through
   the identical agent, concurrently - authorization follows the person,
   not the pipe.

The through-line to say out loud: every answer in this demo is shaped by
relationships in the graph - `WORKS_IN`, `SERVES`, `CAN_TRIGGER` - never
by feature flags or app logic. Change an edge, and the same prompt gets a
different answer.

## Prerequisites

1. **Dataset provisioned**: instant-stack `data/canbank` (set
   `DATASET=canbank`) - documents, customers, decisions, departments, agent
   workflows, KBAC policies, and the CIQ queries (`get-self`,
   `get-stock-quote`, `get-stock-trade-threshold`, `get-internal-documents`,
   `get-customer-facing-documents`, `get-regulatory-agreements`,
   `get-decisions`, `get-hq-weather`, `store-decision`) plus
   `get-agent-workflows` for the gateways, and the `weather` /
   `weather-units` external data resolvers. The Bruno collection
   (`bruno/iag-demo`) mirrors the same data request-by-request for manual
   provisioning or debugging.
2. **Presenting setup**: a split browser with the chatbot
   (`http://localhost:3000`) and the IndyKite Hub; a terminal tailing the
   retriever (`docker logs -f $(docker ps -q --filter
   "name=iag-mcp-demo-retriever-1")`); the IK data explorer centered on
   `external_id=decision_001`.
3. *(Optional)* the `drive` compose profile up for Act 4 (see the README's
   Google Drive section).
4. *(Optional)* the `erp` compose profile for the invoice beats: dataset
   additions provisioned (Invoice nodes, `SERVES`/`HAS_INVOICE` edges, the
   `staff-can-view-invoice` policy, `wf-erp-*` chains), the
   `indykiteagent-erp` IdP client, and in `.env`: `erp` in
   `COMPOSE_PROFILES`, `,erp=http://erp-mcp-iag:8889/mcp` appended to
   `ANALYST_MCP_SERVER_URLS`, `ERP_TOOL_ENABLED=true`,
   `ERP_MCP_IDP_CLIENT_SECRET`. Build with `make new-erp-mcp`.

## The cast

- **leslie** (login, `User`) - Customer Service Rep, `support` department.
- **millicent** (login, `User`) - CSR who also works in `trading`; the
  `trading` department holds the `CAN_RETRIEVE -> stock_quote` edge, so the
  stock-quote, AuthZEN, and Google Drive prompts are hers.
- **flo** (login, `User`) - `support` only; interchangeable with leslie for
  the negative AuthZEN path.
- **roy** (login, `User`) - Roy Kent, a retail trader.
- **rebecca** (data, not a login) - customer with a credit card and a
  trading account; her purchase limit drives the NVDA prompt.
- **carol** and **jane** (logins) - appear only in the WF4 parallel-MCP
  flow: carol as the denied user on `wf3`, jane on the weather workflow
  `wf2`.

## Act 1 - CSR: documents and decisions (log in as leslie)

1. **"who am I"**
   → the retriever resolves the CSR through `get-self`.
2. **"What policy documents pertain to refunds?"**
   → `get-internal-documents` (`taxonomy_external_id=policy`): the internal
   policy library, refund policy included.
3. **"Retrieve past decisions that incorporated the 'refund_policy' document."**
   → `get-decisions` (`document_external_id=refund_policy`): the stored
   decisions - follow along in the data explorer on `decision_001`.
4. **"What customer-facing documents do we have?"**
   → `get-customer-facing-documents`.
5. **"What regulatory agreements do we have?"**
   → `get-regulatory-agreements`.
6. **"Show me the invoices"** *(erp profile)*
   → **6 rows** - the invoices of the customers support `SERVES` (alison,
   bob, charlie), pre-filtered by AuthZEN `search/resource` before any SQL
   runs.
7. **"what are the knowledge queries?"**
   → the retriever reads the MCP server's knowledge-queries resource and
   lists every query it may call.

## Act 2 - Trading desk: quotes and authorization (log in as millicent)

1. **"What is the price of META?"**
   → `get-stock-quote`: the quote via the Yahoo Finance resolver; passes
   because millicent's `trading` department can retrieve quotes. (An
   occasional `429 Too Many Requests` is Yahoo rate-limiting, not an
   authorization failure - retry after a few minutes.)
2. **"Tell me how many shares of NVDA the user with the id: rebecca can
   purchase"**
   → the retriever's composite max-purchase tool:
   `get-stock-trade-threshold` (rebecca's limit) + `get-stock-quote`
   (NVDA price), floor(limit / price).
3. **"Am I allowed to retrieve a stock quote? Check with authzen."**
   → KBAC policy `user-can-retrieve-quote`
   (`User -WORKS_IN-> Department -CAN_RETRIEVE-> Quote`). Watch the
   retriever log: it loads the `authzen` + `canbank-authz` skills, resolves
   its own id via `get-self`, then sends one exact `authzen_evaluate`.
4. **"Perform an authzen test with the following payload: subject_type User,
   subject_id millicent, resource_type Workflow, resource_id wf1,
   action_name CAN_TRIGGER. Report the raw decision."**
   → forces an exact `authzen_evaluate`; decision `true` via the
   `user-can-trigger-workflow` policy.
5. Log in as **leslie** or **flo** (`support` only) for the negative path:
   **"why can't I get a stock quote?"**
   → decision `false` - their department has no `CAN_RETRIEVE` edge.
   Authorization working is best shown by a denial.
6. **"Show me the invoices"** *(erp profile)*
   → millicent (support **and** trading) sees **all 9**; re-run as leslie →
   **6** (support only) and as roy → **3** (trading only: rebecca, ted).
   The exact same prompt, three row counts - decided by `WORKS_IN`/`SERVES`
   edges, not by the database.

Note: evaluations through the chatbot carry the logged-in user's token, and
the platform binds them to that token's subject - asking about a *different*
user (e.g. subject `roy` from millicent's session) always returns `false`
by design.

## Act 3 - Weather: two paths (any login)

1. **"What's the weather in London?"**
   → routed to the `weather_agent`, direct Open-Meteo path - no IKG.
2. **"What's the weather at CanBank HQ?"**
   → the HQ keyword routes to `get-hq-weather` through the IndyKite MCP
   server: the `hq_weather` Weather node's `current` and `units` properties
   are populated live by the `weather` / `weather-units` external data
   resolvers hitting [open-meteo](https://open-meteo.com). Requires
   `CIQ_QUERY_HQ_WEATHER` and the resolver setup.

## Act 4 - Google Drive (compose profile `drive`, log in as millicent)

Mention "Google Drive" (or "Drive") so the orchestrator routes to its
`query_drive` tool; the audit terminal shows the full
orchestrator → analyst → drive-mcp chain (`wf-drive-console`).

1. **"Search Google Drive for canbank and list the matching files."**
   → full-text search over file contents.
2. **"List the files in my Google Drive."**
3. **"Read the file 'CanBank treasury desk summary' from Google Drive and
   summarize it."**
   → Google-native docs only; name the converted copy, not the `.doc`.
4. **"According to the canbank retail onboarding notes in Google Drive,
   what steps are required to onboard a new customer?"**

### The deny → remediate → allow beat (live)

Setup: keep millicent logged in here and open a second browser (or private
window) logged in as **roy** (trading, no drive access) - every console
receives every audit card, so roy's red card appears in millicent's
terminal too.

1. roy: **"List the files in my Google Drive."**
   → red **NOT AUTHORIZED** card (from `drive-mcp-iag`) in both consoles,
   with **why?** and **grant access** buttons.
2. roy clicks **grant access** on his card
   → **403**: "roy is not allowed to grant wf-drive (no CAN_TRIGGER path
   of their own)" - the grant is AuthZEN-guarded, he can't self-serve.
3. millicent clicks **grant access** on the same red card in HER console
   (she holds the drive workflows directly)
   → the Capture write adds `roy -CAN_TRIGGER-> wf-drive*` and the why?
   graph shows the new edge.
4. roy: **"List the files in my Google Drive."** (same prompt again,
   after the gateway cache clears - the same ~5-min/restart note as step 6)
   → **green**, the real Drive listing. *Authorization is data - change
   the graph, behavior changes now.*
5. millicent clicks **revoke access** to reset the beat.
6. roy: **"List the files in my Google Drive."** once more, after ~5
   minutes - or restart the gateways to clear the cache immediately
   (`docker compose restart orchestrator-iag analyst-iag drive-mcp-iag`)
   → red again (the gateways cache each subject's workflow set ~5 min;
   see iag-base-docker.yaml).

## Act 5 - Parallel multi-agent MCP (WF4)

Two users, two agents, one MCP gateway — at the same time.

Log in as two different users in two browsers (or run the Bruno suite
`bruno/iag-demo/wf4-parallel-mcp`, which drives it deterministically: millicent
and carol through the shared retriever, millicent through the analyst). All the
MCP sessions flow through the same `mcp-iag` gateway concurrently, each with
its own `Mcp-Session-Id`, and the authorization decisions follow each user's
token: the identical `authzen_evaluate` call on workflow `wf3` is **allowed**
for millicent and **denied** for carol, even though both ride the same retriever
agent. The audit stream in the chatbot UI shows the interleaved sessions and
the per-user allow/deny decisions.

### The ERP stagecraft

The invoice prompts live in the acts: Act 1 #6 leslie, Act 2 #6
millicent/leslie/roy - the same-prompt-three-row-counts contrast.

- **The contrast shot**: the database hides nothing -

  ```bash
  docker exec iag-mcp-demo-erp-db-1 psql -U erp -d erp \
    -c "SELECT external_id, customer_name, amount, status FROM invoices;"
  ```

  shows every row unfiltered; the graph
  (`Department-[SERVES]->Customer-[HAS_INVOICE]->Invoice`) decides what
  each login sees, via AuthZEN `search/resource` BEFORE the SQL.
- The first prompt per login is slower (the analyst opens its MCP backend
  sessions); warm for `MCP_SESSION_TTL` afterwards.

### The why? beat

Requires the explain queries provisioned (`EXPLAIN_STAFF_QUERY_ID` /
`EXPLAIN_DIRECT_QUERY_ID` in `.env`): click **why?** on any AUTHORIZED /
NOT AUTHORIZED card and the console asks the live graph for the
authorization path - e.g.
`(leslie)-[WORKS_IN]->(Department)-[CAN_TRIGGER]->(wf1)`, or two
disconnected nodes when no path exists (the denial explained). Capture a new
`CAN_TRIGGER` edge and the same card's why? shows the path - authorization
is data.
