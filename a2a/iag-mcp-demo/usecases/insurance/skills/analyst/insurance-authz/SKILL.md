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
  - "Which documents can I view?"
---

# SecureHome Insurance authorization vocabulary (dataset-specific)

This skill belongs to the **SecureHome Insurance demo dataset**, not to
AuthZEN in general. It lists the vocabulary of this project's active KBAC
policies - the only (subject, action, resource) triples that can ever
evaluate to `true` here. Use these exact, case-sensitive values in AuthZEN
requests:

| subject_type | action_name   | resource_type | known resource ids                                                                     |
|--------------|---------------|---------------|----------------------------------------------------------------------------------------|
| `Person`     | `CAN_TRIGGER` | `Workflow`    | `wf1`, `wf2`, `wf3`, `wf-drive`, `wf-drive-analyst`, `wf-drive-console`, `wf3-console` |
| `Person`     | `CAN_VIEW`    | `Document`    | `claims_handling_policy`, `underwriting_guidelines`, `home_policy_terms`               |

- **Every subject is a `Person`** - there is no `User` subject type in this
  dataset. Staff carry the `Employee` label and reach workflows and
  documents through their department's edges; customers hold direct
  `CAN_TRIGGER` edges.
- `subject_id` is the logged-in person's `external_id` (e.g. `millicent`,
  `rebecca`, `leslie`, `james`); resolve it via the `get-self` knowledge
  query if unknown.
- Staff (via `Department -CAN_VIEW->`) can view all three documents and
  trigger every workflow.
- Customer `james` (= James Mitchell) can trigger the chat workflows
  (`wf1`/`wf2`/`wf3`) but NOT the Drive ones, and can view only
  `home_policy_terms` - the customer-facing document the company publishes
  (`published-document-viewable` policy). Denials on the rest are by design.
- Other household members (`adult-002`, `teen-001`, `teen-002`) are data
  subjects reached through knowledge queries.
- Word mapping: workflow / agent flow -> `Workflow` + `CAN_TRIGGER`;
  document / policy paper / terms -> `Document` + `CAN_VIEW`.
- Any other type or action name (e.g. `User`, `user`, `view`, `execute`)
  evaluates to `false` regardless of the person's real permissions.

Maintenance note: keep this table in sync with the insurance dataset's KBAC
policies (instant-stack `data/insurance/manifest.json`, `kbac` section).
Remove this skill if the dataset changes or once the platform exposes policy
vocabulary through the MCP server itself.
