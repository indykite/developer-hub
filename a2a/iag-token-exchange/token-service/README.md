# token-service

The self-hosted issuer of exchanged (delegation) tokens for this demo - the
jarvis `token-service` (an RFC 8693 token-exchange endpoint with OIDC
discovery, JWKS, introspection, and audited delegations). The gateways will
exchange against it instead of the external IdP.

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
  accepted. Phase 1 uses a local demo-IdP key (offline `keys`); phase 2 adds
  the real IdP issuer with its `jwks_uri`.

## Run (standalone, phase 1)

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

## Next phases

1. Wire into `docker-compose.yaml` + gateway networks.
2. Bump the gateway image (2.21.1 → 2.42.x) and point its exchange block at
   this service; accept idsvr-issued subject tokens via a second
   introspection configuration (`jwks_uri` of the IdP).
3. Platform acceptance: the project's Token Introspect config must trust this
   issuer (inline JWKS), provisioned via an instant-stack dataset.
4. Point the audit section at the demo's audit terminal.
