# Chatbot – A2A Orchestrator Client

A simple web app that provides a chatbot UI and forwards user prompts to the Orchestrator Agent via the A2A protocol. The chatbot uses A2A streaming to receive the orchestrator's LLM response incrementally, keeps a session (`context_id`) across turns, and displays responses in real time.

## Requirements

- Python 3.10+
- Orchestrator Agent running and reachable

## Configuration

Environment variables:

| Variable | Default | Description |
| ---------- | --------- | ------------- |
| `CHATBOT_PORT` | `3000` | Port the chatbot web app listens on when run locally, and the port of the login redirect and logout URLs. In compose the container always listens on 3300 and `CHATBOT_PORT` is the published host port. |
| `ORCHESTRATOR_HOST` | `localhost` | Host of the Orchestrator Agent. |
| `ORCHESTRATOR_PORT` | `6001` | Port of the Orchestrator Agent. |
| `ORCHESTRATOR_TIMEOUT` | `300` | Request timeout in seconds (LLM responses can take a while). |
| `ID_SERVER_BASE_URL` | – | OAuth2 issuer base URL (the Auth0 tenant, e.g. `https://your-tenant.eu.auth0.com`). |
| `ID_SERVER_AUTHORIZE_ENDPOINT` | `oauth-authorize` | Authorization endpoint, relative to the base URL (`authorize` on Auth0). |
| `ID_SERVER_TOKEN_ENDPOINT` | `oauth-token` | Token endpoint, relative to the base URL (`oauth/token` on Auth0). |
| `ID_SERVER_AUDIENCE` | – | `audience` sent on the authorize request (Auth0 API identifier, `ibac-console`); without it Auth0 issues an opaque access token. |
| `ID_SERVER_LOGOUT_ENDPOINT` | – | Logout endpoint, relative to the base URL, called with `client_id` + `returnTo` (`v2/logout` on Auth0); empty = local logout only. |
| `CHATBOT_REDIRECT_URL` | – | OAuth2 redirect URI after login (e.g. `http://localhost:3300/auth/callback`), listed as callback URL of the Auth0 application; the logout `returnTo` (`http://<host>:<port>/`) must be an allowed logout URL there too. |
| `ID_SERVER_CLIENT_ID` | `indykiteagent` | OAuth2 client ID (the Auth0 Regular Web Application). |
| `ID_SERVER_SCOPES` | `openid profile email` | OAuth2 scopes; `openid` yields the id_token the UI reads the display name from. |
| `FLASK_SECRET_KEY` | (random) | Secret for Flask session (set in production). |

OAuth2 uses the authorization code grant with PKCE. Users must log in before chatting. The access token is stored in the session and sent as `Authorization: Bearer` on all A2A calls to the orchestrator.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Running

1. Start the Orchestrator Agent first (see `../orchestrator_agent/README.md`).

2. Start the chatbot:

   ```bash
   python app.py
   ```

3. Open <http://localhost:3000> (or your configured `CHATBOT_PORT`) in a browser.

4. Click **Login** to authenticate via OAuth2 (requires `ID_SERVER_BASE_URL` and `CHATBOT_REDIRECT_URL`).

5. After logging in, type prompts; they are sent as A2A messages to the orchestrator with your access token. Responses stream in real time. The session (`context_id`) is persisted in `localStorage` so the orchestrator receives consistent context across turns.

## App and Console layers

