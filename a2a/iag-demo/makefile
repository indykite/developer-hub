default:
	@echo "Removing old Docker images..."
	-@docker rmi chatbot
	-@docker rmi retriever-agent
	-@docker rmi orchestrator-agent
	-@docker rmi weather-agent
	@echo "Building new Docker images..."
	@docker build -t chatbot ./chatbot
	@docker build -t orchestrator-agent ./orchestrator_agent
	@docker build -t retriever-agent ./retriever_agent
	@docker build -t weather-agent ./weather_agent

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
