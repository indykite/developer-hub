# Every image carries the token-exchange tag: iag-mcp-demo builds the same
# service names under :latest, and distinct tags let both stacks coexist on
# one Docker host without one app's `make` replacing the other's images.
TAG := token-exchange

default:
	@echo "Removing old Docker images..."
	-@docker rmi chatbot:$(TAG)
	-@docker rmi retriever-agent:$(TAG)
	-@docker rmi orchestrator-agent:$(TAG)
	-@docker rmi weather-agent:$(TAG)
	-@docker rmi analyst-agent:$(TAG)
	-@docker rmi crm-agent:$(TAG)
	@echo "Building new Docker images..."
	@docker build -t chatbot:$(TAG) ./chatbot
	@docker build -t orchestrator-agent:$(TAG) ./orchestrator_agent
	@docker build -t retriever-agent:$(TAG) ./retriever_agent
	@docker build -t weather-agent:$(TAG) ./weather_agent
	@docker build -t analyst-agent:$(TAG) ./analyst_agent
	@docker build -t crm-agent:$(TAG) ./crm_agent

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

# Google Drive MCP PoC (compose profile "drive"); not part of the default build.
new-drive-mcp:
	-@docker rmi drive-mcp:$(TAG)
	@docker build -t drive-mcp:$(TAG) ./drive_mcp

# ERP invoices MCP server (compose profile "erp"); not part of the default build.
new-erp-mcp:
	-@docker rmi erp-mcp:$(TAG)
	@docker build -t erp-mcp:$(TAG) ./erp_mcp
