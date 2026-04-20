# banking

Banking demo app

## Get started

- clone the repo
- run: `cd banking`

The capture form is exposed at `/api_capture/create` and is pre-populated with
default banking node values so each new configuration can be created by
editing the form and submitting it.

## Requirements

    Environment created on the IndyKite platform: Service Account

## Environment variables

create .env file with the variables:

    SA_TOKEN: SA credentials token obtained on https://eu.hub.indykite.com/service-accounts (or https://us.hub.indykite.com/service-accounts)
    URL_ENDPOINTS: https://eu.api.indykite.com (or https://us.api.indykite.com)

## Install and run

- install pipenv
- run `pipenv install`
- run `pipenv shell`
- run

      flask run

- open the app by clicking the local url (like [http://127.0.0.1:5000](http://127.0.0.1:5000))
