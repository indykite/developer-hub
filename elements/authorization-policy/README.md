# Authorization Policy

Creates a knowledge-based access control policy: who may do which action on which resource, as a graph pattern.

## Run

```bash
cp .env.example .env    # fill the values
pipenv install
pipenv run python app.py   # http://localhost:5106
```

## What it sends

`POST {URL_ENDPOINTS}/configs/v1/authorization-policies` with `Authorization: Bearer <SA_TOKEN>`.

The example plus `project_id` from the environment. The `policy` object is sent as a JSON string, as the API expects.

## What you get back

The policy id.
