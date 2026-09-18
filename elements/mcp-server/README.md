# MCP Server

Creates the MCP server configuration that binds an app agent and a token introspect to the project's MCP endpoint.

## Run

```bash
cp .env.example .env    # fill the values
pipenv install
pipenv run python app.py   # http://localhost:5110
```

## What it sends

`POST {URL_ENDPOINTS}/configs/v1/mcp-servers` with `Authorization: Bearer <SA_TOKEN>`.

The example plus the three ids from the environment. Empty fields are left out, as the API rejects them.

## What you get back

The MCP server id. The endpoint itself is the regional MCP host plus `/mcp/v1/<project id>`.
