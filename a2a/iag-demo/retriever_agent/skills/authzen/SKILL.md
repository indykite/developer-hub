---
name: authzen
description: Run AuthZEN (OpenID) authorization requests. This skill matches MCP resources or tools exposed by the backend—use list_resources and the available MCP tools to find the AuthZEN capability (evaluation, evaluations, resource_search, subject_search, action_search) and run the matching resource or tool.
tags:
  - mcp
  - retriever
  - authzen
  - authorization
examples:
  - "Can user X do Y on resource Z?"
  - "Is alice allowed to view record 109?"
  - "Run an AuthZEN evaluation."
  - "Search for resources user X can access."
  - "Which actions can subject S perform?"
---

# AuthZEN

Use this skill when the request involves **authorization**. AuthZEN matches
**MCP resources or tools** exposed by the backend. List MCP resources and
inspect available MCP tools to find the AuthZEN capability (as a resource or
tool), then run it. The backend supports five AuthZEN operations: evaluation,
evaluations, resource_search, subject_search, action_search.

## AuthZEN operations (5)

1. **evaluation** – Single authorization check: can this subject perform this action on this resource?  
   Example request: `{"subject":{"type":"user","id":"alice"},"action":{"name":"view"},"resource":{"type":"record","id":"109"}}`  
   Response: `{"decision": true}` or `{"decision": false}`.

2. **evaluations** – Batch of evaluation requests (multiple subject-action-resource checks in one call).

3. **resource_search** – Search for resources the subject is authorized to access (e.g. "which records can user X view?").

4. **subject_search** – Search for subjects (e.g. who has access to this resource).

5. **action_search** – Search for actions (e.g. what actions can this subject perform on this resource?).

Use the MCP tools that correspond to these operations (as exposed by the server). If the server exposes a single AuthZEN tool, pass the appropriate request shape for evaluation, evaluations, resource_search, subject_search, or action_search.

## When to use

- User asks "can X do Y on Z?", "is X allowed to ...?", or similar permission questions.
- User asks for authorized resources, subjects, or actions (search-style AuthZEN requests).

## Workflow

Consider available MCP resources (list_resources) and MCP tools together with this skill. Select and run the AuthZEN resource or tool that matches the request (evaluation, evaluations, resource_search, subject_search, or action_search).
