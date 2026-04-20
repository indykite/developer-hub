# Retriever Agent

An A2A-compliant retriever agent that receives incoming messages, sends them to an Ollama LLM as prompts, and returns the LLM response. It uses the official A2A Python SDK (`a2a-sdk`) and runs as an HTTP server on a configurable port.

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
| `RETRIEVER_PORT` | `6002` | Port the agent listens on. |
| `RETRIEVER_AGENT_NAME` | `retriever_agent` | Name used in logs. |
| `LLM_MODEL` | `mistral-nemo:latest` | Ollama model name (e.g. `qwen3:14b-q8_0`, `llama3.2:latest`). |

You can set these in a `.env` file in this directory or export them before running.

## Running the Agent

```bash
python retriever_agent.py
```

The agent will start and log:

```text
Starting retriever_agent on port: 6002
```

It will then listen for incoming A2A messages on the specified port. Incoming messages are logged via the executor.

## Indykite MCP Server

When using the Indykite MCP server (`us.mcp.indykite.com`), the agent applies two workarounds:

1. **Session ID from 202 response**: Indykite returns `202 Accepted` with
   `Mcp-Session-Id` in response headers. The Python MCP SDK normally skips
   extracting the session ID for 202 responses; without it, the subsequent GET
   request fails with 404. The agent patches the SDK to extract the session ID
   from 202 responses.

2. **No session termination**: Indykite returns `403 Forbidden` on `DELETE` (session termination). The agent uses `terminate_on_close=False` to skip the termination request.

**Access token**: The MCP `Authorization: Bearer <token>` header is taken from the incoming A2A request's `Authorization` header. Callers (e.g. the orchestrator) must forward the user's token when invoking the retriever.

**Base URL** (`INDYKITE_BASE_URL`): When set, sent as `X-IndyKite-Base-URL` on all MCP requests. Use this to target a specific Indykite API region (e.g. `https://us.api.indykite.com`).

## Customization

Override `execute` in the `RetrieverExecutor` class to add your own message handling logic (e.g., retrieval, routing, or response generation). The `context.message` contains the incoming A2A message; use `get_message_text(context.message)` to extract plain text.
