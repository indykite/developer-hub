---
name: ibac-authz
description: CanBank (ibac dataset) authorization vocabulary - the exact subject/action/resource values of this project's KBAC policies, plus the cast of people (Auth0 subjects) and their customer records. Load together with the authzen skill whenever composing an AuthZEN request or naming a person or customer id, so types, actions and ids are never guessed.
tags:
  - authzen
  - authorization
  - ibac
  - dataset
examples:
  - "Am I allowed to retrieve a stock quote?"
  - "Can I trigger workflow wf1?"
  - "Which invoices can I view?"
  - "Which of my children's accounts may I see?"
---

# CanBank authorization vocabulary (ibac dataset)

This skill belongs to the **ibac dataset** of the CanBank demo, not to
AuthZEN in general. It lists the vocabulary of this project's active KBAC
policies - the only (subject, action, resource) triples that can ever
evaluate to `true` here - and the cast. Use these exact, case-sensitive
values.

## The one subject type: Person

Every login is a `Person` whose `external_id` is the **Auth0 subject** of
their token (`auth0|<24 hex>`). Roles are relationships, not attributes:

- staff `WORKS_IN` a `Department`;
- customers `IS_CUSTOMER` a `Customer` record (`cust-<firstname>`) that the
  core banking system contains and a department `SERVES`;
- parents are `PARENT_OF` their children; everyone `IS_AGE_GROUP` `minor`
  or `adult` (age bands are nodes, so policies can match them).

`subject_id` is always the logged-in person's Auth0 subject - read it from
`get-self` (`subject.external_id`) if unknown. Evaluations carrying the
user's token are bound to that subject: asking about *another* person
answers `false` by design.

## Triples (subject type `Person`)

