---
name: erp-invoices
description: Query the ERP invoices backend (erp_* tools) - premium invoices whose rows are pre-filtered per caller by AuthZEN search/resource over the knowledge graph. Use for invoices, billing, premiums due, payment status.
tags:
  - erp
  - invoices
  - billing
  - authorization
examples:
  - "Show me the invoices"
  - "Which premiums are still open?"
  - "Show invoice inv-hi-001"
---

# ERP invoices (graph-filtered rows)

Use this skill for **invoice and billing questions**. The ERP backend is a
plain Postgres database behind its own gateway; before any SQL runs, the
server asks IndyKite AuthZEN (`search/resource`) which `Invoice` ids the
calling person may `CAN_VIEW`, and only those rows come back.

## Tools

- **erp_list_invoices** - all invoices visible to the caller (no arguments).
- **erp_get_invoice** - one invoice by id; denied ids return an authorization
  error, not an empty row.

## Rules

- The returned rows are the caller's COMPLETE visible set - present exactly
  what comes back and **never speculate about rows that might be hidden**.
- Different logins legitimately see different rows (staff see the households
  their department serves; a customer sees only their own). A small result
  is not an error.
- Do not answer invoice questions from knowledge-graph tools; the graph holds
  only the authorization edges, the ERP holds the rows.
