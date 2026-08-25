---
name: ciq-execute
description: Executes specific CIQ knowledge queries. Use this ONLY after receiving a resource list from 'list_resources'. Requires a valid 'id' and 'input_params'.
tags:
  - mcp
  - analyst
  - ciq
  - knowledge-graph
---

# CIQ Execute

Use this skill when the request requires **running a CIQ (Knowledge Query)**. CIQ matches **MCP resources or tools** exposed by the backend. List MCP resources and inspect available
MCP tools to find the CIQ capability (as a resource or the ciq_execute tool), then run it with the correct ID and parameters. Consult query descriptions and examples to determine
the right input parameters.

## Tool

- **ciq_execute** – Executes a CIQ query. Arguments:
    - `id` (required): The query ID (e.g. a GID or name from the server).
    - `input_params` (optional): JSON object of input parameters (e.g. `caller_id`, `license_plate`).

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
- "Can Sarah Mitchell (adult-002) see the insurance coverage details?" -> `{"id": "get-home-insurance-access", "input_params": {"caller_id": "adult-002"}}` - a "can X see the policy/coverage" question is a data question: answer from what the query returns, not via AuthZEN
- `get-home-insurance-access` requires `caller_id` = a Person's `external_id` (e.g. `james`, `adult-002`).
  An address ("123 Oak Avenue"), policy number, or property is NOT a caller_id - never pass one as the parameter.
  If the user asks for coverage details without naming a person, ask whose view they want (e.g. "Sarah, adult-002"
  or "James, james") instead of answering "no data found": in this dataset coverage access is always relative to a person.

## Workflow

Consider available MCP resources (list_resources) and MCP tools together with this skill. Select the CIQ resource or tool (e.g. ciq_execute) that matches the request and run it with the appropriate id and input_params.