| action_name | resource_type | known resource ids | who passes |
| --- | --- | --- | --- |
| `CAN_TRIGGER` | `Workflow` | `wf1`, `wf2`, `wf3`, `wf3-console`, `wf-drive`, `wf-drive-analyst`, `wf-drive-console`, `wf-erp`, `wf-erp-analyst`, `wf-erp-console`, `wf-crm` | department or direct `CAN_TRIGGER` edge (the console's grant self-check and why? cards) |
| `CAN_TRIGGER` | `Agent` | the agents' Auth0 subjects (`<client_id>@clients`) | the Agent Gateway's admission check: a workflow the person can trigger whose path of agents equals the delegation chain (`$ik_token.act_chain`); only the gateways call it, with the delegation in `X-IK-Token` |
| `CAN_RETRIEVE`, `GET_PRICE` | `Quote` | `stock_quote` | staff of `trading` |
| `CAN_VIEW` | `Invoice` | `inv-cb-001` ... `inv-cb-009` | staff of the serving department; the customer; a parent of a minor customer |
| `CAN_DISCLOSE` | `Document` | e.g. `refund_policy` | staff, only over the full console→orchestrator→retriever→mcp delegation chain |
| `CAN_DISCLOSE_UNCHAINED`, `CAN_ACCESS_FOLDER_DOCUMENT` | `Document` | documents under the department's folders | staff |
| `CAN_ACCESS` | `Folder` | `cs_policy` | staff of `support` |
| `WORKS_IN`, `WORKS_UNDER` | `Department` | `support`, `trading`, `private`, `risk`, `tech`, `ops`, ... | staff |
| `REPORTS_TO`, `REPORTS_TO_INDIRECTLY`, `CAN_QUERY_PERSON` | `Person` | Auth0 subjects | staff |
| `CAN_LIST_CUSTOMER`, `SERVES` | `Customer` | `cust-*` | staff whose department can access the core system / serves the customer |
| `AFFECTED` | `Account` | accounts behind a decision | the staff member who made it |
| `IS_CUSTOMER`, `CAN_QUERY_CUSTOMER` | `Customer` | `cust-*` | customers |
| `OWNS`, `VIEW_BALANCE` | `Account` | `acc_<firstname>_<type>` | the customer |
| `VIEW_INVOICE`, `VIEW_UNPAID_INVOICE` | `Invoice` | `inv-cb-*` | the customer |
| `HAS_TIER` | `AccountTier` | `tier_1` ... `tier_4` | the customer |
| `VIEW_ACCOUNT_CATEGORY` | `InvestmentCategory` | `cat_stocks`, `cat-lending`, `cat-mutual-fund`, ... | the customer |
| `VIEW_PRODUCT_DOCUMENT` | `Document` | documents classified under the customer's categories | the customer |
| `VIEW_TICKET_DECISION` | `Decision` | `decision_001` | the customer who opened the ticket |
| `PARENT_OF`, `HOUSEHOLD_WITH`, `GUARDIAN_OF` | `Person` | Auth0 subjects | family; `GUARDIAN_OF` only for minor children |
| `VIEW_DEPENDENT_ACCOUNT` | `Account` | a minor child's accounts | the parent |

Parameterised twins (the platform refuses the request if the parameter is
missing): `WORKS_UNDER_DEPARTMENT` needs `input_params.department`,
`CAN_ACCESS_CUSTOMER` needs `input_params.system` (`sys_canbank_core`),
`HAS` needs `input_params.tier`. Without a value, use `WORKS_UNDER`,
`CAN_LIST_CUSTOMER` or `HAS_TIER` instead.

Word mapping: quote / stock price → `Quote` + `CAN_RETRIEVE`; workflow /
agent flow → `Workflow` + `CAN_TRIGGER`; invoice / bill / fee → `Invoice` +
`CAN_VIEW`; my child's account → `Account` + `VIEW_DEPENDENT_ACCOUNT`.
Any other type or action (`User`, `user`, `view`, `read`, `execute`)
evaluates to `false` regardless of the person's real permissions.

## The cast

| person | Auth0 subject | role | customer record |
| --- | --- | --- | --- |
| Millicent Contextsworth | `auth0\|69c410bfb501d3a02bd01ea2` | CSR 2, `support` + `trading`; direct grants on wf1, wf3, wf3-console, wf-drive* | - |
| Vivienne Boardroom | `auth0\|69c40d640ba356c7db9f8c8d` | Customer Service Supervisor, `support`; made `decision_001` | - |
| Cookie Malhotra | `auth0\|69c3ec26b8338328f1511c88` | Trader, `trading`; also a customer; mother of Cashmere | `cust-cookie` |
| Preston Capitalworth | `auth0\|69c40e7a92e98aebbeafe223` | Private Banker, `private`; father of Synergy and Quarterly-Returns | - |
| Mild Concern | `auth0\|69c408f6e7fb3585804a7077` | Risk Analyst, `risk` | - |
| BetaTest McAllister | `auth0\|69c40c7b92e98aebbeafe019` | QA Lead, `tech` | - |
| ApiCall Jackson-Lee | `auth0\|69c40e05815a0ecf477c134b` | Integration Engineer, `tech` | - |
| Marmaduke Doomington | `auth0\|69c4129b0ba356c7db9f91ab` | Operations Officer, `ops`; direct grant on wf1 only; customer; father of Berengar (14) and Humperdinck (15) | `cust-marmaduke` |
| Bambi Stardust | `auth0\|69c407f3b501d3a02bd0151e` | customer (support) | `cust-bambi` |
| Cashmere WiFi Malhotra (20) | `auth0\|69c413eab4187d1438819098` | customer (trading); opened `ticket_001` | `cust-cashmere` |
| Cornelius Pickleworth (14) | `auth0\|69c3ee8cb9ed562744ff9326` | minor customer (support) | `cust-cornelius` |
| Eustorgio Rodriguez (15) | `auth0\|69c41000db4ea22197e9cbf2` | minor customer (support) | `cust-eustorgio` |

Relatives without a login (synthetic subjects, same shape) hold youth
accounts or family edges: Tokenina (11, Millicent's daughter, `cust-tokenina`),
Berengar and Humperdinck (`cust-berengar`, `cust-humperdinck`), Synergy and
Quarterly-Returns Capitalworth (17, `cust-synergy`, `cust-quarterly`),
Percival Contextsworth, Harshvardhan Malhotra, Beulah Doomington, Mortimer
and Wilfred Pickleworth, Filogonio and Petronila Rodriguez, Major Issue,
Murong Feixue Tianyu.

Maintenance note: keep these tables in sync with instant-stack
`data/ibac/manifest.json` (`kbac` section) and `nodes.json`.
