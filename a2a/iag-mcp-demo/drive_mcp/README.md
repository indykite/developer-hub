# Drive MCP (proof-of-concept)

Puts the IndyKite Agent Gateway in front of a **Google Drive MCP server**, to
show that the gateway's MCP proxy mode is downstream-agnostic: the same token
introspection, AuthZEN `CAN_TRIGGER` check, workflow resolution and audit run
for any Streamable HTTP MCP server, not just IndyKite's.

The container wraps a stdio Google Drive MCP server with
[supergateway](https://github.com/supercorp-ai/supergateway) in stateful
Streamable HTTP mode (`Mcp-Session-Id` issued per session, which the IAG
passes through untranslated). Any other Streamable HTTP Drive MCP server can
be swapped in by pointing `DRIVE_MCP_HOST` / `DRIVE_MCP_PORT` at it.

The Drive server is **vendored** under [`vendor/`](vendor/): it is the source
of the archived `@modelcontextprotocol/server-gdrive` reference server, kept
locally so the build depends only on its (non-deprecated) runtime
dependencies rather than the deprecated npm package. Its credential model is
a portable, read-only, plain-JSON file — which is exactly what a
gateway-fronted, container-mounted, single-identity server needs (maintained
community forks tend to use non-portable/encrypted token stores that can't be
authed on the host and mounted into a container).

## The auth model (important)

The gateway always **replaces** the caller's `Authorization` header with a
delegation token minted at the IndyKite IdP — there is no mechanism to inject
Google credentials per downstream. So the Drive server must hold its **own**
Google OAuth material and ignore the incoming bearer token, which is exactly
what this server does (a single server-side OAuth identity). Google's own
hosted MCP endpoints (which require a Google OAuth access token from the
caller) will **not** work behind the gateway.

Also note the gateway does not inspect MCP payloads: authorization is
per-request/session ("may millicent reach the Drive server via `wf-drive`?"),
not per-tool.

## Setup

1. In Google Cloud console: create an OAuth client (Desktop app), enable the
   Drive API, and download the client keys as `gcp-oauth.keys.json` into
   `drive_mcp/.gdrive/` (gitignored).
2. Run the interactive auth flow once on the host (opens a browser — Docker
   containers can't, so this must run on the host). It uses a random loopback
   port, so there's no clash with the stack's ports:

   ```bash
   cd drive_mcp
   GDRIVE_OAUTH_PATH=$PWD/.gdrive/gcp-oauth.keys.json \
   GDRIVE_CREDENTIALS_PATH=$PWD/.gdrive/.gdrive-server-credentials.json \
   npx -y @modelcontextprotocol/server-gdrive auth
   ```

   This saves `.gdrive/.gdrive-server-credentials.json` (portable plain JSON),
   which the container mounts read-only at `/gdrive`. The deprecated package is
   invoked here only as a throwaway `npx` for the one-time browser auth — it
   is **not** installed in the image (the image builds from `vendor/`). To
   avoid it entirely, run the vendored server's auth instead:
   `cd vendor && npm install && GDRIVE_OAUTH_PATH=… GDRIVE_CREDENTIALS_PATH=… node index.js auth`.
3. Create an `indykiteagent-drive` IdP client and set
   `DRIVE_MCP_IDP_CLIENT_ID` / `_SECRET` in `.env`.
4. Re-run the agent-workflow ingest
   (`bruno/iag-demo/ingest/agent-worfklow`): it adds the `wf-drive` Workflow,
   the `indykiteagent-drive` Agent, the
   `wf-drive -INVOKES-> indykiteagent-drive` edge (`workflow_name: wf-drive`)
   and `millicent -CAN_TRIGGER-> wf-drive`. Only millicent is granted — other users
   get a 403 at the gateway, which is the point of the demo.

## Run

```bash
make new-drive-mcp                  # build the image
# in .env set: COMPOSE_PROFILES=drive
docker compose up                   # boots drive-mcp + drive-mcp-iag alongside the stack
```

Setting `COMPOSE_PROFILES=` back to empty (or removing it) returns to the
base demo — the drive services are excluded and everything else runs
unchanged. `docker compose --profile drive up` works as a one-off
alternative to editing `.env`.

Then run the Bruno folder `bruno/iag-demo/drive-mcp` in order (initialize →
initialized → list tools → search) with `user_token_millicent` set. Requests go
to `http://localhost:8887/mcp` (the `drive-mcp-iag` gateway), which forwards
the path to `http://drive-mcp:8000/mcp` after the IAG checks pass.

Without a valid token mounted, `initialize` still returns a session but the
child server errors on auth in `docker compose logs drive-mcp` — a useful
smoke signal that the proxying works and only the Google side is missing.
