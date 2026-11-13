---
name: wall-control
description: The control-room vocabulary and procedure - mandates, companies, the restricted list, trade orders and holds - and which knowledge query serves each task.
user-invocable: false
metadata:
  tags: [retriever, control-room, information-barrier]
---

# Wall control

The caller is the Control Room Sentinel, an autonomous agent. It keeps the information barrier:
a company the bank holds a mandate on is restricted, and orders in a restricted company are
held.

## Ids

| Thing | Id shape | Examples |
| --- | --- | --- |
| Mandate | `mnd-<number>` | `mnd-1001`, `mnd-1002` |
| Company | lowercase name with hyphens (never the ticker) | `halcyon-energy` (HLCN), `brightwater-pharma` (BWPH), `kestrel-logistics` (KSTL), `orchid-semiconductors` (ORCS) |
| Trade order | `ord-<number>` | `ord-7001` |
| Restriction | `rst-<mandate id>` | `rst-mnd-1002` |
| Hold | `hold-<order id>` | `hold-ord-7001` |

## Which tool for which task

| Task | Tool | input_params |
| --- | --- | --- |
| Read a mandate, find its company and region | `get-mandate` | `mandate_external_id` |
| Which companies are restricted | `get-restricted-list` | none |
| Orders in a company | `get-orders-in-company` | `company_external_id` |
| Put a mandate's company on the restricted list | `raise-restriction` | `restriction_id`, `reason`, `mandate_external_id` |
| Hold an order | `hold-order` | `hold_external_id`, `reason`, `order_external_id` |

## What you choose yourself

A task names the records it acts on, not the ones to create. You name those:

| To create | Id | Reason |
| --- | --- | --- |
| Restriction | `rst-<mandate id>` | `Advisory mandate <mandate id>: private-side information held` |
| Hold | `hold-<order id>` | `Company on the restricted list` |

## How tasks are worded

| Task | Calls |
| --- | --- |
| "Put company C on the restricted list. It is the company of mandate M." | `raise-restriction` with `mandate_external_id` M, `restriction_id` `rst-M` and the reason above. One call. |
| "Place a hold on the trade orders. They are the orders of company C." | `get-orders-in-company` for C, then one `hold-order` per order returned. |
| "Place a hold on trade order Y, which is in company C, if that company is on the restricted list." | `get-restricted-list`; if C is in it, `hold-order` for Y; if not, call nothing else and say the order was left unchanged because C is not restricted. |

## Tasks that take several calls

- "List the orders in the company of mandate X and hold each": `get-mandate` (to learn the
  company id), then `get-orders-in-company`, then one `hold-order` per order.
- "Hold order Y": call `hold-order` directly. The graph accepts it only when the order's
  company is restricted; an empty answer means it is not, and nothing was written.

## Answer

State what was read or written with its ids, one line per item. For an empty answer say so and
name the tool that returned it. Do not add advice.
