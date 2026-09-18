# ContX IQ Knowledge Query

Creates a knowledge query bound to a ContX IQ policy: the projection of nodes, relationships and values an agent gets back.

## Run

```bash
cp .env.example .env    # fill the values
pipenv install
pipenv run python app.py   # http://localhost:5108
```

## What it sends

`POST {URL_ENDPOINTS}/configs/v1/knowledge-queries` with `Authorization: Bearer <SA_TOKEN>`.

The example plus `project_id` and `policy_id` from the environment. The `query` object is sent as a JSON string, as the API expects.

## What you get back

The knowledge query id. Agents call it by name or id through `ciq_execute`.
