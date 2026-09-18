# Token Introspect

Creates a token introspect configuration: how the platform validates your users' tokens and maps them to graph nodes.

## Run

```bash
cp .env.example .env    # fill the values
pipenv install
pipenv run python app.py   # http://localhost:5105
```

## What it sends

`POST {URL_ENDPOINTS}/configs/v1/token-introspects` with `Authorization: Bearer <SA_TOKEN>`.

The example plus `project_id` from the environment. `offline_validation` is used on purpose: online validation calls the identity provider's userinfo endpoint, which rejects ID tokens.

## What you get back

The token introspect id, needed by the MCP server mini app.
