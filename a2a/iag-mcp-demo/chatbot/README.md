# Chatbot – A2A Orchestrator Client

A simple web app that provides a chatbot UI and forwards user prompts to the Orchestrator Agent via the A2A protocol. The chatbot uses A2A streaming to receive the orchestrator's LLM response incrementally, keeps a session (`context_id`) across turns, and displays responses in real time.

## Requirements

- Python 3.10+
- Orchestrator Agent running and reachable

## Configuration

Environment variables:

| Variable | Default | Description |
| ---------- | --------- | ------------- |
| `CHATBOT_PORT` | `3000` | Port the chatbot web app listens on. |
| `ORCHESTRATOR_HOST` | `localhost` | Host of the Orchestrator Agent. |
| `ORCHESTRATOR_PORT` | `6001` | Port of the Orchestrator Agent. |
| `ORCHESTRATOR_TIMEOUT` | `300` | Request timeout in seconds (LLM responses can take a while). |
| `ID_SERVER_BASE_URL` | – | OAuth2 Identity Server base URL (e.g. `https://idsvr.indykite.one/oauth/v2`). |
| `CHATBOT_REDIRECT_URL` | – | OAuth2 redirect URI after login (e.g. `http://127.0.0.1:3000/auth/callback`). |
| `ID_SERVER_CLIENT_ID` | `indykiteagent` | OAuth2 client ID. |
| `ID_SERVER_SCOPES` | `openid profile email` | OAuth2 scopes. |
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

The page has two faces of the same conversation, switched with the
**App | Console** toggle in the header. The audit terminal on the right is
outside both and always visible.

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
