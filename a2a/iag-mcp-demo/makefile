default:
	@echo "Removing old Docker images..."
	-@docker rmi chatbot
	-@docker rmi retriever-agent
	-@docker rmi orchestrator-agent
	-@docker rmi weather-agent
	-@docker rmi analyst-agent
	@echo "Building new Docker images..."
	@docker build -t chatbot ./chatbot
	@docker build -t orchestrator-agent ./orchestrator_agent
	@docker build -t retriever-agent ./retriever_agent
	@docker build -t weather-agent ./weather_agent
	@docker build -t analyst-agent ./analyst_agent

new-chatbot:
	-@docker rmi chatbot
	@docker build -t chatbot ./chatbot

new-retriever:
	-@docker rmi retriever-agent
	@docker build -t retriever-agent ./retriever_agent

new-orchestrator:
	-@docker rmi orchestrator-agent
	@docker build -t orchestrator-agent ./orchestrator_agent

new-weather:
	-@docker rmi weather-agent
	@docker build -t weather-agent ./weather_agent

new-analyst:
	-@docker rmi analyst-agent
	@docker build -t analyst-agent ./analyst_agent

# Google Drive MCP PoC (compose profile "drive"); not part of the default build.
new-drive-mcp:
	-@docker rmi drive-mcp
	@docker build -t drive-mcp ./drive_mcp
