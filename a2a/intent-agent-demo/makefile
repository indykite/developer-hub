# intent-agent-demo
#
#   make keys                 generate the Token Service config + demo issuer key (once)
#   make intent-agent-image   build intent-agent:local from a checkout of the public repo
#   make images               build the console image
#   make up / down / logs     run the stack
#   make test / lint          console unit tests, ruff over the demo
#   make helm-lint / helm-template   check the chart renders

COMPOSE := docker compose
# Path to a checkout of https://github.com/indykite/intent-agent
INTENT_AGENT_SRC ?= ../../../intent-agent
INTENT_AGENT_IMAGE ?= intent-agent:local

.PHONY: keys images intent-agent-image up down logs test lint helm-lint helm-template

# Runs as the invoking user so the generated files belong to you, not to the
# image's uid 1001.
keys:
	@mkdir -p console/keys
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" \
		-v "$(CURDIR)/token-service:/work/token-service" \
		-v "$(CURDIR)/console/keys:/work/keys" console \
		python -m app.keygen --template /work/token-service/token-service.template.yaml \
		--out /work/token-service/token-service.yaml --key /work/keys/demo-idp.key

intent-agent-image:
	docker buildx build --load -t $(INTENT_AGENT_IMAGE) "$(INTENT_AGENT_SRC)"

images:
	$(COMPOSE) build console

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
	helm lint chart

helm-template:
	helm template intent-demo chart > /dev/null && echo "chart renders"
