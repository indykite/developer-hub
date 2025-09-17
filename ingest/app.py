import os

# Register apis
from api.capture import api_capture
from api.relationships import api_relationships
from dotenv import load_dotenv
from flask import render_template
from flask_openapi3 import Info, OpenAPI, SecurityScheme

load_dotenv()
app_token = os.getenv("APP_TOKEN")
url = os.getenv("URL_ENDPOINTS")
project_id = os.getenv("PROJECT_ID")
# Initialize OpenAPI app
info = Info(title="whatever API", version="1.0.0")
security_schemes = {"BearerToken": SecurityScheme(type="apiKey", in_="header", name="Authorization")}

app = OpenAPI(__name__, info=info, security_schemes=security_schemes)

app.register_api(api_capture)
app.register_api(api_relationships)


@app.get("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(debug=False)
