# developer-hub

Codes examples to clone and test locally

- Ingest data into your IKG (Aura instance) in the IndyKite platform using the Ingest gRPC API
- Capture data into your IKG (Aura instance) in the IndyKite platform using the Capture REST API

Endpoints definitions: <https://openapi.indykite.com>

## How to use

- clone the repo
- to capture data with the Capture API, run: `cd capture`
- to capture data with the Ingest API, run: `cd ingest`
- install pipenv
- run `pipenv install`
- run `pipenv shell`
- export the required env variables (check the README in each folder)
- run

  ```sh
  flask run
  ```

- open the app by clicking the local url (like [http://127.0.0.1:5000](http://127.0.0.1:5000))
