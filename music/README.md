# music

Music demo app — built from the `music-dataset` Postman collection, modeled after `canbank`.

## Get started

- clone the repo
- run: `cd music`

The capture form is exposed at `/api_capture/create` and is pre-populated with
the default music nodes (artists, tracks, albums, people, venues, …) so each new
configuration can be created by editing the form and submitting it.

## Requirements

    Environment created on the IndyKite platform: Service Account

## Environment variables

create .env file with the variables:

- `SA_TOKEN` — SA credentials token obtained on
  [eu.hub.indykite.com/service-accounts](https://eu.hub.indykite.com/service-accounts)
  (or [us.hub.indykite.com/service-accounts](https://us.hub.indykite.com/service-accounts))
- `URL_ENDPOINTS` — `https://eu.api.indykite.com` (or `https://us.api.indykite.com`)
- `ORGANIZATION_ID` — ID attribute available in Organization > Settings
- `USER_TOKEN` — optional Auth0 ID token; only needed for person-subject CIQ
  executes and `/chat/`

These are the only values you type in yourself. Everything else in `.env`
(`PROJECT_ID`, `APP_TOKEN`, all the `*_ID` keys, …) is saved automatically by
the steps below.

## Install and run

- install pipenv
- run `pipenv install`
- run `pipenv shell`
- run

      flask run

- open the app by clicking the local url (like [http://127.0.0.1:5000](http://127.0.0.1:5000))

## Getting Started steps (1–5)

The five cards on the landing page set up the platform environment. Each one
needs the values saved by the previous steps and saves its own result to
`.env`:

| # | Step | Page | Needs | Saves to `.env` |
| --- | --- | --- | --- | --- |
| 1 | Create Project | `/api_project/create` | `SA_TOKEN`, `URL_ENDPOINTS`, `ORGANIZATION_ID` | `PROJECT_ID` |
| 2 | Create Application | `/api_application/create` | + `PROJECT_ID` | `APPLICATION_ID` |
| 3 | Create App Agent | `/api_app_agent/create` | + `APPLICATION_ID` | `APP_AGENT_ID`, `APP_TOKEN` |
| 4 | Token Introspect | `/api_token_introspect/create` | + `PROJECT_ID` | `TOKEN_INTROSPECT_ID` |
| 5 | MCP Server | `/api_mcp_server/create` | + `APP_AGENT_ID`, `TOKEN_INTROSPECT_ID` | `MCP_SERVER_ID` |

> **Token Introspect uses offline validation.** The config is created with
> `offline_validation: {}` (not `online_validation`). The Auth0 user tokens in this
> demo are **ID tokens** (`aud` = the client ID), and Auth0's `/userinfo` endpoint
> which `online_validation` calls, rejects ID tokens with `401 "Invalid token type"`,
> so person-subject CIQ executes fail with `Invalid token in Authorization header`.
> Offline validation verifies the token's signature against the
> issuer's JWKS instead, so the ID token is accepted.

## One-click provisioning

Once steps 1–5 exist, `/api_provision/run` ("Provision Everything" on the
landing page) replays every remaining create button for you, in click order:
both captures, the 10 KBAC policies, then each CIQ policy followed by its
knowledge queries — 93 steps, saving the same IDs to `.env` as clicking by
hand. Needs `URL_ENDPOINTS`, `SA_TOKEN`, `PROJECT_ID` and `APP_TOKEN` in
`.env`. Safe to re-run: steps whose ID is already saved are skipped. AuthZEN
evaluations and CIQ executes are not included — they are reads, not creations.

You can also still do everything one button at a time; provisioning is just the
shortcut.

## Backfill .env (sandbox created outside this app)

If the music sandbox already exists but was **not** created by this app: e.g.
a Hub console sandbox, or scripts — `/api_provision/backfill` ("Backfill .env")
recovers every derived ID by looking the configs up **by name** and saves them
to `.env`, so the app's forms work against that sandbox. Read-only: nothing is
created or modified on the platform.

- Needs only `URL_ENDPOINTS`, `SA_TOKEN` and `PROJECT_ID` in `.env`.
- Recognizes both this app's fixed names and the console sandbox's
  random-suffixed names (`<name>-123456-0`, `app-123456`, …).
- Tokens (`APP_TOKEN`, `USER_TOKEN`) cannot be recovered: credential secrets
  are only shown at creation.
- Console sandboxes have no MCP server, so `MCP_SERVER_ID` is reported as
  *missing* — create it with Getting Started step 5 afterwards.

## Manual provisioning order

1. Create Project, Application, App Agent, Token Introspect, MCP Server.
2. Capture nodes (`/api_capture/create`) and relationships (`/api_relationships/create`).
3. Create KBAC authorization policies (`/api_authorization_policy/create` … `/create10`).
4. Run AuthZEN evaluations (`/api_authzen/evaluate` … `/evaluate11`).
5. Create CIQ policies (`/api_ciq_policy/create` … `/create24`) and their knowledge queries
   (`/api_ciq_knowledge_query/create` … `/create24`; variants use `b`/`c`/`d` suffixes,
   e.g. `/create2b`, `/create6d`).
6. Execute (`/api_ciq_execute/execute` … `/execute24` and variants).
7. Or walk the story at `/chat/`: an interactive frontend that runs every CIQ
   execute in dependency-safe order (creates before reads, deletes last),
   scripted by `data/scenario.json`.

## Dataset source

The default data comes from `music-dataset.postman_collection.json`:

- `data/nodes/nodes_music.json`: 15,889 nodes (artists, tracks, albums, people, venues,
  playlists, streaming services…)
- `data/relationships/relationships_music.json`: 31,297 relationships
- `data/music_manifest.json`: every other configuration (project, application, app agent,
  token introspect, MCP server, 10 KBAC policies, 11 AuthZEN evaluations, 37 CIQ policies
  (including variant slots like 1b/1c/2b…) with their 44 knowledge queries and execution
  bodies)
