# The Storm - a SecureHome story in four scenes

A performable narrative for the insurance demo: every prompt from the
[DEMO_SCRIPT](DEMO_SCRIPT.md), told as one continuous story. **Say** lines
are for the presenter; **type** lines go into the console verbatim.

**Premise**: last night a storm hit Chicago. Water backed up into the
Mitchell family's basement at 123 Oak Avenue. Today, everyone at SecureHome
touches that one event - and every answer they get is decided by
relationships in a knowledge graph, never by app logic.

**Stage setup**: browser A logged in as **millicent** (CSR), browser B for
**rebecca** then **james** (or three browsers/profiles). Audit terminal
visible at all times - it is the co-star.

---

## Scene 1 - The call (millicent, CSR)

> **Say**: "8:45 AM. James Mitchell calls: 'our basement flooded - what
> does our policy cover?' Millicent picks up. Watch the right side of the
> screen: every hop her AI assistant takes is authorized against the graph
> and audited."

1. **Type**: "who am I"
   → the assistant knows who is asking - everything downstream depends on it.
2. **Type**: "James Mitchell (james) is calling about the home insurance for
   123 Oak Avenue. What can he see about the policy?"
   → policy exists, term dates - but **no financials**. *Say*: "The graph
   knows James is not the primary policyholder. It doesn't hide the blind
   spot - it explains it."
3. **Type**: "And what does Sarah Mitchell (adult-002) see?"
   → full details: $450k coverage, deductible, $1,850 premium. *Say*: "Same
   question, different family member, different answer. One edge in the
   graph - PRIMARY_POLICYHOLDER - decides."
4. **Type**: "Why can't James see the premium? Quote our policy terms."
   → the assistant quotes the Home Policy Standard Terms. *Say*: "And the
   rule itself is a document in the graph."
5. **Type**: "Which policy documents am I allowed to see?"
   → all three, including the two internal ones. *Say*: "Remember this
   list - James will ask the same question tonight."
6. **Type**: "Open a Salesforce case for James Mitchell about his
   water-backup claim."
   → Case number + link; **two TOKEN cards** in the terminal. *Say*: "The
   delegation chain crossed into a real third-party SaaS - the case in
   Salesforce records that an agent filed it on millicent's behalf."
7. **Click** **why?** on any green AUTHORIZED card
   → the modal draws `millicent → Customer Support → CAN_TRIGGER → wf1`.
   *Say*: "Not a log. A live query of the graph, right now."

## Scene 2 - The opportunity (rebecca, sales)

> **Say**: "Down the hall, sales looks at the same storm-soaked household
> and sees something else."

1. **Type**: "Which children in our clients' households are approaching
   driving age?"
   → Olivia (16) and Ethan (15) - the Mitchells again.
2. **Type**: "Show me the family overview for james mitchell."
   → both parents, with emails.
3. **Type**: "Who is authorized to drive IL-ABC1234?"
   → James and Sarah; Olivia is the gap - and the pitch.
4. **Type**: "What do our underwriting guidelines say about adding teen
   drivers?"
   → driver-ed requirement, good-student discount - quoted from the
   internal guideline document.
5. **Type**: "Show me the invoices"
   → **5 rows** - and *none* of them overlap millicent's five. *Say*: "Same
   prompt millicent ran. Different department, different `SERVES` edges,
   different rows - the database was never told about any of this."

## Scene 3 - The customer (james, at home)

> **Say**: "That evening, James logs into the portal himself. Same agents,
> same gateways - a different person."

1. **Type**: "Show my household coverage"
   → his family, house, car - and Sarah's policy WITHOUT the financials.
   *Say*: "The blind spot from Scene 1, now experienced first-person."
2. **Type**: "Which policy documents am I allowed to see?"
   → **only one** - the published terms. *Say*: "Millicent saw three. The
   internal ones need a `WORKS_IN` edge James doesn't have."
3. **Type**: "Show me the invoices"
   → exactly **one row**, his own premium.
4. **Type**: "Show me invoice inv-hi-003"
   → **authorization error**. *Say*: "He can name the id. The graph still
   says no - there is no path from James to his neighbor's invoice."
5. **Type**: "Open a Salesforce case about my water-backup claim"
   → red **DENY**. *Say*: "The exact prompt millicent ran this morning.
   Same tool, different person, opposite decision."
6. **Type**: "What's the weather at SecureHome HQ?"
   → works - denials are precise, not blanket.

## Scene 4 - The finale: authorization is data (james + millicent)

> **Say**: "James asks for one thing he's not entitled to - and we fix it
> live, without touching a line of code."

1. james, **type**: "List the files in the Google Drive"
   → the highlighted red card with **why?** and **grant access** - in BOTH
   browsers.
2. james **clicks grant access** → **403**. *Say*: "He can't grant himself
   access - the grant button is itself authorization-checked against the
   graph."
3. millicent **clicks grant access** on the same card in HER browser
   → granted; the why? graph pops open showing the new edge. *Say*: "One
   relationship was just written: james CAN_TRIGGER the drive workflows."
4. james, **type**: "List the files in the Google Drive" (after the
   gateway cache clears - ~5 min, or restart the gateways; fill the wait
   with the why? modal, which shows the new edge instantly)
   → **green**, the real file list.
   *Say the punchline*: "**Authorization is data.** We changed the graph;
   the behavior changed. No deploy, no config, no feature flag."
5. millicent **clicks revoke access** → the world resets for the next demo.

---

*Optional encores* (from DEMO_SCRIPT Act 4): the Drive PDF content prompts
("In the umbrella liability product sheet in Google Drive, are car-sharing
drivers covered?"), the CRM variations, and the psql "contrast shot"
showing the database hides nothing - the graph decides.
