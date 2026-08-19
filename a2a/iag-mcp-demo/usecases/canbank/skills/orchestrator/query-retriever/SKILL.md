---
name: query-retriever
description: Forward the user's question to the data retriever agent. Use for MCP, MCP tools or resources, data retrieval, documents, questions about employees, enterprise data, authorization (AuthZEN), knowledge queries, or any internal/knowledge-graph data. The retriever uses an MCP server with access to enterprise data.
tags:
  - retriever
  - mcp
  - data
  - enterprise
  - authzen
examples:
  - "What data do we have about employee X?"
  - "List available MCP resources."
  - "Can Alice view record 109?"
  - "Search documents about project Y."
  - "What is the stock price for AAPL?"
---

# Query Retriever

Use this skill when the user asks for **data that the retriever agent can provide**: MCP resources or tools, documents, employees, enterprise data, AuthZEN authorization, knowledge-graph queries, or any internal data.

## Tool

- **query_retriever** – Sends the user's question to the data retriever agent via A2A and returns the retriever's response. Pass the question as the `query` argument.

## When to use

- User asks about enterprise data, documents, employees, MCP resources, authorization (e.g. "Can X do Y?"), stock/purchase data, or any topic the retriever handles.
- Prefer query_retriever over web search for data, documents, employees, and authorization. Only use web search when the information is real-time or external and cannot be answered from enterprise data.
