---
name: insurance-authz
description: SecureHome Insurance dataset authorization vocabulary - the exact subject/action/resource values defined by this project's KBAC policies. Load together with the authzen skill whenever composing an AuthZEN request in the SecureHome demo, so types, actions, and ids are never guessed.
tags:
  - authzen
  - authorization
  - insurance
  - dataset
examples:
  - "Can I trigger workflow wf1?"
  - "Which workflows can I trigger?"
  - "Am I allowed to run the drive workflow?"
---

# SecureHome Insurance authorization vocabulary (dataset-specific)

This skill belongs to the **SecureHome Insurance demo dataset**, not to
AuthZEN in general. It lists the vocabulary of this project's active KBAC
policies - the only (subject, action, resource) triples that can ever
evaluate to `true` here. Use these exact, case-sensitive values in AuthZEN
requests:

| subject_type | action_name    | resource_type | known resource ids                     |
|--------------|----------------|---------------|----------------------------------------|
| `User`       | `CAN_TRIGGER`  | `Workflow`    | `wf1`, `wf2`, `wf3`, `wf-drive`        |
| `Person`     | `CAN_TRIGGER`  | `Workflow`    | `wf1`, `wf2`, `wf3` (NOT `wf-drive`)   |

- `subject_id` is the logged-in staff user's `external_id` (e.g.
  `millicent`, `rebecca`); resolve it via the `get-self` knowledge query if
  unknown.
- A logged-in household customer (e.g. `james` = James Mitchell) is a
  `Person` subject: allowed to trigger the chat workflows, denied the
  Drive ones by design.
- Other household members (`adult-002`, `teen-001`, `teen-002`) are
  `Person` data subjects reached through knowledge queries.
- Word mapping: workflow / agent flow → `Workflow` + `CAN_TRIGGER`.
- Any other type or action name (e.g. `user`, `policy`, `view`, `execute`)
  evaluates to `false` regardless of the user's real permissions.

Maintenance note: keep this table in sync with the insurance dataset's KBAC
policies (instant-stack `data/insurance/manifest.json`, `kbac` section).
Remove this skill if the dataset changes or once the platform exposes policy
vocabulary through the MCP server itself.
