# SecureHome Insurance demo script

The insurance flavor of the demo: same agents, gateways, and authorization
pipeline as the CanBank usecase, but a deliberately different story -
**relationship-aware access in a household**, based on the
household-insurance dataset (the Mitchell family and nine other households).

Switch with `./switch-usecase.sh insurance` (relinks `.env` to
`.env.insurance` and recreates the stack; see `usecases/README.md`).

## Prerequisites

1. **Dataset provisioned**: instant-stack `data/insurance` (set
   `DATASET=insurance`) - Mitchell family et al., SecureHome staff users,
   agent workflows, KBAC, document `CAN_VIEW` policies (staff-can-view /
   published-document-viewable, both subject `Person`) plus the
   `Department -[CAN_VIEW]-> Document` and `InsuranceCompany -[PUBLISHES]->
   Document` edges they read, the CIQ
   queries (`get-self`, `get-home-insurance-access`, `get-family-overview`,
   `get-teens-driving-age`, `get-authorized-drivers`, `get-policy-documents`,
   `get-hq-weather`, `get-my-household`) plus `get-agent-workflows` for the
   gateways, weather resolvers - and the CRM workflow (`wf-crm`, agent
   `indykiteagent-crm`, `CAN_TRIGGER` from support and sales only).
2. **Salesforce (CRM acts)**: a Developer Edition org with a connected app
   for the OAuth 2.0 JWT Bearer flow (RFC 7523): generate a keypair
   (`openssl req -x509 -newkey rsa:2048 -nodes -keyout crm_agent/keys/sf-jwt.key
   -out sf-jwt.crt -days 365 -subj "/CN=iag-mcp-demo"`), upload `sf-jwt.crt`
   to the connected app, enable OAuth + "Use digital signatures", scope
   "Manage user data via APIs (api)", set the permitted-users policy to
   "Admin approved users are pre-authorized" and assign the integration
   user's profile. Fill `SF_CONSUMER_KEY`, `SF_USERNAME` in `.env`; also
   create the `indykiteagent-crm` IdP client (`CRM_IDP_CLIENT_ID/SECRET`).
3. **PDFs**: the six sample documents in [`documents/`](documents/) split
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

   The `drive-mcp` server extracts PDF text, so the analyst can answer over
   the *contents* of these PDFs (not just list them). This needs the current
   drive-mcp image - rebuild it (`docker build -t drive-mcp ./drive_mcp`) if
   you are on an older build.

## The cast

Every login in this dataset - staff and customer - is a `Person` (the
token introspect binds `ikg_node_type: Person`). Staff are `Person`s who
`WORKS_IN` a department; the customer is a `Person` with none.

- **millicent** (login, `Person`, `WORKS_IN` Customer Support) - CSR.
- **rebecca** (login, `Person`, `WORKS_IN` Sales & Growth) - rep.
- **leslie** (login, `Person`, `WORKS_IN` Customer Support) - second
  support login; interchangeable with millicent in Acts 1 and 4.
- **james** (login, `Person`, no department) - James Mitchell, the end
  customer: engineer, pays the mortgage, may trigger the chat workflows but
  NOT the Drive ones. Every login in this dataset is a `Person`, so set
  `AUTHZEN_SUBJECT_TYPES=Person` in `.env`.
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
5. "Which policy documents am I allowed to see?"
   → the retriever runs an AuthZEN resource search (`authzen_search_resource`,
   action `CAN_VIEW`, resource_type `Document`). millicent is staff, so KBAC
   `staff-can-view-document` authorizes **all three** - Claims Handling
   Policy, Underwriting Guidelines, Home Policy Standard Terms. Note the two
   internal ones. (Contrast with Act 3.)
6. "Open a Salesforce case for James Mitchell about his water-backup claim."
   → the orchestrator routes to the CRM agent through `crm-iag` (`wf-crm`,
   staff-only). Watch the audit terminal: the AUTHORIZED hop, the IndyKite
   delegation TOKEN card, then a **second TOKEN card** - the Salesforce
   access token minted via the OAuth 2.0 JWT Bearer flow (RFC 7523),
   redacted by default (`SF_REPORT_FULL_TOKEN=true` shows it in full). The
   answer carries the Case number and a link; the Case description records
   "Filed on behalf of millicent via agent chain ..." - the delegation
   chain lands in a real third-party SaaS.
7. "what are the knowledge queries?"

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
3. "Which policy documents am I allowed to see?"
   → same AuthZEN resource search (`authzen_search_resource`, subject_type
   `Person`, `CAN_VIEW`, `Document`), now with james as subject. james (a
   `Person`) is not in any department, so `staff-can-view-document` grants
   him nothing; only `published-document-viewable` applies, authorizing
   **only**
   the Home Policy Standard Terms (the doc the company `PUBLISHES`) - NOT the
   internal Claims Handling Policy or Underwriting Guidelines that millicent
   saw in Act 1. **The document blind spot:** "why can't the customer see the
   underwriting guidelines?" - because he has no `WORKS_IN` a department that
   `CAN_VIEW` them. Same documents, same question, different AuthZEN
   decisions - the graph + KBAC decide.
