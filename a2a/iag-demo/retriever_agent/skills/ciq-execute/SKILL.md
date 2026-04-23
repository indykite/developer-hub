---
name: ciq-execute
description: Executes specific CIQ knowledge queries. Use this ONLY after receiving a resource list from 'list_resources'. Requires a valid 'id' and 'input_params'.
user-invocable: false
metadata:
  tags: [mcp, retriever, ciq, knowledge-graph]
  depends_on: "list_resources"
---

# CIQ Execute

Use this skill when the request requires **running a CIQ (Knowledge Query)**. CIQ matches **MCP resources or tools** exposed by the backend. List MCP resources and inspect available
MCP tools to find the CIQ capability (as a resource or the ciq_execute tool), then run it with the correct ID and parameters. Consult query descriptions and examples to determine
the right input parameters.

## Tool

- **ciq_execute** – Executes a CIQ query. Arguments:
    - `id` (required): The query ID (e.g. a GID or name from the server).
    - `input_params` (optional): JSON object of input parameters (e.g. `ticker`, `customer_external_id`, `user_external_id`).

## When to use

- User asks for data that comes from a known or discoverable CIQ (stock price, purchase limit, lookups by user/ticker/customer, etc.).
- Do **not** use for maximum shares for a user and ticker—use the **max-purchase-amount** skill instead, which uses ciq_execute internally.

### examples

- "Who am I?" -> `{"id": "get-self", "input_params": { }}`
- "What is the stock price for NTR?" -> `{"id": "get-stock-quote", "input_params": {"ticker": "NTR"}}`
- "What is the purchase limit for user X?" -> `{"id": "get-stock-trade-threshold", "input_params": {"customer_external_id": "bob"}}`

## Workflow

Consider available MCP resources (list_resources) and MCP tools together with this skill. Select the CIQ resource or tool (e.g. ciq_execute) that matches the request and run it with the appropriate id and input_params.
