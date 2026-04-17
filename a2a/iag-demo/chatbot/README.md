# Chatbot – A2A Orchestrator Client

A simple web app that provides a chatbot UI and forwards user prompts to the Orchestrator Agent via the A2A protocol. The chatbot uses A2A streaming to receive the orchestrator's LLM response incrementally, keeps a session (`context_id`) across turns, and displays responses in real time.

## Requirements

- Python 3.10+
- Orchestrator Agent running and reachable

## Configuration

Environment variables:

| Variable | Default | Description |
| ---------- | --------- | ------------- |
| `CHATBOT_PORT` | `6000` | Port the chatbot web app listens on. |
| `ORCHESTRATOR_HOST` | `localhost` | Host of the Orchestrator Agent. |
| `ORCHESTRATOR_PORT` | `6001` | Port of the Orchestrator Agent. |
| `ORCHESTRATOR_TIMEOUT` | `300` | Request timeout in seconds (LLM responses can take a while). |
| `ID_SERVER_BASE_URL` | – | OAuth2 Identity Server base URL (e.g. `https://idsvr.indykite.one/oauth/v2`). |
| `CHATBOT_REDIRECT_URL` | – | OAuth2 redirect URI after login (e.g. `http://127.0.0.1:5800/auth/callback`). |
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

3. Open <http://localhost:6000> (or your configured `CHATBOT_PORT`) in a browser.

4. Click **Login** to authenticate via OAuth2 (requires `ID_SERVER_BASE_URL` and `CHATBOT_REDIRECT_URL`).

5. After logging in, type prompts; they are sent as A2A messages to the orchestrator with your access token. Responses stream in real time. The session (`context_id`) is persisted in `localStorage` so the orchestrator receives consistent context across turns.
