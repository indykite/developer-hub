# intent-ibac-gateway
#
#   make keys                 render the Token Service and audit configs (needs AUTH0_DOMAIN in .env), once
#   make intent-agent-image   build intent-agent:local from a checkout of the public repo
#                             (only to test an unreleased agent; the stack runs the published image)
#   make images               build the console image
#   make up / down / logs     run the stack
#   make test / lint          console unit tests, ruff over the demo
#   make helm-lint / helm-template   check the chart renders

COMPOSE := docker compose
# Path to a checkout of https://github.com/indykite/intent-agent, and the tag a
# source build gets (kept distinct from the published indykite/intent-agent).
INTENT_AGENT_SRC ?= ../../../intent-agent
INTENT_AGENT_IMAGE ?= intent-agent:local

.PHONY: keys images intent-agent-image up down logs test lint helm-lint helm-template

# Runs as the invoking user so the generated files belong to you, not to the
# image's uid 1001.
keys:
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" \
		-v "$(CURDIR)/token-service:/work/token-service" \
		-v "$(CURDIR):/work/demo" console \
		python -m app.keygen --template /work/token-service/token-service.template.yaml \
		--out /work/token-service/token-service.yaml \
		--audit-template /work/demo/audit-config.template.yaml --audit-out /work/demo/audit-config.yaml

intent-agent-image:
	docker buildx build --load -t $(INTENT_AGENT_IMAGE) "$(INTENT_AGENT_SRC)"

# The console, the fleet's stand-in agent (one image, six containers) and the auth0-bridge.
images:
	$(COMPOSE) build console agent-trading-desk auth0-bridge

up:
	$(COMPOSE) up -d --wait

down:
	$(COMPOSE) down --remove-orphans

logs:
	$(COMPOSE) logs -f --tail=100

# Tests run from a throwaway interpreter with the console's own requirements
# (uvx keeps the repo root free of uv.lock/.venv).
test:
	cd console && uvx --with-requirements requirements.txt --with pytest --with pytest-asyncio pytest -q tests

lint:
	uvx ruff check . && uvx ruff format --check .

helm-lint:
	helm lint chart --set auth0.domain=tenant.example

helm-template:
	helm template intent-ibac chart --set auth0.domain=tenant.example > /dev/null && echo "chart renders"
