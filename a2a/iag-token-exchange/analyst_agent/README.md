# Analyst Agent

An A2A-compliant analyst agent modeled on the retriever agent: it receives
incoming messages, drives an LLM tool-calling loop, and uses the IndyKite MCP
server (through the MCP-protecting IAG) for data retrieval and analysis.
Unlike the retriever, it is not called by the orchestrator — users (e.g.
millicent) call its gateway directly, which is what the WF4 "parallel multi-agent
MCP" flow demonstrates: two agents (retriever + analyst) and two users
(millicent + carol) holding concurrent MCP sessions through the same MCP gateway.

## Configuration

Environment variables (see `.example.env` at the repo root):

- `ANALYST_PORT` (default `6005`) - HTTP port of the agent
- `ADVERTISED_HOST` - hostname advertised in the agent card
- `ANALYST_AGENT_NAME` (default `analyst_agent`)
- `MCP_SERVER_URL` - MCP endpoint; the compose file points this at the MCP-protecting IAG (`mcp-iag`)
- `INDYKITE_BASE_URL` - IndyKite API base URL (sent as `X-IndyKite-Base-URL`)
- LLM settings: `GEMINI_ENABLED`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `LLM_MODEL`, `OLLAMA_HOST`

The agent extracts the Bearer token forwarded by its protecting gateway (`analyst-iag`) from the incoming request and reuses it for the MCP session, so per-user authorization applies end to end.

## Graph model

In the IndyKite Knowledge Graph the analyst is the `indykiteagent-4` Agent node, invoked by workflow `wf3`:
`millicent -CAN_TRIGGER-> wf3 -INVOKES-> indykiteagent-4 -INVOKES-> indykiteagent-mcp` (edges carry `workflow_name: wf3`; the shared edge into the MCP agent also carries `discriminating_property: workflow_name`).

## Run

Built and started via the repo-level `make` + `docker compose up`; standalone:

```bash
pip install -r requirements.txt
python analyst_agent.py
```
