---
name: ciq-execute
description: Executes specific CIQ knowledge queries. Use this ONLY after receiving a resource list from 'list_resources'. Requires a valid 'id' and 'input_params'.
user-invocable: false
metadata:
  tags: [mcp, analyst, ciq, knowledge-graph]
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

- User asks for data that comes from a known or discoverable CIQ (household insurance access, family overview, teen-driver leads, authorized drivers, policy documents, etc.).

### examples

- "Who am I?" -> `{"id": "get-self", "input_params": { }}`
- "What can James Mitchell (james) see about the home insurance?" -> `{"id": "get-home-insurance-access", "input_params": {"caller_id": "james"}}`
- "Show the Mitchell family overview" -> `{"id": "get-family-overview", "input_params": {"caller_id": "james"}}`
- "Which children are approaching driving age?" -> `{"id": "get-teens-driving-age", "input_params": { }}`
- "Who is authorized to drive IL-ABC1234?" -> `{"id": "get-authorized-drivers", "input_params": {"license_plate": "IL-ABC1234"}}`
- "What policy documents do we have?" -> `{"id": "get-policy-documents", "input_params": { }}`
- "Show my household coverage" (logged-in customer, e.g. james) -> `{"id": "get-my-household", "input_params": { }}`

## Workflow

Consider available MCP resources (list_resources) and MCP tools together with this skill. Select the CIQ resource or tool (e.g. ciq_execute) that matches the request and run it with the appropriate id and input_params.
