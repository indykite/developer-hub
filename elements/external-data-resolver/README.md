# External Data Resolver

Creates an external data resolver: an HTTP call the graph runs at query time to fill a property from an outside API.

## Run

```bash
cp .env.example .env    # fill the values
pipenv install
pipenv run python app.py   # http://localhost:5109
```

## What it sends

`POST {URL_ENDPOINTS}/configs/v1/external-data-resolvers` with `Authorization: Bearer <SA_TOKEN>`.

The example plus `project_id` from the environment. `{$param || default}` in the URL is filled from the query's input params at run time.

## What you get back

The resolver id. A node property refers to it by name through `external_value`.
