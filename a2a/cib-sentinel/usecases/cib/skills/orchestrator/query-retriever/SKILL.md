---
name: query-retriever
description: Forward a control-room task to the data retriever agent. Use for mandates, companies, the restricted list, restrictions, trade orders and holds - every read or write of the knowledge graph.
tags:
  - retriever
  - mcp
  - control-room
examples:
  - "Read mandate mnd-1001: which company does it concern?"
  - "Put the company of mandate mnd-1002 on the restricted list."
  - "List the trade orders in brightwater-pharma and place a hold on each."
  - "Which companies are restricted?"
---

# Query Retriever

Use this skill for every task about **mandates, companies, the restricted list, restrictions,
trade orders or holds**. The retriever reads and writes the knowledge graph through the MCP
gateway.

## Tool

- **query_retriever** - sends the task to the retriever agent and returns its answer. Pass the
  task as the `query` argument, **word for word, with every id it names** (mandate ids such as
  `mnd-1002`, order ids such as `ord-7001`, restriction and hold ids). Do not shorten it.

## Rules

- One call per task. Return the retriever's answer unchanged, including an empty answer or a
  refusal: both are results the Sentinel needs to see.
- Do not use web search for anything about the bank.
- Deal room documents (term sheets, valuations) are not in the retriever: see **deal-room**.
