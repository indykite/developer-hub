---
name: web-search
description: Search the web for real-time or external information that cannot be answered from enterprise data. Use only when absolutely necessary—e.g. latest news, current events, live stock market data. Prefer the retriever for data, documents, employees, and authorization.
tags:
  - search
  - web
  - current-events
  - facts
examples:
  - "What's the latest news about X?"
  - "What is the weather in London today?"
---

# Web Search

Use this skill **only when necessary** for information that the data retriever cannot provide: real-time or external facts, news, weather, or other public web data.

## Tool

- **duckduckgo_search** (or equivalent web search tool) – Runs a web search. Use sparingly; prefer query_retriever for enterprise data, documents, employees, and AuthZEN.

## When to use

- User explicitly asks for latest news, current events, or real-time external information.
- The retriever does not have access to the requested data (e.g. live sports scores, weather, breaking news).
- Do **not** use for internal data, documents, employees, or authorization—use query_retriever instead.