The page has three faces of the same conversation, switched with the
**App | Console | Graph** toggle in the header (the third face, the request
drawn as a graph, is described [below](#the-graph-view)). The audit terminal
on the right is outside all of them and always visible.

- **App** (default): what a customer or employee of the demo organization
  would see - the org's own web app. **Home** has a free-text assistant box
  and shows the latest answer; the nav opens the org's sections (for
  SecureHome: Policies, Documents, Claims, Billing, Support), each a set of
  *action cards* that send the demo's prompt behind the scenes and render
  the answer inside the card. Answers are free-form markdown from the
  orchestrator, so each card carries a render hint and a small presenter
  picks the rendering: document lists become document tiles (with an
  "available" / "not available" state), `Coverage amount: $450,000` lines
  become a details card, markdown tables become app tables, a Salesforce
  answer becomes a case confirmation, and anything else stays as text. What
  each user sees differs by their rights, through the answers and the
  denials. A NOT AUTHORIZED gateway decision shows as a red notice with a
  **See why** link into the console - only decisions from the gateways the
  card depends on (its `services` list in `app.json`; the analyst probes
  every backend it has, so a customer's Drive request also yields an
  unrelated graph-gateway denial, which stays in the audit column) - and, on
  cards marked for it, a **Request access** button for whoever was denied,
  staff included: the request fans out to every open console
  (`POST /api/access-request`), staff who can grant at least one mapped
  service (the AuthZEN self-check behind `can_grant` in `/api/auth/status`)
  see it in their Support inbox and grant
  it there (through `/api/grant` and its AuthZEN self-check), and the
  requester's card turns green with a **Try again** button when the grant
  event arrives.
- **Console**: the page as before - the whole conversation and the input box.

With **Auto-switch** on (default), sending from the app flips to the console
while the agents work, so the audience sees the prompt, the streaming answer
and the audit cards arrive, then returns to the app with the result. Flipping
manually during a request cancels the automatic return. The chosen layer and
the auto-switch preference persist in `localStorage`; `#app` / `#console` in
the URL select a layer for deep links.

Staff and customers see different groups of cards: a group carries an
`audience` (`customer`, `staff`, or none for everyone) and the role comes from
the user's own graph relationships - `staff_check` in `app.json` is
`{"explain": {"workflow": "wf1"}}`: the chatbot runs the explain queries' staff
leg with the app key (the same query behind the why? cards), which returns rows
only when the person `WORKS_IN` a department that `CAN_TRIGGER` that workflow -
the dataset's definition of staff. (An alternative form,
`{"query": ..., "field": ...}`, runs a knowledge query as the user on the MCP
server at `MCP_SERVER_URL`; it needs an MCP server that accepts the console's
user token.) A failed check shows every group. The role is cached in the
session and returned by `/api/auth/status`.

The app is usecase data: `usecases/<usecase>/app.json` (default layer, accent,
placeholder, `staff_check`, and the sections with their groups and action
cards - title, description, optional input, the prompt with `{input}`, a
render hint, the `services` chain the card depends on (first gateway to
last), `access: "request"` for cards that may be denied, and an optional
`denied` text shown instead of the generic notice and the agent's apology
when the denial is outright). A request targets the deepest denied hop of
the card's chain, so the staff grant writes that gateway's workflow bundle
from `GRANT_WORKFLOW_MAP`. The file is mounted into the container
at `/app/usecase-app.json` by docker-compose (`USECASE_APP_FILE` to override
when running locally). Branding stays in `usecase.env` (`ORG_NAME`,
`ORG_TAGLINE`). Rebuild the image after changing the static files
(`make new-chatbot`, then `docker compose up -d chatbot`).

## The audit terminal

The right pane lists what the gateways, the Token Service, the agents and
the Intent Agent recorded (`POST /api/push-update`, streamed to the browser
over `/api/updates`). `static/js/ledger.js` owns everything around a card:

