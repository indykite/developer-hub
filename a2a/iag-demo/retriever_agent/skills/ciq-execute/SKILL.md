---
name: ciq-execute
description: Run a specific Knowledge Query (CIQ). This skill matches MCP resources or tools exposed by the backend—use list_resources and the available MCP tools to find the CIQ capability (e.g. ciq_execute tool or a CIQ resource), then run it with the query id and optional input_params.
tags:
  - mcp
  - retriever
  - ciq
  - knowledge-graph
examples:
  - "What is the stock price for AAPL?"
  - "What is the purchase limit for user X?"
  - "Look up data for ticker NVDA."
  - "Run the CIQ query for customer Y."
---

# CIQ Execute

Use this skill when the request requires **running a CIQ (Knowledge Query)**.
CIQ matches **MCP resources or tools** exposed by the backend. List MCP
resources and inspect available MCP tools to find the CIQ capability (as a
resource or the `ciq_execute` tool), then run it with the correct ID and
parameters. Consult query descriptions and examples to determine the right
input parameters.

## Tool

- **ciq_execute** – Executes a CIQ query. Arguments:
    - `id` (required): The query ID (e.g. a GID or name from the server).
    - `input_params` (optional): JSON object of input parameters (e.g. `ticker`, `customer_external_id`, `user_external_id`).

Example: `{"id": "<query-gid>", "input_params": {"ticker": "AAPL"}}`

## When to use

- User asks for data that comes from a known or discoverable CIQ (stock price, purchase limit, lookups by user/ticker/customer, etc.).
- Do **not** use for maximum shares for a user and ticker—use the **max-purchase-amount** skill instead, which uses ciq_execute internally.

## Workflow

Consider available MCP resources (list_resources) and MCP tools together with this skill. Select the CIQ resource or tool (e.g. ciq_execute) that matches the request and run it with the appropriate id and input_params.
