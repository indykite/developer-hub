# Orchestrator Agent

An A2A-compliant orchestrator agent that receives incoming messages, sends
them to an Ollama LLM as prompts, and returns the LLM response. It can search
the web via an internal DuckDuckGo tool when the LLM decides additional
information is needed. It uses the official A2A Python SDK (`a2a-sdk`) and
runs as an HTTP server on a configurable port.

## Requirements

- Python 3.10+
- `a2a-sdk` and `uvicorn`

## Installation

1. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

## Configuration

Environment variables (optional):

| Variable | Default | Description |
| ---------- | --------- | ------------- |
| `ORCHESTRATOR_PORT` | `6001` | Port the agent listens on. |
| `ORCHESTRATOR_AGENT_NAME` | `orchestrator_agent` | Name used in logs. |
| `LLM_MODEL` | `mistral-nemo:latest` | Ollama model name (e.g. `qwen3:14b-q8_0`, `llama3.2:latest`). |

You can set these in a `.env` file in this directory or export them before running.

## Running the Agent

```bash
python orchestrator_agent.py
```

The agent will start and log:

```text
Starting agent: orchestrator_agent on port: 6001
```

It will then listen for incoming A2A messages on the specified port. Incoming messages are logged via the `on_message` handler.

## Customization

Override `execute` in the `OrchestratorExecutor` class to add your own message handling logic (e.g., routing, delegation, or response generation). The `context.message` contains the incoming A2A message; use `get_message_text(context.message)` to extract plain text.

## Web Search

The orchestrator includes a DuckDuckGo web search tool. When users ask about current events, facts, or information that may change, the LLM can invoke the tool to fetch up-to-date results and incorporate them into the response.
