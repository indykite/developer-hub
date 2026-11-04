# Every image carries the ibac-demo tag: the sibling demos build the same
# service names under :latest and :token-exchange, and distinct tags let the
# stacks coexist on one Docker host without one app's `make` replacing the
# other's images.
TAG := ibac-demo

default:
	@echo "Removing old Docker images..."
	-@docker rmi chatbot:$(TAG)
	-@docker rmi retriever-agent:$(TAG)
	-@docker rmi orchestrator-agent:$(TAG)
	-@docker rmi weather-agent:$(TAG)
	-@docker rmi analyst-agent:$(TAG)
	-@docker rmi crm-agent:$(TAG)
	-@docker rmi auth0-bridge:$(TAG)
	@echo "Building new Docker images..."
	@docker build -t chatbot:$(TAG) ./chatbot
	@docker build -t orchestrator-agent:$(TAG) ./orchestrator_agent
	@docker build -t retriever-agent:$(TAG) ./retriever_agent
	@docker build -t weather-agent:$(TAG) ./weather_agent
	@docker build -t analyst-agent:$(TAG) ./analyst_agent
	@docker build -t crm-agent:$(TAG) ./crm_agent
	@docker build -t auth0-bridge:$(TAG) ./auth0_bridge

new-chatbot:
	-@docker rmi chatbot:$(TAG)
	@docker build -t chatbot:$(TAG) ./chatbot

new-retriever:
	-@docker rmi retriever-agent:$(TAG)
	@docker build -t retriever-agent:$(TAG) ./retriever_agent

new-orchestrator:
	-@docker rmi orchestrator-agent:$(TAG)
	@docker build -t orchestrator-agent:$(TAG) ./orchestrator_agent

new-weather:
	-@docker rmi weather-agent:$(TAG)
	@docker build -t weather-agent:$(TAG) ./weather_agent

new-analyst:
	-@docker rmi analyst-agent:$(TAG)
	@docker build -t analyst-agent:$(TAG) ./analyst_agent

new-crm:
	-@docker rmi crm-agent:$(TAG)
	@docker build -t crm-agent:$(TAG) ./crm_agent

new-auth0-bridge:
	-@docker rmi auth0-bridge:$(TAG)
	@docker build -t auth0-bridge:$(TAG) ./auth0_bridge

# Google Drive MCP PoC (compose profile "drive"); not part of the default build.
new-drive-mcp:
	-@docker rmi drive-mcp:$(TAG)
	@docker build -t drive-mcp:$(TAG) ./drive_mcp

# Intent Agent image (compose profile "intent"): the stack runs the published
# indykite/intent-agent; this target builds a checkout of the public repo
# https://github.com/indykite/intent-agent only to test an unreleased agent.
# Set INTENT_AGENT_SRC to the checkout and INTENT_AGENT_IMAGE in .env to the
# name built here.
INTENT_AGENT_SRC ?= ../../../intent-agent
INTENT_AGENT_IMAGE ?= intent-agent:local

intent-agent-image:
	docker buildx build --load -t $(INTENT_AGENT_IMAGE) "$(INTENT_AGENT_SRC)"

# ERP invoices MCP server (compose profile "erp"); not part of the default build.
new-erp-mcp:
	-@docker rmi erp-mcp:$(TAG)
	@docker build -t erp-mcp:$(TAG) ./erp_mcp
