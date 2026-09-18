# Elements

One tiny Flask app per IndyKite platform element. Each app creates exactly one
element from one predefined example, shows the request it sends and the
platform's answer, and nothing else: no provisioning chain, no graph, no data
schema. They exist to record short videos of each step in isolation.

| Folder | Creates | Needs from the platform |
| --- | --- | --- |
| `project/` | a project | an organization id |
| `application/` | an application | a project id |
| `app-agent/` | an application agent | an application id |
| `app-agent-credentials/` | credentials for an agent | an app agent id |
| `token-introspect/` | a token introspect configuration | a project id |
| `authorization-policy/` | a KBAC authorization policy | a project id |
| `ciq-knowledge-query/` | a ContX IQ knowledge query | a project id |
| `ciq-policy/` | a ContX IQ policy | a project id |
| `external-data-resolver/` | an external data resolver | a project id |
| `mcp-server/` | an MCP server configuration | a project id, an app agent id, a token introspect id |
| `authzen/` | nothing: evaluates one AuthZEN decision | an app agent credential |

Every folder is self-contained: its own `app.py`, `manifest.json` with the
one example, `templates/index.html`, `Pipfile`, `.env.example` and
`README.md`. Copy a folder anywhere and it still runs.

## Run one

```bash
cd elements/<folder>
cp .env.example .env        # fill SA_TOKEN, URL_ENDPOINTS and the ids the table lists
pipenv install              # Pipfile per folder, like the other projects here
pipenv run python app.py    # the port is in .env.example, PORT to change it
```

The page shows the target URL, then the example from `manifest.json` in an
editable box with a short explanation of the element and its fields above it,
and a single **Create** button. The answer appears below with the new
element's id on top, ready to paste into the corresponding next mini app's `.env`.

## Conventions

- The manifest holds the example exactly as the API expects it. Fields that
  come from the environment (project id, application id, ...) are filled in
  by the app, not stored in the manifest.
- The app never stores anything. Re-run it and it creates the element again;
  names that must be unique will then fail with the platform's own error,
  which the page shows as-is.
- The explanation above the form lives in `ELEMENT["explanation"]` in each
  `app.py` (an intro, a list of `(field, meaning)` pairs and one guide link);
  `templates/index.html` renders it and stays identical across all folders.
  The text comes from the guides on <https://developer.indykite.com/guides>.
- The same values as instant-stack: `URL_ENDPOINTS` is the config API base
  (for example `https://eu.api.indykite.com`) and `SA_TOKEN` a service
  account token for it.
