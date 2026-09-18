# App Agent

Creates an application agent with its API permissions. Credentials are a separate element.

## Run

```bash
cp .env.example .env    # fill the values
pipenv install
pipenv run python app.py   # http://localhost:5103
```

## What it sends

`POST {URL_ENDPOINTS}/configs/v1/application-agents` with `Authorization: Bearer <SA_TOKEN>`.

The example plus `application_id` from the environment. `api_permissions` lists what the agent may call.

## What you get back

The app agent id, needed by the credentials and MCP server mini apps.
