# Canbank Demo

Greetings. I am going to give you a little tour of the IndyKite Agent Gateway
and the IndyKite MCP server.

## Background

Canbank is a retail bank. Cankbank is adding AI agents to help their employees
support their customers. In this demo there is a chatbot and two agents deployed: an orchestrator and a retriever. Each agent is protected by the IndyKite
The retriever agent is using the IndyKite MCP server to answer questions.
Throughout the demo the gateway checks the calling chain from the original requester through to the agent to ensure the request can proceed to the next agent.

There are three personas involved in the demo. They are:

1. Leslie - Customer Service Rep - CSR 2
2. Rebecca - Customer with a credit card and a trading account
3. Roy - a retail trader

## Prompts

These are the prompts used in the demo.

### Refund policy documents

Use this prompt after you log in as Leslie.

What policy documents pertain to refunds?

### Past decisions

Retrieve past decisions that incorporated the 'refund_policy' document.

### Stock quote

Tell me how many shares of NVDA the user with the id: rebecca can purchase

## Setup

There are three things required for the demo:

1. IndyKite chatbot
2. terminal with docker ps -q --filter "name=iag-demo-retriever-1" | xargs docker logs -f or
   docker logs -f $(docker ps -q --filter "name=iag-demo-retriever-1")
3. IK data explorer centered on external_id=decision_001

Have a browser with both the IndyKite Hub and the IndyKite chatbot.
Have the browser split

## Script

Hi, I am Dave from IndyKite, I am going to give you a quick tour of IndyKite Agent Control.

Canbank is a fictional retail bank.

Cankbank is adding AI agents to help their employees provide better support for their customers.

In this demo there is a chatbot and two agents deployed: an orchestrator and a retriever.

Each agent is protected by the IndyKite Agent Gateway.

The retriever agent uses the IndyKite MCP server to answer questions about Canbank.

Throughout the demo the gateways check the calling chain from the original requester through to the agent to ensure the request can proceed to the next agent.

There are three personas involved in the demo. They are:

1. Leslie - Customer Service Rep - CSR 2
2. Rebecca - Customer with a credit card and a trading account
3. Roy - a retail trader

Before we get started let's take a look at some of the data in the IndyKite platform.

The IndyKite TrustScore allows the enterprise to score the risk of their data.

It does this by taking the user's token and the agents token and calling the IdP's token exchange service. The token exchange returns a new token with the user as the subject and the agent as the actor. The agent is acting on the subject's behalf.
