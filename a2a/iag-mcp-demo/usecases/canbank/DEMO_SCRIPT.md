# CanBank demo script

The banking flavor of the demo: CanBank is a fictional retail bank adding AI
agents to help its employees support their customers. A chatbot and two
agents (an orchestrator and a retriever) are deployed, each protected by the
IndyKite Agent Gateway; the retriever answers questions through the IndyKite
MCP server. Throughout the demo the gateways check the calling chain from
the original requester to the agent at every hop.

Switch with `./switch-usecase.sh canbank` (relinks `.env` to `.env.canbank`
and recreates the stack; see `usecases/README.md`).

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

1. "who am I" - the retriever resolves the CSR through `get-self`.
2. "What policy documents pertain to refunds?"
   → `get-internal-documents` (`taxonomy_external_id=policy`): the internal
   policy library, refund policy included.
3. "Retrieve past decisions that incorporated the 'refund_policy' document."
   → `get-decisions` (`document_external_id=refund_policy`): the stored
   decisions - follow along in the data explorer on `decision_001`.
4. "What customer-facing documents do we have?"
   → `get-customer-facing-documents`.
5. "What regulatory agreements do we have?"
   → `get-regulatory-agreements`.
6. "Show me the invoices" *(erp profile)* → **6 rows** - the invoices of
   the customers support `SERVES` (alison, bob, charlie), pre-filtered by
   AuthZEN `search/resource` before any SQL runs.
7. "what are the knowledge queries?" - the retriever reads the MCP server's
   knowledge-queries resource and lists every query it may call.

## Act 2 - Trading desk: quotes and authorization (log in as millicent)

1. "What is the price of META?"
   → `get-stock-quote`: the quote via the Yahoo Finance resolver; passes
   because millicent's `trading` department can retrieve quotes. (An
   occasional `429 Too Many Requests` is Yahoo rate-limiting, not an
   authorization failure - retry after a few minutes.)
2. "Tell me how many shares of NVDA the user with the id: rebecca can
   purchase"
   → the retriever's composite max-purchase tool:
   `get-stock-trade-threshold` (rebecca's limit) + `get-stock-quote`
   (NVDA price), floor(limit / price).
3. "Am I allowed to retrieve a stock quote? Check with authzen."
   → KBAC policy `user-can-retrieve-quote`
   (`User -WORKS_IN-> Department -CAN_RETRIEVE-> Quote`). Watch the
   retriever log: it loads the `authzen` + `canbank-authz` skills, resolves
   its own id via `get-self`, then sends one exact `authzen_evaluate`.
4. "Perform an authzen test with the following payload: subject_type User,
   subject_id millicent, resource_type Workflow, resource_id wf1,
   action_name CAN_TRIGGER. Report the raw decision."
   → forces an exact `authzen_evaluate`; decision `true` via the
   `user-can-trigger-workflow` policy.
5. Log in as **leslie** or **flo** (`support` only) for the negative path:
   "why can't I get a stock quote?"
   → decision `false` - their department has no `CAN_RETRIEVE` edge.
   Authorization working is best shown by a denial.
6. "Show me the invoices" *(erp profile)* → millicent (support **and**
   trading) sees **all 9**; re-run as leslie → **6** (support only) and as
   roy → **3** (trading only: rebecca, ted). The exact same prompt, three
   row counts - decided by `WORKS_IN`/`SERVES` edges, not by the database.

Note: evaluations through the chatbot carry the logged-in user's token, and
the platform binds them to that token's subject - asking about a *different*
user (e.g. subject `roy` from millicent's session) always returns `false`
by design.

## Act 3 - Weather: two paths (any login)

1. "What's the weather in London?"
   → routed to the `weather_agent`, direct Open-Meteo path - no IKG.
2. "What's the weather at CanBank HQ?"
   → the HQ keyword routes to `get-hq-weather` through the IndyKite MCP
   server: the `hq_weather` Weather node's `current` and `units` properties
   are populated live by the `weather` / `weather-units` external data
   resolvers hitting [open-meteo](https://open-meteo.com). Requires
   `CIQ_QUERY_HQ_WEATHER` and the resolver setup.

## Act 4 - Google Drive (compose profile `drive`, log in as millicent)

Mention "Google Drive" (or "Drive") so the orchestrator routes to its
`query_drive` tool; the audit terminal shows the full
orchestrator → analyst → drive-mcp chain (`wf-drive-console`).

1. "Search Google Drive for canbank and list the matching files." -
   full-text search over file contents.
2. "List the files in my Google Drive."
3. "Read the file 'CanBank treasury desk summary' from Google Drive and
   summarize it." - Google-native docs only; name the converted copy, not
   the `.doc`.
4. "According to the canbank retail onboarding notes in Google Drive, what
   steps are required to onboard a new customer?"

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

Watch the audit terminal throughout: every hop shows the gateway decision
(subject → actor, AUTHORIZED / NOT AUTHORIZED, reason) and the exchanged
delegation token for that hop - a new token with the user as the subject and
the agent as the actor, so the agent provably acts on the user's behalf.

**The ERP stagecraft** (invoice prompts live in the acts: Act 1 #6 leslie,
Act 2 #6 millicent/leslie/roy - the same-prompt-three-row-counts contrast):

- **The contrast shot**: `docker exec iag-mcp-demo-erp-db-1 psql -U erp -d
  erp -c "SELECT external_id, customer_name, amount, status FROM
  invoices;"` shows every row unfiltered - the database hides nothing; the
  graph (`Department-[SERVES]->Customer-[HAS_INVOICE]->Invoice`) decides
  what each login sees, via AuthZEN `search/resource` BEFORE the SQL.
- The first prompt per login is slower (the analyst opens its MCP backend
  sessions); warm for `MCP_SESSION_TTL` afterwards.

**The why? beat** (requires the explain queries provisioned -
`EXPLAIN_STAFF_QUERY_ID` / `EXPLAIN_DIRECT_QUERY_ID` in `.env`): click
**why?** on any AUTHORIZED / NOT AUTHORIZED card and the console asks the
live graph for the authorization path - e.g.
`(leslie)-[WORKS_IN]->(Department)-[CAN_TRIGGER]->(wf1)`, or two
disconnected nodes when no path exists (the denial explained). Capture a new
`CAN_TRIGGER` edge and the same card's why? shows the path - authorization
is data.
