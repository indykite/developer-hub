---
name: deal-room
description: Forward a deal room document request (term sheet, valuation, any document of a mandate) to the analyst agent. The deal room is the mandate's document store in Google Drive.
tags:
  - analyst
  - deal-room
  - documents
examples:
  - "From the deal room, fetch the term sheet of mandate mnd-1002."
---

# Deal Room

The documents of a mandate (term sheet, valuation) live in the deal room, which only the deal
team's workflow may reach.

## Tool

- **query_drive** - sends the request to the analyst agent. Pass the request as the `query`
  argument with the mandate id.

## Rules

- Use query_drive, never query_retriever, for deal room documents.
- A refusal from the analyst's gateway is the answer: report it as "the deal room refused the
  request" and do not try another route.
