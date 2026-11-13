# Every image carries the cib-sentinel tag so this stack can coexist with the
# sibling demos on one Docker host. The fleet agents and the Auth0 bridge are
# built from ../ibac-demo: this app reuses them unchanged and gives them its
# own usecase bundle (usecases/cib).
TAG := cib-sentinel
FLEET := ../ibac-demo

default: fleet new-console new-sentinel

fleet:
	@docker build -t orchestrator-agent:$(TAG) $(FLEET)/orchestrator_agent
	@docker build -t retriever-agent:$(TAG) $(FLEET)/retriever_agent
	@docker build -t analyst-agent:$(TAG) $(FLEET)/analyst_agent
	@docker build -t auth0-bridge:$(TAG) $(FLEET)/auth0_bridge

new-console:
	@docker build -t cib-console:$(TAG) ./console

new-sentinel:
	@docker build -t cib-sentinel:$(TAG) ./sentinel

# Render token-service/token-service.yaml with a fresh signing key and client
# secret (once). Needs AUTH0_DOMAIN in .env. Prints TOKEN_SERVICE_CLIENT_SECRET
# for .env and TOKEN_SERVICE_PUBLIC_JWK for instant-stack's .env.
keys:
	@docker run --rm --user $$(id -u):$$(id -g) --env-file .env -e HOME=/tmp \
		-v "$(CURDIR)/token-service:/work" python:3.13-slim \
		sh -c "pip install -q --user cryptography && python /work/keygen.py"

test:
	python -m pytest sentinel/tests console/tests

.PHONY: default fleet new-console new-sentinel keys test
