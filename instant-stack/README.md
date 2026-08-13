# Instant Stack

Instant Stack provisioning app. A copy of
[`canbank-iag`](../canbank-iag) (itself derived from [`canbank`](../canbank))
whose default data and policies are sourced from the
[`a2a/iag-mcp-demo/bruno`](../a2a/iag-mcp-demo/bruno/iag-demo) collection, so
one app provisions everything the
[IAG MCP demo](../a2a/iag-mcp-demo) needs.

Differences from `canbank`:

- **Data** (`data/iag/nodes.json`, `data/iag/relationships.json`)
  is the union of the Bruno `ingest` folders (`agent-workflow` +
  `demo-data/{canbank,customers,customer-docs}`): the CanBank organization
  plus the agent-workflow graph - users (`millicent`, `carol`, `joe`, …),
  workflows `wf1`–`wf3` and the `indykiteagent*` agent chains ending at
  `indykiteagent-mcp`. The workflow→agent `INVOKES` edges carry
  `workflow_name` (and `discriminating_property: workflow_name` on edges into
  the shared MCP agent), which the Agent Gateway's `get-agent-workflows`
  ContX IQ query (slot 8) filters on at every hop. Wired workflows:
  `wf1` = orchestrator → retriever → MCP, `wf2` = weather → MCP,
  `wf3` = analyst → MCP (millicent only - carol is denied, which the WF4
  parallel-MCP tests exercise). The `hq_weather` node is kept from `canbank`
  so the weather EDRs and CIQ slot 9 still work.
- **CIQ policies and knowledge queries** (slots 1–8) are byte-identical to
  the latest versions in the Bruno collection (`ciq-context/*` and
  `ciq-config/workflows-v2`).
- **AuthZEN defaults** use the Bruno environment's subject (`joe` /
  `CAN_TRIGGER` / `wf1`). Users get workflow access via direct `CAN_TRIGGER`
  edges, and the chatbot demo users additionally via their departments:
  `support` and `trading` have `CAN_TRIGGER -> wf1`, so `leslie` and `roy`
  (through `WORKS_IN`, matched by the KBAC policy's
  `WORKS_IN|CAN_TRIGGER*..3` pattern) can trigger the orchestrator workflow.
  `rebecca` exists as both a `Customer` and a `User` (with a direct
  `CAN_TRIGGER -> wf1`) so she can log in to the chatbot too.
- **Store Decision (slot 10)**: a CIQ *write* not present in `canbank`:
  creates a `Decision` node linked to a `Document` (`APPLIES`), a `Ticket`
  (`CLOSED_BY`) and the acting `User` (`MADE`) in one atomic upsert. It
  provisions the `store-decision` query the `iag-mcp-demo` agents reference
  via `CIQ_QUERY_STORE_DECISION`; stored decisions are readable back through
  *Get Decisions* (slot 7).

## Get started

- clone the repo
- run: `cd instant-stack`

The capture form is exposed at `/api_capture/create` and is pre-populated with
the IAG demo node values so each new configuration can be created by
editing the form and submitting it.

## Requirements

Environment created on the IndyKite platform: Service Account

## Environment variables

create .env file with the variables:

```text
SA_TOKEN: SA credentials token obtained on https://eu.hub.indykite.com/service-accounts (or https://us.hub.indykite.com/service-accounts)
URL_ENDPOINTS: https://eu.api.indykite.com (or https://us.api.indykite.com)
ORGANIZATION_ID: ID attribute available in Organization > Settings
DATASET: which data/<DATASET>/ folder to provision from (optional; defaults to "iag")
```

## Datasets

All of this app's configuration and graph data live under `data/<dataset>/`, one
self-contained folder per dataset:

```text
data/
  iag/                  # the default dataset (DATASET=iag)
    manifest.json       # all Config-API elements: project, application,
                        # app_agent, mcp_server, token_introspect, kbac,
                        # resolvers, ciq_policies, ciq_queries
    nodes.json          # graph nodes
    relationships.json  # graph relationships
```

