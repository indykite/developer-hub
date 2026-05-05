# banking

Banking demo app

## Get started

- clone the repo
- run: `cd banking`

The capture form is exposed at `/api_capture/create` and is pre-populated with
default banking node values so each new configuration can be created by
editing the form and submitting it.

## Requirements

    Environment created on the IndyKite platform: Service Account

## Environment variables

create .env file with the variables:

    SA_TOKEN: SA credentials token obtained on https://eu.hub.indykite.com/service-accounts (or https://us.hub.indykite.com/service-accounts)
    URL_ENDPOINTS: https://eu.api.indykite.com (or https://us.api.indykite.com)
    ORGANIZATION_ID: ID attribute available in Organization > Settings

## Install and run

- install pipenv
- run `pipenv install`
- run `pipenv shell`
- run

      flask run

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

### CIQ slot 9 — Get HQ Weather

A new use-case (Policy 9 + Knowledge Query 9 + Execute 9) reads the `hq_weather` Weather node end-to-end:

- `/api_ciq_policy/create9` — `get-hq-weather` policy.
- `/api_ciq_knowledge_query/create9` — `get-hq-weather` query (returns `weather.{external_id, location, latitude, longitude, current, units}`).
- `/api_ciq_execute/execute9` — runs the query; both EDRs fire and the response contains the live open-meteo block plus the unit labels.

To wire the existing `get-stock-quote` use case (slot 2) end-to-end, create the `stock-quote` resolver (slot 3 above) — the `stock_quote` node already has `"external_value": "stock-quote"` on its `price` property.

### Provisioning order

1. Capture nodes (`/api_capture/create`) and relationships.
2. Create the resolvers you need (`/api_external_data_resolver/create*`).
3. Create the matching policies and knowledge queries.
4. Execute (`/api_ciq_execute/execute*`).
