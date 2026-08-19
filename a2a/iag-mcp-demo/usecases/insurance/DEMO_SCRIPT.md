# SecureHome Insurance demo script

The insurance flavor of the demo: same agents, gateways, and authorization
pipeline as the CanBank subject, but a deliberately different story -
**relationship-aware access in a household**, based on the
household-insurance dataset (the Mitchell family and nine other households).

Switch with `./switch-usecase.sh insurance` (relinks `.env` to
`.env.insurance` and recreates the stack; see `usecases/README.md`).

## Prerequisites

1. **Dataset provisioned**: instant-stack `data/insurance` (set
   `DATASET=insurance`) - Mitchell family et al., SecureHome staff users,
   agent workflows, KBAC, the eight CIQ queries (`get-self`,
   `get-home-insurance-access`, `get-family-overview`,
   `get-teens-driving-age`, `get-authorized-drivers`,
   `get-policy-documents`, `get-hq-weather`, `get-my-household`) plus
   `get-agent-workflows` for the gateways, weather resolvers.
2. **PDFs**: the six sample documents in [`documents/`](documents/) split
   into two groups with different homes.

   Three live **in the graph** (Document nodes in the policy library, full
   text embedded as `content_excerpt`, linked via CONTAINS/CLASSIFIED_AS -
   the retriever quotes them through `get-policy-documents`; do NOT upload
   these to Drive):
   - `SecureHome-Claims-Handling-Policy.pdf`
   - `SecureHome-Underwriting-Guidelines.pdf`
   - `SecureHome-Home-Policy-Standard-Terms.pdf`

   Three are **Drive-only** (customer-facing product material for the
   analyst route, independent of the dataset) - upload these to the Drive
   account behind `drive-mcp`:
   - `SecureHome-Auto-Teen-Driver-Product-Guide.pdf`
   - `SecureHome-Umbrella-Liability-Product-Sheet.pdf`
   - `SecureHome-Water-Backup-Flood-Endorsement-Guide.pdf`

## The cast

- **millicent** (login, `User`) - CSR at SecureHome, Customer Support.
- **rebecca** (login, `User`) - Sales & Growth representative.
- **leslie** (login, `User`) - second Customer Support login; interchangeable
  with millicent in Acts 1 and 4.
- **james** (login, `Person`) - James Mitchell, the end customer: engineer,
  pays the mortgage, may trigger the chat workflows but NOT the Drive ones.
  Requires `AUTHZEN_SUBJECT_TYPES=User Person` in `.env`.
- **The rest of the Mitchells** (data, not logins): Sarah (`adult-002`,
  primary policyholder of HI-2024-001), Ethan (15) and Olivia (16);
  123 Oak Avenue, Chicago; a 2022 Honda Accord (IL-ABC1234); neighbor
  Michael Williams (`adult-003`, attorney).

## Act 1 - CSR: the household blind spot (log in as millicent)

1. "who am I" - the retriever resolves the CSR through `get-self`.
2. "James Mitchell (james) is calling about the home insurance for
   123 Oak Avenue. What can he see about the policy?"
   → `get-home-insurance-access`: James sees the policy exists (number,
   type, term dates) but **no financials** - Sarah is the primary
   policyholder. The graph explains the blind spot instead of hiding it.
3. "And what does Sarah Mitchell (adult-002) see?"
   → same query, `caller_id=adult-002`: full details - $450k coverage,
   deductible, $1,850 premium.
4. "Why can't James see the premium? Quote our policy terms."
   → `get-policy-documents`: the retriever quotes the Home Policy Standard
   Terms excerpt ("Full financial details ... are visible only to the
   primary policyholder...").
5. "what are the knowledge queries?"

## Act 2 - Sales: the teen-driver lead (log in as rebecca)

1. "Which children in our clients' households are approaching driving age?"
   → `get-teens-driving-age`: Olivia (16) and Ethan (15) surface as leads.
2. "Show me the family overview for james mitchell."
   → `get-family-overview`: both parents with emails - who to contact.
3. "Who is authorized to drive IL-ABC1234?"
   → `get-authorized-drivers`: James and Sarah today; Olivia is the
   opportunity.
4. "What do our underwriting guidelines say about adding teen drivers?"
   → quotes the Underwriting Guidelines excerpt (driver-ed requirement,
   good-student discount).

## Act 3 - End customer (log in as james = James Mitchell)

1. "Show my household coverage" → `get-my-household` (Person-subject
   query, no params): James sees his family, property, vehicle with its
   authorized drivers, his mortgage side - and Sarah's policy WITHOUT the
   financials. The Act 1 blind spot, experienced first-person.
2. "Who can drive the family car?" - same query's vehicle/driver section.
3. "List the files in the Google Drive" → **NOT AUTHORIZED**: james may
   trigger the chat workflows but not `wf-drive` - watch the red DENY card
   in the audit terminal. Authorization working is best shown by a denial.
4. "What's the weather at SecureHome HQ?" - works: `wf2` is granted.

## Act 4 - Documents and the wider stack (any staff login)

1. "List the files in insurance repo in the Google Drive" - the analyst route lists the three
   SecureHome product PDFs (real files behind the drive-mcp gateway).
2. "What's the weather at SecureHome HQ?" - `get-hq-weather` reads the
   Chicago `hq_weather` node; live conditions via the weather resolvers.
3. "Can I trigger workflow wf1?" - AuthZEN with the `insurance-authz`
   vocabulary.

Watch the audit terminal throughout: every hop shows the gateway decision
(subject → actor, AUTHORIZED / NOT AUTHORIZED, reason) and the exchanged
delegation token for that hop.
