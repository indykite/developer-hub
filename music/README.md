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

## Provisioning order

> **Token Introspect uses offline validation.** The config is created with
> `offline_validation: {}` (not `online_validation`). The Auth0 user tokens in this
> demo are **ID tokens** (`aud` = the client ID), and Auth0's `/userinfo` endpoint —
> which `online_validation` calls, rejects ID tokens with `401 "Invalid token type"`,
> so person-subject CIQ executes fail with `Invalid token in Authorization header`.
> Offline validation verifies the token's signature against the
> issuer's JWKS instead, so the ID token is accepted.

1. Create Project, Application, App Agent, Token Introspect, MCP Server.
2. Capture nodes (`/api_capture/create`) and relationships (`/api_relationships/create`).
3. Create KBAC authorization policies (`/api_authorization_policy/create` … `/create10`).
4. Run AuthZEN evaluations (`/api_authzen/evaluate` … `/evaluate11`).
5. Create CIQ policies (`/api_ciq_policy/create` … `/create24`) and their knowledge queries
   (`/api_ciq_knowledge_query/create` … `/create24`; variants use `b`/`c`/`d` suffixes,
   e.g. `/create2b`, `/create6d`).
6. Execute (`/api_ciq_execute/execute` … `/execute24` and variants).
7. Or walk the story at `/chat/` — an interactive frontend that runs every CIQ
   execute in dependency-safe order (creates before reads, deletes last),
   scripted by `data/scenario.json`.

## Dataset source

The default data comes from `music-dataset.postman_collection.json`:

- `data/nodes/nodes_music.json` — 15,889 nodes (artists, tracks, albums, people, venues,
  playlists, streaming services…)
- `data/relationships/relationships_music.json` — 31,297 relationships
- `data/music_manifest.json` — every other configuration (project, application, app agent,
  token introspect, MCP server, 10 KBAC policies, 11 AuthZEN evaluations, 24 CIQ policies
  with their 44 knowledge queries and execution bodies)