- **Runs.** The records of one request fold under one header: time, person,
  outcome (`allowed`, `denied`, `attention`), record count and the prompt.
  A new run folds the earlier ones away; a click on a header opens or
  closes it. A run starts when a prompt is sent from this console, when a
  person is admitted at a first gateway (a delegation chain of one agent),
  or with a new Intent Agent record. The agents start a new trace on every
  hop, so runs are not built on the trace id. Records that name no person
  (the Token Service describing an agent's token) are listed as `service
  records`.
- **Intent on the header.** When the Intent Agent was asked, the run's
  header says `intent · 2` (evaluations found) or `no intent`.
- **Repeats.** A record identical to one already in its run is counted on
  that card (`×3`) instead of shown again.
- **Filters.** Free text over everything a card shows, a service list, one
  chip per kind of record (`admission`, `capabilities`, `intent`, `data`,
  `tokens`) and `denials` for refusals and failures. While a filter is on,
  every run with a match shows its matching cards.
- **Pane controls.** `following` keeps the newest record in view and pauses
  when you scroll up; `collapse` / `expand` folds every run; `export`
  downloads every record as JSON; `clear` empties the pane. Drag the pane's
  left edge to resize it (double-click to reset).
- **Card tools.**
    - `raw record` shows the record exactly as the service sent it; `copy`
      puts it on the clipboard.
    - Intent and intent-check cards show each evaluation as chips (subject →
      action → resource) with what it fits, and a data-check card each
      question with the platform's `allowed` / `denied`.
    - `token claims` reads out the token of a hop: subject, audience, the
      delegation chain (oldest actor first), the intent it carries, expiry.
    - `ask the platform now` repeats a recorded AuthZEN question (a `data
      check` card's resolved evaluations, or the Intent Agent's evaluations
      that name both ids) through `POST /api/recheck`. The route sends one
      evaluation with the app agent credential and needs a logged-in
      session; it writes nothing. The question goes without the delegation
      token of the original call, so a policy that reads the chain can answer
      differently, and a policy with parameters the question lacks is
      reported as `the platform answered 422`.

## The Graph view

**Graph** in the header draws one request from its audit records
(`static/js/graphs.js`). It asks the platform nothing: the picture is rebuilt
from the run's records each time one arrives, so it fills in while the
agents work. `Latest` keeps it on the newest request; the list next to it
picks an earlier one.

- **Request.** Top to bottom: the person, then for every hop the gateway
  that decided and the agent or MCP server behind it, following the
  delegation chain each record names (`actorsChain`). A gateway lists the
  kinds of records it wrote (admission, discovery, invocation, intent check,
  data check, token). The capabilities a call named hang off the party that
  offers them (`used` / `refused`), and the Intent Agent off the gateway
  that asked it. Green solid edges are allowed, red dashed ones refused;
  a hop that only appears in a later hop's chain is drawn dashed (`no
  record`), and the party behind a refusing gateway as `not reached`.
- **Selecting a node** marks its path back to the person - the delegation
  chain of that hop, its direction shown by the moving dashes - opens its
  records in a panel on the stage, and points the audit terminal at their
  cards. A click on a listed record does the same for that one card, a
  click on the background clears the selection.
- **The stage.** Nodes are cards: what the node is (person, Agent Gateway,
  agent, MCP server, ...), its name, and for a gateway the kinds of records
  it wrote; a mark says how it ended (✓ allowed, ✕ refused). The wheel
  zooms, dragging the background pans, `Fit` brings the whole graph back.
- **Capabilities.** For one gateway (the list next to the mode switch): the
  party behind it, the kinds it offers, and per kind what the caller was
  permitted to see, what it used, what was refused, and how many entries of
  the server's list stayed hidden (`+17 hidden`), from the gateway's
  `permittedSet`, `discovery` counts and invocation records.
- **Intent.** What the Intent Agent read from the prompt and what the
  gateways' IBAC engine did with it.
    - A status line says whether IBAC is on (`IBAC_ENABLED`), what a skill
      without a mapping means (`IBAC_UNMAPPED`) and whether the Intent Agent
      answers its health endpoint (`GET /api/ibac/status`; the agent is asked
      only when the `intent` profile is on).
    - Six steps follow the prompt: **Prompt**, **Intent Agent** (evaluations
      and duration, or why there is no intent), **Entry gateway** (how many
      evaluations fit a skill of the entry agent; without the gateway's own
      record, the console's preview from the agent card), **Token** (the
      evaluations in `act.intent`), **MCP gateway** (calls checked against
      the intent, lists narrowed) and **Platform** (answers about the data a
      call named). A step without a record is drawn dashed; a click on a
      step points the audit terminal at its records.
    - The graph reads left to right: the prompt, one node per evaluation
      (subject → action → resource), the skills each fits at the entry
      gateway, the tools listed and the calls checked at an MCP gateway, and
      the platform's answer for the data a call named. An evaluation that
      fits nothing ends in a red `fits nothing`. Selecting a node keeps
      everything it came from and led to and dims the rest.
- **From a card.** `graph` in a card's footer opens the view on that
  record: an admission card selects its hop in the request graph, a
  discovery or invocation card opens the capabilities of its gateway, an
  intent or data-check card opens the intent graph on its node.

The why? modal (the live path behind an admission, from the explain
queries) has a legend, zooms with the wheel and has a `Fit` button.
