---
name: query-erp
description: Route invoice and billing questions (invoices, premiums due, account fees, payment status) to the analyst's ERP backend via the query_erp tool. The rows come back pre-filtered by authorization for the calling user.
tags:
  - erp
  - invoices
  - billing
examples:
  - "Show me the invoices"
  - "Which premiums are still open?"
  - "Show me invoice inv-hi-001"
---

# Query ERP (invoices)

Use this skill when the user asks about **invoices or billing**.

## Tool

- **query_erp** - forwards the question to the analyst, which answers with
  the `erp_*` MCP tools against the ERP backend.

## When to use

- Invoices, billing, premiums due, fees, payment status -> query_erp,
  never query_retriever.
- The rows are pre-filtered by authorization: different users get different
  rows for the same prompt, and that is the point - report what comes back.

## When NOT to use

- Coverage, policies, the household, family members, property, vehicles,
  authorized drivers, documents, "what can X see" - that is knowledge-graph
  data: use query_retriever, never query_erp. "Show my household coverage"
  is a retriever question even though a premium is part of a policy.
- Only route here when the user asks what is owed or was paid: an invoice,
  a bill, an amount due, a payment status.
