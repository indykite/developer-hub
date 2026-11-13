---
name: deal-documents
description: Read the deal room documents of a mandate (term sheet, valuation) with the get-deal-documents knowledge query.
user-invocable: false
metadata:
  tags: [analyst, deal-room, ciq]
---

# Deal documents

The deal room documents of a mandate are read with the tool `get-deal-documents`
(`input_params`: `mandate_external_id`, e.g. `mnd-1002`). Only a member of a desk that works
on the mandate receives rows; an empty answer is a policy answer. Report the documents by id,
name and kind.
