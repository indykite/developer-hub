---
name: retriever
description: General retriever behavior when no specific tool or skill is required. Use for simple replies, clarifications, or when the user message does not match MCP resources, CIQ, max purchase amount, or AuthZEN.
tags:
  - retriever
  - demo
examples:
  - "Hello"
  - "What can you do?"
  - "Explain how you work."
---

# Retriever (general)

Use this skill when the user message does not require MCP tools, CIQ, max_purchase_amount, or AuthZEN.

## When to use

- Greetings, meta-questions ("what can you do?"), or clarifications.
- When no data lookup, resource read, or authorization check is needed.
- Reply directly without calling tools. If no tool or resource is found for a data request, say "No tool or resource found." If authorization fails, say "Authorization evaluation failed." If no data is found, say "No data found."
