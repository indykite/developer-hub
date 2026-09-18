# App Agent Credentials

Creates credentials for an existing app agent and shows the resulting token, the X-IK-ClientKey the data-plane APIs expect.

## Run

```bash
cp .env.example .env    # fill the values
pipenv install
pipenv run python app.py   # http://localhost:5104
```

## What it sends

`POST {URL_ENDPOINTS}/configs/v1/application-agent-credentials` with `Authorization: Bearer <SA_TOKEN>`.

The example plus `application_agent_id` from the environment. `expire_days` is turned into the absolute `expire_time` the API wants, at the moment you press the button.

## What you get back

The agent token, shown once. It is the `X-IK-ClientKey` for AuthZEN, ContX IQ and Capture calls.
