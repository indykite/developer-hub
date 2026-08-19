---
name: authzen
description: Run AuthZEN (OpenID) authorization requests. This skill matches MCP resources or tools exposed by the backend—use list_resources and the available MCP tools to find the AuthZEN capability (evaluation, evaluations, resource_search, subject_search, action_search) and run the matching resource or tool.
tags:
  - mcp
  - analyst
  - authzen
  - authorization
examples:
  - "Can I trigger workflow wf1?"
  - "Can I trigger this workflow?"
  - "Run an AuthZEN evaluation."
  - "Which resources can I access?"
  - "Which actions can I perform on this resource?"
---

# AuthZEN

Use this skill when the request involves **authorization**. AuthZEN matches
**MCP resources or tools** exposed by the backend. List MCP resources and
inspect available MCP tools to find the AuthZEN capability (as a resource or
tool), then run it. The backend supports five AuthZEN operations: evaluation,
evaluations, resource_search, subject_search, action_search.

## AuthZEN operations (5)

1. **evaluation** – Single authorization check: can this subject perform this action on this resource?
   Example request: `{"subject":{"type":"<SubjectType>","id":"<subject_external_id>"},"action":{"name":"<ACTION_NAME>"},"resource":{"type":"<ResourceType>","id":"<resource_external_id>"}}`
   Response: `{"decision": true}` or `{"decision": false}`.

2. **evaluations** – Batch of evaluation requests (multiple subject-action-resource checks in one call).

3. **resource_search** – Search for resources the subject is authorized to access (e.g. "which records can user X view?"). Needs no resource id - an empty result IS the answer: the user has none they may access.

4. **subject_search** – Search for subjects (e.g. who has access to this resource).

5. **action_search** – Search for actions (e.g. what actions can this subject perform on this resource?).

Use the MCP tools that correspond to these operations (as exposed by the server). If the server exposes a single AuthZEN tool, pass the appropriate request shape for evaluation, evaluations, resource_search, subject_search, or action_search.

## Request vocabulary - never guess it

**First check the skill list for a dataset-specific authorization vocabulary
skill** (tagged `dataset` + `authzen`, e.g. a `<dataset>-authz` skill) and
activate it before composing any request - it lists the exact triples this
project's policies accept.

Decisions are rendered by the project's **KBAC policies**, which define the
exact (subject type, action name, resource type) vocabulary. These are
**case-sensitive knowledge-graph terms** (e.g. `User`, `Workflow`,
`CAN_TRIGGER`), NOT generic REST words - `user`, `view`, `read`, or `execute`
will simply evaluate to `false`. A `false` from a made-up type or action is
indistinguishable from a real denial, so:

- Use type and action names exactly as they appear in the project's policies
  and knowledge graph (node types and relationship-style action names).
  Mentions in the conversation, MCP resources (`list_resources`), and
  knowledge-query results are good sources for the exact spelling.
- `subject_id` and `resource_id` are `external_id` values from the knowledge
  graph. **Never fabricate an id.** If you do not know the current user's
  external_id, first run the self-profile knowledge query (e.g. `get-self`
  via `ciq_execute`) and read it from the result.
- Evaluations carrying the logged-in user's token are **bound to that
  token's subject**: evaluating a *different* subject returns `false` by
  design, regardless of that user's real permissions. If asked about another
  user, explain this instead of evaluating.

## When to use

- User asks "can X do Y on Z?", "is X allowed to ...?", or similar permission questions - use evaluation when you hold a concrete resource id.
- "Why can't I ..." / "which X can I ..." with no concrete id - prefer resource_search.
- User asks for authorized resources, subjects, or actions (search-style AuthZEN requests).

## Workflow

1. Map the user's words onto the project's policy vocabulary; if unsure of a
   type or action spelling, look before guessing (list_resources, prior
   query results, the conversation).
2. Resolve `subject_id` for "I/me" questions via the self-profile query.
3. Select and run the AuthZEN resource or tool that matches the request
   (evaluation, evaluations, resource_search, subject_search, action_search)
   and report the raw decision with a short plain-language explanation.
