# ContX IQ Policy

Creates a ContX IQ policy: the graph pattern a knowledge query may read, and which nodes and values it may return.

## Run

```bash
cp .env.example .env    # fill the values
pipenv install
pipenv run python app.py   # http://localhost:5107
```

## What it sends

`POST {URL_ENDPOINTS}/configs/v1/authorization-policies` with `Authorization: Bearer <SA_TOKEN>`.

The example plus `project_id` from the environment. The `policy` object is sent as a JSON string, as the API expects.

## What you get back

The policy id, needed by the knowledge-query mini app.
