# token-service

The self-hosted issuer of exchanged (delegation) tokens for this demo - the
jarvis `token-service` (an RFC 8693 token-exchange endpoint with OIDC
discovery, JWKS, introspection, and audited delegations). The gateways will
exchange against it instead of the external IdP once the platform accepts
its tokens (see [Status](#status)).

## Build the image

No public image yet (service version 0.0.1). Build from the jarvis repo:

```shell
cd <jarvis-repo>
docker build -t token-service:local \
  --build-arg SERVICE_NAME=token-service \
  --build-arg SERVICE_PATH=agent-gateway/token-service .
```

## Configure

`token-service.yaml` (gitignored - it holds the private signing key and the
client secret) is created from `token-service.example.yaml`:

- `idp.signing_keys`: a fresh RSA JWK **with the private part**, `alg` +
  `kid` + `use: sig` set. Generate one (e.g. PyJWT:
  `jwt.algorithms.RSAAlgorithm.to_jwk(private_key)`).
- `idp.client_auth`: the credentials the gateways present on the token
  endpoint (`client_secret_basic`).
- `token_introspection.configurations`: which subject/actor tokens are
  accepted - offline JWT matchers per gateway actor audience, plus the real
  IdP issuer via its `jwks_uri` for user subject tokens.

## Run (standalone)

```shell
docker run --rm -d --name tx-token-service -p 8102:8102 \
  -v $(pwd)/token-service/token-service.yaml:/app/.configs/token-service.yaml:ro \
  token-service:local --config=/app/.configs/token-service.yaml
```

Endpoints: `/.well-known/openid-configuration`, `/.well-known/jwks.json`,
`POST /oauth2/token` (the exchange), `POST /oauth2/introspect`.

## Manual exchange smoke test

```shell
curl -u "agent-gateway:$TOKEN_SERVICE_CLIENT_SECRET" -X POST http://localhost:8102/oauth2/token \
  -d "grant_type=urn:ietf:params:oauth:grant-type:token-exchange" \
  -d "subject_token=<user JWT>"  -d "subject_token_type=urn:ietf:params:oauth:token-type:access_token" \
  -d "actor_token=<agent JWT>"   -d "actor_token_type=urn:ietf:params:oauth:token-type:access_token" \
  -d "requested_token_type=urn:ietf:params:oauth:token-type:access_token"
```

The response's `access_token` carries `iss` = the service's `base_url`,
`sub` = the subject token's subject, `act` = `{sub: <actor>, type: Agent}`,
and a 5-minute TTL. Verify with `POST /oauth2/introspect` (same client auth).
Both subject and actor tokens must match a `token_introspection`
configuration (issuer + audience) and verify against its keys.

## Status

1. ~~Wire into `docker-compose.yaml` + gateway networks~~ - done: the
   `token-service` service in [`../docker-compose.yaml`](../docker-compose.yaml)
   joins every gateway network.
2. ~~Bump the gateway image and point its exchange block at this service~~ -
   done: gateways run `2.48.0`. `2.42.x` added the `JARVIS_TOKEN_SERVICE_*`
   block; `2.47.0` made the gateway read the incoming `X-IK-Token`
   (introspected here, subject of the next exchange), so the **A2A gateways
   now run token-service mode with multi-hop chains** - the block is live on
   orchestrator/retriever/weather/analyst in `docker-compose.yaml`.
   idsvr-issued subject tokens are accepted via a second introspection
   configuration (`jwks_uri` of the IdP).
3. **Platform acceptance - fixed in mcp-server `2.49.0` (2026-08-28),
   rollout pending**: the MCP endpoint no longer gates Authorization on the
   single issuer+audience shape the MCP config binds (any Token Introspect
   config of the app space authenticates) and now introspects the
   `X-IK-Token` delegated token itself (`400 invalid_request` on a bad one,
   `sub` must match the access token). The project's Token Introspect config
   already trusts this issuer (inline public JWKS, `https` issuer required).
   The MCP gateways keep the classic IdP exchange until the release reaches
   the target environment - probe: a valid Bearer plus a garbage
   `X-IK-Token` answers `400` naming the header on the new server, while the
   old one ignores it - then copy the `JARVIS_TOKEN_SERVICE_*` block onto
   `mcp-iag`/`drive-mcp-iag`.
4. ~~Point the audit section at the demo's audit terminal~~ - done: every
   exchange attempt is delivered to the chatbot's audit webhook
   (`audit.http.url`), tagged `service: token-service`.
