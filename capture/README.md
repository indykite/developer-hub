# developer-hub

Codes examples to clone and test locally

## Capture data into your IKG (Aura instance) in the IndyKite platform using the Capture REST API

    Capture nodes
    Capture relationships

## Requirements

Environment created on the IndyKite platform: Project, Application, AppAgent, AppAgent Credentials using :

- <https://eu.api.indykite.com> (or <https://us.api.indykite.com>),
- the Terraform plugin(<https://registry.terraform.io/providers/indykite/indykite/latest>) or
- the REST APIs (<https://openapi.indykite.com/>).

## Environment variables

    APP_TOKEN: AppAgent token from the AppAgent credentials
    URL_ENDPOINTS: https://eu.api.indykite.com (or https://us.api.indykite.com)
    PROJECT_ID: gid of your project on the IK platform

## Install and run

- install pipenv
- run `pipenv install`
- run `pipenv shell`
- export the required env variables
- run

      flask run

- open the app by clicking the local url (like [http://127.0.0.1:5000](http://127.0.0.1:5000))
