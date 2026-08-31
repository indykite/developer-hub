---
name: canbank-authz
description: CanBank dataset authorization vocabulary - the exact subject/action/resource values defined by this project's KBAC policies. Load together with the authzen skill whenever composing an AuthZEN request in the CanBank demo, so types, actions, and ids are never guessed.
tags:
  - authzen
  - authorization
  - canbank
  - dataset
examples:
  - "Am I allowed to retrieve a stock quote?"
  - "Can I trigger workflow wf1?"
  - "Which workflows can I trigger?"
  - "Who can retrieve the stock quote?"
---

# CanBank authorization vocabulary (dataset-specific)

This skill belongs to the **CanBank demo dataset**, not to AuthZEN in
general. It lists the vocabulary of this project's active KBAC policies -
the only (subject, action, resource) triples that can ever evaluate to
`true` here. Use these exact, case-sensitive values in AuthZEN requests:

| subject_type | action_name    | resource_type | known resource ids                     |
|--------------|----------------|---------------|----------------------------------------|
| `User`       | `CAN_TRIGGER`  | `Workflow`    | `wf1`, `wf2`, `wf3`, `wf-drive`        |
| `User`       | `CAN_RETRIEVE` | `Quote`       | `stock_quote`                          |
| `User`       | `CAN_VIEW`     | `Invoice`     | `inv-cb-001` ... `inv-cb-009`          |

- `subject_id` is the logged-in user's `external_id` (e.g. `millicent`);
  resolve it via the `get-self` knowledge query if unknown.
- Word mapping: quote / stock price → `Quote` + `CAN_RETRIEVE`;
  workflow / agent flow → `Workflow` + `CAN_TRIGGER`.
- Any other type or action name (e.g. `user`, `stock`, `view`, `execute`)
  evaluates to `false` regardless of the user's real permissions.

Maintenance note: keep this table in sync with the project's KBAC policies
(instant-stack `data/canbank/manifest.json`, `kbac` section). Remove this skill if
the dataset changes or once the platform exposes policy vocabulary through
the MCP server itself.