4. "List the files in the Google Drive" → **NOT AUTHORIZED**: james may
   trigger the chat workflows but not `wf-drive` - watch the red DENY card
   in the audit terminal. Authorization working is best shown by a denial.
5. "Open a Salesforce case about my water-backup claim" → **NOT AUTHORIZED**:
   `wf-crm` is staff-only (support/sales hold the `CAN_TRIGGER` edge, james
   does not) - the same prompt millicent ran in Act 1 ends in a red DENY
   here. Same tool, different person, different decision.
6. "What's the weather at SecureHome HQ?" - works: `wf2` is granted.

## Act 4 - Documents and the wider stack (any staff login)

The document story has two corpora - keep them distinct on stage:

- **Graph-embedded policy docs** (internal): claims-handling, underwriting,
  home-policy-terms - queried via `get-policy-documents`, the retriever
  quotes their `content_excerpt`.
- **Drive product PDFs** (customer-facing): the auto/teen-driver, umbrella,
  and water-backup guides - the analyst route reads them through the
  drive-mcp gateway. The gateway now extracts PDF text, so the analyst can
  answer *about the contents*, not just list filenames.

Drive prompts (each content answer comes from the PDF text, read through the
drive-mcp gateway - not just the filename). **Say "in Google Drive"
explicitly** so the orchestrator routes to the analyst/Drive path rather than
the retriever/graph path:

1. **List** - "List the files in the Google Drive" - the analyst route lists
   the three SecureHome product PDFs (real files behind the drive-mcp gateway).
2. **Umbrella / car-sharing** - "In the umbrella liability product sheet in
   Google Drive, are car-sharing drivers covered?" - "named non-resident
   drivers on a car-sharing endorsement are NOT covered by the household
   umbrella".
3. **Umbrella / limits** - "In the umbrella liability product sheet in Google
   Drive, what coverage limits are offered and who should consider it?" - up
   to $5M in $1M steps; households with teen drivers, pools, or car-sharing
   arrangements.
4. **Auto / discount** - "In the auto and teen driver product guide in Google
   Drive, summarize the teen-driver discount." - the good-student discount:
   up to 15% for a GPA of 3.0+, plus driver-ed completion and a named-vehicle
   assignment.
5. **Auto / telematics** - "In the auto and teen driver product guide in
   Google Drive, is there a telematics program for new drivers?" - the
   12-month monitoring program with a safe-driving rebate up to 10%.
6. **Water-backup / flood** - "In the water backup and flood endorsement
   guide in Google Drive, what is the flood endorsement waiting period and
   which flood zone gets the preferred rate?" - a 30-day waiting period; Zone
   X qualifies for the preferred rate.
7. **Bridge (Drive + graph)** - "What internal policy sets our emergency
   mitigation limit, and what does the water-backup guide in Google Drive say
   about it?" - the retriever quotes the internal Claims Handling Policy
   ($5,000 pre-approved) from the graph while the analyst quotes the Drive
   water-backup guide that references the same limit.
8. "What's the weather at SecureHome HQ?" - `get-hq-weather` reads the
   Chicago `hq_weather` node; live conditions via the weather resolvers.
9. "Can I trigger workflow wf1?" - AuthZEN with the `insurance-authz`
   vocabulary.

CRM prompts (Salesforce, staff logins only - `wf-crm`). Each case lands in
the connected dev org with the on-behalf-of person and the agent chain
recorded in the description; watch for the **two** TOKEN cards per run
(IndyKite delegation, then the Salesforce access token from the RFC 7523
exchange):

1. **Claim case** (millicent) - "Open a Salesforce case for James Mitchell
   about his water-backup claim." - the Act 1 beat: case number + Lightning
   link in the answer.
2. **Sales follow-up** (rebecca) - "File a CRM case to follow up on the
   teen-driver quote for Olivia Mitchell." - shows the sales department
   holds `CAN_TRIGGER` on `wf-crm` too, not just support.
3. **Structured subject** (any staff) - "Open a Salesforce case. Subject:
   Policy renewal HI-2024-001. It is about renewing Sarah Mitchell's home
   policy before the term ends." - the orchestrator passes an explicit
   subject line; the case title matches it exactly.
4. **Graph + CRM bridge** (millicent) - "What can James Mitchell (james) see
   about the home insurance? Then open a Salesforce case asking underwriting
   to add him as a named insured." - the retriever answers from the graph,
   then the CRM agent files the follow-up case in the same conversation.
5. **The denial** (james) - "Open a Salesforce case about my water-backup
   claim" - the Act 3 beat: red NOT AUTHORIZED, because no `CAN_TRIGGER`
   path links james to `wf-crm`. Same prompt millicent ran in 1, opposite
   decision - authorization by relationship, not by feature flag.

Watch the audit terminal throughout: every hop shows the gateway decision
(subject → actor, AUTHORIZED / NOT AUTHORIZED, reason) and the exchanged
delegation token for that hop.
