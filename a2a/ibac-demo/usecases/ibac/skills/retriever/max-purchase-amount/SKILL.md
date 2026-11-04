---
name: max-purchase-amount
description: Compute the maximum number of shares a user can buy for a given ticker. Use when the user asks for "max purchase amount", "how many shares can X buy", or "purchase limit divided by stock price" for a user and ticker.
tags:
  - mcp
  - retriever
  - trading
  - demo
examples:
  - "What is the maximum number of shares customer cust-cashmere can buy of NVDA?"
  - "How many shares can the customer cust-cookie buy of AAPL?"
  - "Max purchase amount for ticker MSFT and customer cust-cashmere."
---

# Max Purchase Amount

Use this skill when the user asks for the **maximum number of shares** a given user can buy for a given stock ticker.

## What it does

1. Fetches the current stock price for the ticker via CIQ (stock price query).
2. Fetches the user's purchase limit (tier threshold) via CIQ (purchase limit query).
3. Returns `floor(purchase_limit / stock_price)` as the maximum number of shares.

## Tool

- **max_purchase_amount** – Takes `user_id` (the Customer record's external ID, `cust-<firstname>`) and `ticker` (stock symbol). Returns an integer string.

## When to use

- User explicitly asks for "max purchase amount", "maximum shares", or "how many shares can [user] buy" for a ticker.
- Do not use for generic "stock price" or "purchase limit" questions; use MCP CIQ tools or list/read resources instead.
