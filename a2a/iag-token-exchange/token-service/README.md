# token-service

The self-hosted issuer of exchanged (delegation) tokens for this demo - the
jarvis `token-service` (an RFC 8693 token-exchange endpoint with OIDC
discovery, JWKS, introspection, and audited delegations). Every gateway
exchanges against it instead of the external IdP, and the platform validates
its tokens through the project's Token Introspect configurations (see
[How the demo uses it](#how-the-demo-uses-it)).

## Get the image

Published on Docker Hub as
[`indykite/token-service`](https://hub.docker.com/r/indykite/token-service)
(since `1.0.0`, 2026-09-03; `linux/amd64` only):

```shell
docker pull indykite/token-service:1.0.0
```

On arm64 hosts (Apple Silicon) add `--platform linux/amd64` to the pull and
run commands - the image then runs under emulation (Rosetta/QEMU).

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
  indykite/token-service:1.0.0 --config=/app/.configs/token-service.yaml
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

## How the demo uses it

- The `token-service` service in [`../docker-compose.yaml`](../docker-compose.yaml)
  joins every gateway network; the gateways reach it at
  `http://token-service:8102`.
- **Every gateway** - A2A and MCP-protecting alike - runs token-service mode:
  the `JARVIS_TOKEN_SERVICE_*` block sits in
  [`../iag-base-docker.yaml`](../iag-base-docker.yaml). A gateway introspects
  the incoming `X-IK-Token` here and exchanges it, so the `act` chain nests
  hop by hop; the forwarded request carries the user's token in
  `Authorization` and the delegation in `X-IK-Token`. The platform's MCP
  server validates the `X-IK-Token` itself (`400 invalid_request` on a bad
  one, `sub` must match the access token).
- `idp.audiences` lists every protected agent (`indykiteagent*`, including
  `-crm` and `-erp`) and the user-token audiences; the
  `token_introspection.configurations` accept the gateways' actor tokens
  offline per audience and fall back to online introspection at the IdP for
  user tokens.
- Platform-side trust comes from the `-ts` instant-stack datasets
  (`data/canbank-ts`, `data/insurance-ts`): a Token Introspect configuration
  for this issuer with the public JWK inline (`TOKEN_SERVICE_PUBLIC_JWK`),
  plus the chain-guarded query/policy pair and KBAC policy that read the
  chain as `$ik_token`.
- Every exchange attempt is delivered to the chatbot's audit webhook
  (`audit.http.url`), tagged `service: token-service`.
