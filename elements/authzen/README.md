# AuthZEN Evaluation

Creates nothing. Asks the platform one question, can this subject do this action on this resource, and shows the decision.

## Run

```bash
cp .env.example .env    # fill the values
pipenv install
pipenv run python app.py   # http://localhost:5111
```

## What it sends

`POST {URL_ENDPOINTS}/access/v1/evaluation` with `X-IK-ClientKey: <APP_TOKEN>`.

The example as-is: a subject, a resource and an action.

## What you get back

The decision, `true` or `false`, with the platform's full answer below it.
