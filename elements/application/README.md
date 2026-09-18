# Application

Creates an application inside a project. Agents and their credentials hang off it.

## Run

```bash
cp .env.example .env    # fill the values
pipenv install
pipenv run python app.py   # http://localhost:5102
```

## What it sends

`POST {URL_ENDPOINTS}/configs/v1/applications` with `Authorization: Bearer <SA_TOKEN>`.

The example plus `project_id` from the environment.

## What you get back

The application id, needed by the app-agent mini app.
