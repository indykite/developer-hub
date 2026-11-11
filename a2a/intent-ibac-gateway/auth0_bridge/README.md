# auth0-bridge

The gateways' identity provider endpoint, in front of Auth0.

Auth0 publishes no RFC 7662 introspection endpoint. The Agent Gateway
validates the user's access token only through one, under the same base URL
as the client-credentials endpoint it gets its own actor token from, and the
Intent Agent introspects its bearer the same way. This service is that base
URL for the whole stack:

| Endpoint | What it does |
| --- | --- |
| `POST /oauth/introspect` | Verifies an Auth0 JWT against the tenant JWKS (signature, `iss`, `exp`) and answers it as RFC 7662: `active`, `sub`, `aud`, `iss`, `exp`, `iat`, `scope`, `client_id` (Auth0's `azp`). A token it cannot vouch for answers `{"active": false}`. |
| `POST /oauth/token` | Forwards the request to Auth0's `/oauth/token` unchanged, adding `audience=AUTH0_DEFAULT_AUDIENCE` when the caller sent none. A client-credentials grant is answered from the kept token when there is one. |
| `GET /healthz` | Liveness. |

Who may introspect: the caller presents HTTP Basic (or `client_id` +
`client_secret` in the form). The bridge verifies the pair with a
client-credentials grant at Auth0, so every machine-to-machine application of
the tenant may introspect and none has to be listed here. The gateways
introspect with their own M2M client; the Intent Agent with the client named
by its `INTROSPECT_CLIENT_ID`.

Tokens are reused: a client-credentials token is kept in memory, per
application and audience, and handed back until `TOKEN_CACHE_MIN_REMAINING`
seconds before it expires. A gateway that restarts gets its token back, and
the check of an introspection caller uses the caller's own kept token, so
Auth0 issues one token per application and audience per token lifetime.
Restarting the bridge forgets them all.

What is not enforced here: the audience. The gateway checks it against
`JARVIS_PROTECTED_AGENT_AUTHENTICATION_AUDIENCES`, the Intent Agent against
`EXPECTED_TOKEN_AUDIENCE`.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `AUTH0_DOMAIN` | required | Tenant domain, e.g. `your-tenant.eu.auth0.com`; issuer, token endpoint and JWKS URI derive from it. |
| `AUTH0_DEFAULT_AUDIENCE` | empty | Audience added to a grant that names none (the gateways' actor tokens). |
| `TOKEN_CACHE_MIN_REMAINING` | `300` | A kept token is handed back until this many seconds before it expires. |
| `TOKEN_CACHE_MAX_ENTRIES` | `256` | The most tokens kept at once; beyond it a new token replaces the one closest to its expiry. Expired tokens are dropped. |
| `BRIDGE_PORT` | `8090` | Listening port. |
| `UPSTREAM_TIMEOUT_SECONDS` | `10` | Timeout of calls to Auth0. |
| `LOG_LEVEL` | `INFO` | One log line per introspection and token request; never the tokens. |

## Try it

From inside the compose network (the service publishes no host port):

```sh
# actor token for a gateway's M2M client - Auth0 answers, the audience was added
docker compose exec orchestrator-iag sh -c \
  'curl -s -u "$JARVIS_PROTECTED_AGENT_AUTHENTICATION_CLIENT_ID:$JARVIS_PROTECTED_AGENT_AUTHENTICATION_CLIENT_SECRET" \
   -d grant_type=client_credentials http://auth0-bridge:8090/oauth/token'

# introspect any Auth0 token of the tenant
docker compose exec orchestrator-iag sh -c \
  'curl -s -u "$JARVIS_PROTECTED_AGENT_AUTHENTICATION_CLIENT_ID:$JARVIS_PROTECTED_AGENT_AUTHENTICATION_CLIENT_SECRET" \
   -d token=<jwt> http://auth0-bridge:8090/oauth/introspect'
```

## Tests

```sh
cd auth0_bridge
pip install -r requirements.txt pytest
python -m pytest tests
```

The tests sign tokens with a local RSA key and stub the JWKS lookup and the
Auth0 token endpoint; no network.
