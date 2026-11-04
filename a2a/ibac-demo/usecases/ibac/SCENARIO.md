# Cashmere's late fee - a CanBank story in five scenes

A performable narrative for the ibac demo: the prompts of the
[DEMO_SCRIPT](DEMO_SCRIPT.md) told as one morning. **Say** lines are for the
presenter; **type** lines go into the console verbatim.

**Premise**: Cashmere WiFi Malhotra, 20, holds an investing account and a
credit card at CanBank. A late fee landed on the card. Her mother Cookie
happens to be a trader at the bank. Everyone involved logs in with Auth0;
everything they see is decided by relationships in a knowledge graph -
department, customer record, family, age.

**Stage setup**: browser A logged in as **Vivienne** (support supervisor),
browser B for **Millicent** (support + trading desk), browser C for
**Cookie** (trading floor, and a mother); customers Cashmere, Cornelius
and Marmaduke take turns in a private window. Audit terminal visible at all
times - it is the co-star.

---

## Scene 1 - The refund call (Vivienne, support)

> **Say**: "9:05 AM. Cashmere calls support: 'there's a fee on my card I
> want refunded.' Vivienne picks up. Watch the audit terminal: every hop her
> assistant takes is introspected through Auth0, delegated by the Token
> Service, authorized against the graph, and audited."

1. **Type**: "who am I" → the assistant resolves who is asking.
2. **Type**: "What policy documents pertain to refunds?"
3. **Type**: "Retrieve past decisions that incorporated the 'refund_policy'
   document." → `decision_001`: Vivienne herself waived Cashmere's fee last
   time. *Say*: "Precedent, linked in the graph."
4. **Type**: "Show me the invoices" → **6 rows**, the customers support
   serves. *Say*: "Not filtered by the app, not by the database - by the
   graph, before any SQL ran."
5. **Click** **why?** on a green card → `Vivienne → support → CAN_TRIGGER →
   wf1`.

## Scene 2 - The handoff (Vivienne fails, Millicent delivers)

> **Say**: "While she has support on the line, Cashmere asks: 'how many
> NVDA shares could I buy with my limit?' That's a trading-desk question."

1. Vivienne, **type**: "why can't I get a stock quote?" → `false`. *Say*:
   "Support has no `CAN_RETRIEVE` edge to quotes. The denial IS the
   handoff."
2. Millicent, **type**: "What is the price of META?" → a live quote.
3. **Type**: "Tell me how many shares of NVDA the customer cust-cashmere
   can purchase" → tier limit ÷ live price. *Say*: "Two graph queries and
   a market feed, under Millicent's authority, on Cashmere's data."
4. **Type**: "Show me the invoices" → **all 9**. *Say*: "Vivienne saw 6.
   Cookie will see 3. Same prompt, three answers."

## Scene 3 - The research and the grant (Millicent, Cookie)

> **Say**: "The desk keeps its research in Google Drive, behind the same
> gateway. Cookie, on the trading floor, wants it too - and isn't entitled."

1. Millicent, **type**: "Search Google Drive for canbank and list the
   matching files."
2. Cookie, **type**: "List the files in my Google Drive." → red card with
   **why?** and **request access** - in both browsers.
3. Cookie clicks **request access**; Millicent clicks **grant access** on
   the same card. *Say*: "One relationship was just written."
4. Cookie, ~30 s later, **type** the same → **green**. *Say the punchline*:
   "**Authorization is data.** We changed the graph; the behavior changed."
5. Millicent clicks **revoke access** → the world resets.

## Scene 4 - The customers (Cashmere, Cornelius, Marmaduke)

> **Say**: "Now the other side of the counter. Customers log in with the
> same Auth0 accounts - and the graph knows who is whose."

1. Cashmere, **type**: "Show me the invoices" → **1 row**, hers. **Type**:
   "Show me invoice inv-cb-001" → denied. *Say*: "Knowing an id buys
   nothing; the graph must connect you to it."
2. Cornelius (14), **type**: "Show me the invoices" → his one paid invoice.
   **Type**: "What is the price of META?" → denied: no department.
3. Marmaduke (father of two minors), **type**: "Show my family" → Berengar
   and Humperdinck with their youth accounts. **Type**: "Show me the
   invoices" → **2 rows**: his own and Berengar's. *Say*: "The second row
   comes through `PARENT_OF` plus `IS_AGE_GROUP minor` - a guardian policy,
   not a department."
4. Cookie, **type**: "Show my family" → Cashmere, an adult, **no**
   accounts. *Say*: "Same policy, opposite answer: her daughter is twenty."

## Scene 5 - The case (Millicent, then Cornelius)

> **Say**: "To close the refund properly, the bank files a case in its CRM."

1. Millicent, **type**: "Open a Salesforce case for Cashmere WiFi Malhotra
   about her late payment fee." → the AUTHORIZED hop on `wf-crm`, the
   delegation TOKEN card, then the **Salesforce** TOKEN card. *Say*: "The
   delegation chain lands in a real third-party SaaS, with Millicent as
   subject and the agents as actors."
2. Cornelius, **type**: "Open a Salesforce case about the late fee on my
   credit card" → red. *Say*: "Staff file cases; customers ask. The
   workflow itself is a node in the graph, and he has no path to it."

---

*Optional encores* (from DEMO_SCRIPT): the weather two-path beat, the
parallel multi-agent MCP act (Millicent allowed and Marmaduke denied on
`wf3`, concurrently), the psql contrast shot, and - once the Intent Agent is
wired - the gateway log showing what each prompt *meant* before it was
forwarded.
