from typing import Optional
from flask_openapi3 import APIBlueprint
from flask_openapi3 import Tag
from pydantic import BaseModel, Field
from flask import render_template, request, redirect, url_for, flash, jsonify

import json
import os
import requests

import app

tag = Tag(name='api_relationships', description='Capture relationships')
security = [{"ApiKeyAuth": []}]


class Unauthorized(BaseModel):
    code: int = Field(-1, description="Status Code")
    message: str = Field("Unauthorized!", description="Exception Information")


api_relationships = APIBlueprint(
    'api_relationships',
    __name__,
    url_prefix='/api_relationships',
    abp_tags=[tag],
    abp_security=security,
    abp_responses={"401": Unauthorized},
    doc_ui=True
)

@api_relationships.get('/select', tags=[tag])
def select_json_file():
    json_files = [f for f in os.listdir('data/relationships') if f.endswith('.json')]
    return render_template('ingest/select_relationships_file.html', json_files=json_files)


@api_relationships.post('/create', tags=[tag])
def upsert_file():
    selected_file = request.form.get('json_file')
    if not selected_file:
        flash("No file selected", "danger")
        return redirect(url_for('api_relationships.select_json_file'))

    json_file_path = os.path.join('data/relationships', selected_file)
    with open(json_file_path, 'r') as file:
        json_data = json.load(file)

    response = requests.post(app.url + "/indykite.ingest.v1beta3.IngestAPI/BatchUpsertRelationships",
                             headers={
                                 "Content-Type": "application/json",
                                 "Authorization": f"Bearer {app.app_token}"
                             },
                             json=json_data
                             )

    try:
        response_json = response.json()
    except ValueError:
        response_json = {"message": "Invalid JSON response", "status": response.status_code}

    return render_template('ingest/result_relationships.html',
                           response_json=response_json,
                           status_code=response.status_code,
                           selected_file=selected_file)
