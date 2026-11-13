---
name: ciq-execute
description: Runs a knowledge query through the IndyKite MCP server. Each query is its own MCP tool, named after the query (get-mandate, raise-restriction, hold-order, ...); call that tool with its input_params.
user-invocable: false
metadata:
  tags: [mcp, retriever, ciq, knowledge-graph]
  depends_on: "list_resources"
---

# CIQ Execute

The IndyKite MCP server exposes **one tool per knowledge query, named after the query**. Your
bound tools hold exactly the ones the Agent Gateway permits for this workflow:
`get-mandate`, `get-restricted-list`, `get-orders-in-company`, `raise-restriction`,
`hold-order`. A query you do not see among your tools is not available to this caller: do not
try to reach it another way, say that it is not available.

## Tools

- **`<query-name>`** - runs that query. Arguments:
    - `input_params`: JSON object of the query's parameters, as its description lists them.
- **`read_resource`** on `indykite://knowledge-queries/` lists the queries with their
  parameters when a tool description is not enough.

## Workflow

1. Pick the tool named after the query the task needs; read its description for the parameters.
2. Call it with `input_params`. An **empty answer is a policy answer**: the caller is not
   allowed to see or write those rows. Report it as "No data found (the graph does not allow
   this for the caller)"; never retry with other ids.
3. For a write (`raise-restriction`, `hold-order`), report the ids that were written.
4. When a tool answers an **error**, stop: do not call it again with other ids or values. Report the
   error **word for word** between quotes, with the tool name and the `input_params` you sent.

See the **wall-control** skill for the ids and the order of the calls.