The `api/*.py` modules read their form defaults from the active dataset's
`manifest.json` via `api/_dataset.py` (they hold no hardcoded config). Select the
dataset with the `DATASET` env var - in `.env` or as a real env var - defaulting
to `iag` when unset. To add another dataset, drop in a new `data/<name>/` folder
with the same three files and set `DATASET=<name>`; no code change is needed
(`api/_dataset.py:available_datasets()` auto-discovers any folder containing a
`manifest.json`). The landing page, forms, execute pages and provisioning all
adapt to the active manifest - any number of queries/policies/resolvers/KBAC
policies. Conventions worth following in a new manifest:

- Give each query/policy/resolver a `slot` (any unique string) - forms are
  reachable as `/api_.../create?slot=<slot>` and provisioning records ids under
  `CIQ_POLICY_ID_<slot>` / `CIQ_QUERY_ID_<slot>` env keys.
- End each query description with an
  `Example: { "id": ..., "input_params": { ... } }` snippet - the execute form
  pre-fills its input from it, and CIQ policies whose `subject.type` is
  `_Application` are offered without a user bearer token.
- `kbac` may be a single policy object or a list; ids are recorded under
  `KBAC_POLICY_ID`, `KBAC_POLICY_ID_2`, ... in manifest order (append new
  policies at the end so existing `.env` keys keep their meaning).

## Install and run

- install pipenv
- run `pipenv install`
- run `pipenv shell`
- run

  ```shell
  flask run
  ```

- open the app by clicking the local url (like [http://127.0.0.1:5000](http://127.0.0.1:5000))

## External Data Resolvers

The app exposes forms to create
[External Data Resolvers (EDRs)](https://developer.indykite.com/guides/guide-external-data-resolver)
that populate node properties from a remote API at query time. Reference a resolver
from a node by setting `"external_value": "<resolver-name>"` on a property instead of
`"value"`.

| Slot | Endpoint | Resolver name | Bound to | Notes |
| --- | --- | --- | --- | --- |
| 1 | `/api_external_data_resolver/create` | `weather` | `hq_weather.current` | `GET https://api.open-meteo.com/v1/forecast?latitude={$latitude}&longitude={$longitude}&current=…`, selector `.current`. Lat/lon come from the `hq_weather` node's `latitude` / `longitude` properties. |
| 2 | `/api_external_data_resolver/create2` | `weather-units` | `hq_weather.units` | Same call, selector `.current_units`; returns the unit labels (°C, km/h, …). |
| 3 | `/api_external_data_resolver/create3` | `stock-quote` | `stock_quote.price` | `GET https://query1.finance.yahoo.com/v8/finance/chart/{$ticker}?interval=1d`, selector `.chart.result[0].meta.regularMarketPrice`. `{$ticker}` is supplied via the knowledge query's `input_params`. |

Each successful create stores the returned resolver id under `EXTERNAL_DATA_RESOLVER_ID_<slot>` in `.env`.

### CIQ slot 9: Get HQ Weather

A new use-case (Policy 9 + Knowledge Query 9 + Execute 9) reads the `hq_weather` Weather node end-to-end:

- `/api_ciq_policy/create9`: `get-hq-weather` policy.
- `/api_ciq_knowledge_query/create9`: `get-hq-weather` query (returns `weather.{external_id, location, latitude, longitude, current, units}`).
- `/api_ciq_execute/execute9`: runs the query; both EDRs fire and the response contains the live open-meteo block plus the unit labels.

To wire the existing `get-stock-quote` use case (slot 2) end-to-end, create the `stock-quote` resolver (slot 3 above) - the `stock_quote` node already has `"external_value": "stock-quote"` on its `price` property.

### Provisioning order

1. Capture nodes (`/api_capture/create`) and relationships.
2. Create the resolvers you need (`/api_external_data_resolver/create*`).
3. Create the matching policies and knowledge queries.
4. Execute (`/api_ciq_execute/execute*`).
