# Usecases

A *usecase* is the demo's domain packaged as configuration: everything that
makes the app "the CanBank demo" lives here, while the agents, gateways,
console, bridge and compose topology stay usecase-agnostic. This app ships
one bundle, `ibac`: CanBank with the music demo's people as Auth0 logins
(instant-stack dataset `data/ibac`).

## Selecting a usecase

Each usecase owns a **complete** env file, `.env.<usecase>` (`.env.ibac`;
gitignored: it holds the usecase's platform bindings and secrets: `USECASE`,
`APP_AGENT_CREDENTIALS_TOKEN`, `MCP_SERVER_PATH`, `MCP_SERVER_URL`, the
explain query ids, the console's `*_WORKFLOW_ID` values, the Auth0 clients,
`COMPOSE_PROFILES`, ports, ...). The committed template is the single
`.example.env`: copy it to `.env.<usecase>` and fill it, taking the
usecase's values from that usecase's provisioning output. `.env` is a
symlink to the active one, so every plain `docker compose` command works
unchanged. Switch with:

```sh
./switch-usecase.sh ibac   # relinks .env and runs docker compose up -d
```

Everything switches at once: skills, vocabulary, console app layer and the
platform project bindings, so a half-switched stack (one usecase's skills
against another's project) cannot happen. The script refuses an env file
whose `USECASE` is not its own name, and a `.env` that is still a regular
file (move it to `.env.<usecase>` first). The console header shows the
active usecase.

## How a bundle is wired

`USECASE=<name>` in the env file selects `usecases/<name>/`; the compose
file wires it in two ways per agent:

- `env_file: ./usecases/${USECASE}/usecase.env` - domain vocabulary: org
  name and tagline, CIQ knowledge-query names, weather HQ keywords, feature
  toggles.
- `volumes: ./usecases/${USECASE}/skills/<agent>:/app/skills:ro` - the
  agent's skill files (SKILL.md), including the dataset-specific
  `ibac-authz` vocabulary skill.

The console mounts `usecases/${USECASE}/app.json` as its app layer (sections,
action cards, prompts, who sees what).

## Bundle layout

```text
usecases/ibac/
  usecase.env        # env consumed by all agents
  app.json           # console app layer (sections, cards, prompts)
  DEMO_SCRIPT.md     # acts, prompts and expected outcomes
  SCENARIO.md        # the same prompts as one performable story
  skills/
    orchestrator/    # mounted at /app/skills in the orchestrator (query-retriever, query-erp, query-crm, web-search, orchestrator)
    retriever/       # mounted at /app/skills in the retriever (authzen, ibac-authz, ciq-execute, max-purchase-amount, retriever)
    analyst/         # mounted at /app/skills in the analyst (authzen, ibac-authz, ciq-execute, erp-invoices)
    crm/             # mounted at /app/skills in the CRM agent (no SKILL.md: the agent has no LLM)
    <agent>/coaz-mappings.json   # IBAC: the x-coaz-mappings each skill publishes on the agent card
```

`coaz-mappings.json` maps a skill id to the coarse-authorization mappings
(subject type, resource type, allowed actions) the Agent Gateway's IBAC
engine matches a prompt's intent against; the agent merges them into its
public card. Keep the actions in step with the dataset's KBAC policies.

The skill ids are also graph data: the dataset holds one `Skill` node per
skill id and one `Tool` node per MCP tool name, attached to the agent that
offers them with `HAS {workflow_name}` edges, which the gateway's capability
checks read through the `person-can-use-*` policies. A new
skill or tool needs its node and edges in the dataset, or the gateway hides
it from the extended card and refuses the tool call. Every `HAS` edge also
carries `discriminating_property: workflow_name`, like the `INVOKES` edges:
the platform keeps one relationship per source, target and type unless a
discriminating property tells the parallel edges apart, and without it only
the last workflow written would survive for each tool.

## Adding a usecase

1. Copy the `ibac` bundle and adapt: `usecase.env`, `app.json`, the
   dataset-specific skill (`<name>-authz` - keep its policy table and cast in
   sync with the dataset manifest), and the examples inside the generic
   skills.
2. Author and provision the dataset (instant-stack `data/<name>`): people,
   departments, workflows, documents, the KBAC policies, and the CIQ
   queries referenced by `usecase.env`. Logins must be Auth0 users whose
   subject is the Person's `external_id`.
3. `cp .example.env .env.<name>`, set `USECASE=<name>` and the usecase's
   project bindings and secrets in it, then `./switch-usecase.sh <name>`.

The graph dataset is the other half of a usecase: prompts only return data
once the matching dataset is provisioned in the IndyKite project.
