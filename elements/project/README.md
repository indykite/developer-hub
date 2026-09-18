# Project

Creates a project in your organization. Everything else lives inside a project.

## Run

```bash
cp .env.example .env    # fill the values
pipenv install
pipenv run python app.py   # http://localhost:5101
```

## What it sends

`POST {URL_ENDPOINTS}/configs/v1/projects` with `Authorization: Bearer <SA_TOKEN>`.

The example plus `organization_id` from the environment. The name must match `^[a-z][a-z0-9-]*$`.

## What you get back

The project id. Note that the graph behind a new project takes a few minutes to become active.
