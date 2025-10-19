# get started

Codes examples to clone and test locally

## Get started with the IndyKite platform using REST APIs

- clone the repo
- run: `cd get-started`

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
