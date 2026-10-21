# Usecases

A *usecase* is the demo's domain packaged as configuration: everything that
makes the app "the CanBank demo" or "the CanSure Insurance demo" lives here,
while the agents, gateways, chatbot, and compose topology stay
usecase-agnostic.

## Selecting a usecase

Each usecase owns a **complete** env file - `.env.canbank`, `.env.insurance`
(gitignored; they hold the usecase's platform bindings and secrets:
`USECASE`, `CIQ_QUERY_ID`, `MCP_SERVER_URL`, `APP_AGENT_CREDENTIALS_TOKEN`,
`WORKFLOW_ID`, `AUTHZEN_SUBJECT_TYPES`, ports, IdP clients, ...). The
committed template is the single `.example.env` (its comments flag the
per-usecase values) - copy it to `.env.<usecase>` and fill the placeholders,
taking the usecase's values from that usecase's provisioning output. `.env` is a
symlink to the active one, so every plain `docker compose` command works
unchanged. Switch with:

```sh
./switch-usecase.sh insurance   # relinks .env and runs docker compose up -d
```

Everything switches atomically - skills, vocabulary, AND the platform
project bindings - so a half-switched stack (one usecase's skills against
the other's project) cannot happen. The compose file wires the bundle in
two ways per agent:

- `env_file: ./usecases/${USECASE}/usecase.env` - domain vocabulary: org
  name, CIQ knowledge-query names, weather HQ keywords, feature toggles.
- `volumes: ./usecases/${USECASE}/skills/<agent>:/app/skills:ro` - the
  agent's skill files (SKILL.md), including dataset-specific ones such as
  `canbank-authz` / `insurance-authz`.

## Bundle layout

```text
usecases/<name>/
  usecase.env        # env consumed by all agents (see canbank/usecase.env)
  DEMO_SCRIPT.md     # suggested prompts and demo narrative
  skills/
    orchestrator/    # mounted at /app/skills in the orchestrator
    retriever/       # mounted at /app/skills in the retriever
    analyst/         # mounted at /app/skills in the analyst
```

## Adding a usecase

1. Copy an existing bundle and adapt: `usecase.env`, the dataset-specific
   skill (`<name>-authz` - keep its KBAC vocabulary table in sync with the
   dataset manifest), and the examples inside the generic skills.
2. Author and provision the dataset (instant-stack `data/<name>`): users,
   departments, workflows, documents, the KBAC policies, and the CIQ
   queries referenced by `usecase.env`.
3. Point `USECASE=<name>` in `.env` and recreate the agents.

The graph dataset is the other half of a usecase: prompts only return data
once the matching dataset is provisioned in the IndyKite project.
