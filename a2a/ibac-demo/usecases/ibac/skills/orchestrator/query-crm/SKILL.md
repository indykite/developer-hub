---
name: query-crm
description: File a support case in Salesforce (the CRM) on behalf of a person, via the CRM agent. Use for opening/filing a case or ticket in Salesforce or the CRM. Staff-only workflow (wf-crm) - a customer's request is denied by the gateway, which is by design.
tags:
  - crm
  - salesforce
  - case
  - ticket
  - delegation
examples:
  - "Open a Salesforce case for Cashmere WiFi Malhotra about her late payment fee"
  - "File a CRM case to follow up on Bambi Stardust's mortgage rate question"
  - "Create a support case for the credit card dispute of cust-cookie"
---

# Query CRM (Salesforce cases)

Use this skill when the user asks to **open or file a case/ticket in
Salesforce or the CRM**, typically on behalf of a customer.

## Tool

- **query_crm** – Sends the case request to the CRM agent via A2A. The agent
  performs a second token exchange (OAuth 2.0 JWT Bearer, RFC 7523) for a
  Salesforce access token, creates the Case, and answers with the case number
  and link.

## Composing the argument

Compose the `query` argument deterministically (the CRM agent has no LLM):

```text
subject: <short case title>
description: <what happened / what is needed, including who the case is
for, e.g. "on behalf of Cashmere WiFi Malhotra (cust-cashmere)">
```

Keep the subject under one line; put every other detail the user gave into
the description. Do not invent details the user did not state.

## When to use

- "Open/file/create a case or ticket in Salesforce / the CRM" - use
  query_crm, never query_retriever.
- The workflow behind this tool (`wf-crm`) is **staff-only**: `support` and
  `trading` hold `CAN_TRIGGER` on it. When a customer login asks for it, the
  gateway denies the hop (red card in the audit terminal). That denial is
  the expected demo behavior - report it, do not retry through other tools.
