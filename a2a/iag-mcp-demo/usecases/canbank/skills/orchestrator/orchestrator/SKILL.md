---
name: orchestrator
description: General orchestrator behavior when no tool is required. Use for greetings, meta-questions, or when the user message does not need the retriever or web search.
tags:
  - orchestrator
  - demo
examples:
  - "Hello"
  - "What can you do?"
  - "Explain how you work."
---

# Orchestrator (general)

Use this skill when the user message does not require querying the retriever or searching the web.

## When to use

- Greetings, meta-questions ("what can you do?", "how does this work?"), or clarifications.
- Reply directly without calling any tool. If the user asks for data or authorization, use query-retriever instead. If the user asks for real-time external information, consider web-search.
