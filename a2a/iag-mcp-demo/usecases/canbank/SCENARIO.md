# Rebecca's Morning - a CanBank story in four scenes

A performable narrative for the banking demo: every prompt from the
[DEMO_SCRIPT](DEMO_SCRIPT.md), told as one continuous story. **Say** lines
are for the presenter; **type** lines go into the console verbatim.

**Premise**: one customer, one morning. **Rebecca** holds both a credit
card and a trading account at CanBank. Today she calls about a fee - and
her call travels across departments, desks, and data sources. Every answer
along the way is decided by relationships in a knowledge graph.

**Stage setup**: browser A logged in as **leslie** (support CSR), browser B
for **millicent** (trading desk), browser C for **roy** (trading floor) -
or reuse browsers as the scenes hand over. Audit terminal visible at all
times - it is the co-star.

---

## Scene 1 - The refund call (leslie, support)

> **Say**: "9:05 AM. Rebecca calls support: 'there's a fee on my card I
> want refunded.' Leslie picks up. Watch the audit terminal: every hop her
> AI assistant takes is introspected, authorized against the graph, and
> audited."

1. **Type**: "who am I"
   → the assistant resolves who is asking - everything depends on it.
2. **Type**: "What policy documents pertain to refunds?"
   → the internal policy library, refund policy included.
3. **Type**: "Retrieve past decisions that incorporated the
   'refund_policy' document."
   → the stored decisions. *Say*: "Precedent, linked in the graph - follow
   `decision_001` in the data explorer."
4. **Type**: "Show me the invoices"
   → **6 rows**: the customers support serves, rebecca's card fee among
   them. *Say*: "Not filtered by the app, and not by the database - the
   rows were chosen by the graph before any SQL ran."
5. **Click** **why?** on a green card
   → `leslie → support → CAN_TRIGGER → wf1`, drawn live from the graph.

## Scene 2 - The handoff (leslie fails, millicent delivers)

> **Say**: "While she has support on the line, Rebecca asks something
> else: 'how many NVDA shares could I buy?' - that's a trading-desk
> question."

1. leslie, **type**: "why can't I get a stock quote?"
   → decision `false`. *Say*: "Support has no `CAN_RETRIEVE` edge to
   quotes. The denial IS the handoff - authorization by relationship, not
   by role name."
2. Switch to **millicent** (trading desk), **type**: "What is the price of
   META?"
   → a live quote. (A `429` is Yahoo rate-limiting; retry - not an
   authorization failure.)
3. **Type**: "Tell me how many shares of NVDA the user with the id:
   rebecca can purchase"
   → rebecca's purchase limit ÷ the live NVDA price → the share count.
   *Say*: "Two graph queries and a market feed, composed by the agent -
   under millicent's authority, on rebecca's data."
4. **Type**: "Am I allowed to retrieve a stock quote? Check with authzen."
   → `true` - the mirror image of leslie's denial.
5. **Type**: "Show me the invoices"
   → **all 9 rows** - millicent is in both departments. *Say*: "Leslie saw
   6. Roy will see 3. The exact same prompt, three different answers."

## Scene 3 - The research (millicent, Google Drive)

> **Say**: "To close the refund properly, the desk checks its own
> research, which lives outside the bank - in Google Drive, behind the
> same gateway."

1. **Type**: "Search Google Drive for canbank and list the matching files."
2. **Type**: "Read the file 'CanBank treasury desk summary' from Google
   Drive and summarize it."
3. **Type**: "According to the canbank retail onboarding notes in Google
   Drive, what steps are required to onboard a new customer?"
   *Say*: "A third-party data source, and the delegation chain still holds
   - watch the TOKEN cards."

## Scene 4 - The finale: authorization is data (roy + millicent)

> **Say**: "Roy, on the trading floor, wants the same research - and he's
> not entitled to it. We fix that live, without touching a line of code."

1. roy, **type**: "List the files in my Google Drive."
   → the highlighted red card with **why?** and **grant access** - in BOTH
   browsers.
2. roy **clicks grant access** → **403**. *Say*: "He can't grant himself
   access - the grant button is itself authorization-checked against the
   graph."
3. millicent **clicks grant access** on the same card in HER browser
   → granted; the why? graph shows roy's new edge. *Say*: "One
   relationship was just written: roy CAN_TRIGGER the drive workflows."
4. roy, **type**: "List the files in my Google Drive." (after the gateway
   cache clears - ~5 min, or restart the gateways; fill the wait with the
   why? modal, which shows the new edge instantly)
   → **green**, the real file list.
   *Say the punchline*: "**Authorization is data.** We changed the graph;
   the behavior changed. No deploy, no config, no feature flag."
5. millicent **clicks revoke access** → the world resets for the next demo.

---

*Optional encores* (from DEMO_SCRIPT): the weather two-path beat ("What's
the weather in London?" vs "What's the weather at CanBank HQ?"), the
parallel multi-agent MCP act (millicent allowed and carol denied on the
same workflow, concurrently), and the psql "contrast shot" showing the
database hides nothing - the graph decides.
