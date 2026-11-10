---
name: ciq-execute
description: Runs a CIQ knowledge query through the IndyKite MCP server. Each query is its own MCP tool, named after the query (indykite_get-self, indykite_get-stock-quote, ...); call that tool with its input_params. indykite_ciq_execute is deprecated.
user-invocable: false
metadata:
  tags: [mcp, analyst, ciq, knowledge-graph]
  depends_on: "list_resources"
---

# CIQ Execute

Use this skill when the request requires **running a CIQ (Knowledge Query)**. The IndyKite MCP
server exposes **one tool per knowledge query, named after the query**; your bound tools carry the
backend prefix `indykite_`: `indykite_get-self`, `indykite_get-stock-quote`,
`indykite_get-stock-trade-threshold`, `indykite_get-internal-documents`, `indykite_get-decisions`,
`indykite_store-decision`, `indykite_get-my-family`. You hold exactly the ones the Agent Gateway
permits for this workflow; a query you do not see is not available here, do not try it through
`indykite_ciq_execute`. The tool's description is the query's own description with its parameters.

## Tools

- **`indykite_<query-name>`** (preferred) - runs that query. Arguments:
    - `input_params` (optional): JSON object of the query's parameters (e.g. `ticker`,
      `customer_external_id`, `taxonomy_external_id`, `document_external_id`).
    - `page_size`, `page_token` (optional): paging, rarely needed.
- **`indykite_ciq_execute`** - **deprecated**, kept for old clients. Same `input_params`, plus
  `id` = the query name. Use it only when the query's own tool is not among your bound tools
  although the request clearly needs it, and say so in the answer.
- **`indykite_read_resource`** on `indykite://knowledge-queries/` lists every query with its
  parameters when a tool description is not enough.

## When to use

- User asks for data that comes from a known or discoverable CIQ (stock price, purchase limit,
  lookups by user/ticker/customer, documents, decisions, family).
- Do **not** use for maximum shares for a user and ticker - use the **max-purchase-amount** skill
  instead, which runs the two queries itself.
- Invoices come from the ERP backend (`erp_` tools, see the erp-invoices skill), not from a CIQ.

### examples

- "Who am I?" -> tool `indykite_get-self`, `{"input_params": {}}`
- "What is the stock price for NTR?" -> tool `indykite_get-stock-quote`,
  `{"input_params": {"ticker": "NTR"}}`
- "What is the purchase limit for customer cust-cashmere?" -> tool
  `indykite_get-stock-trade-threshold`, `{"input_params": {"customer_external_id": "cust-cashmere"}}`
- "Show my family" / "my children's accounts" -> tool `indykite_get-my-family`, `{"input_params": {}}`
- Customer ids are the `cust-<firstname>` external_ids of the Customer records (e.g.
  `cust-cashmere`, `cust-bambi`), never a person's name or Auth0 subject; see the ibac-authz skill
  for the cast.

## Workflow

1. Pick the tool named after the query the request needs; read its description for the parameters.
2. Call it with `input_params`. An empty answer is a policy answer (the caller may not see those
   rows), not an error: report it as such.
3. Fall back to `indykite_ciq_execute` with `id` only when the named tool is absent, and mention
   that it is deprecated.
