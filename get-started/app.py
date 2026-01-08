import logging
import os

# Register apis
from api.app_agent import api_app_agent
from api.application import api_application
from api.authorization_policy import api_authorization_policy
from api.authzen import api_authzen
from api.capture import api_capture
from api.ciq_execute import api_ciq_execute
from api.ciq_knowledge_query import api_ciq_knowledge_query
from api.ciq_policy import api_ciq_policy
from api.project import api_project
from api.relationships import api_relationships
from api.token_introspect import api_token_introspect
from dotenv import load_dotenv
from flask import render_template
from flask_openapi3 import Info, OpenAPI, SecurityScheme

logger = logging.getLogger(__name__)

# Configure logging - logs go to BOTH console AND file
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),  # Logs to console/terminal
        logging.FileHandler("flask_app.log"),  # Logs to file flask_app.log
    ],
)

# Set logging level for api modules
logging.getLogger("api.project").setLevel(logging.DEBUG)
logging.getLogger("api.application").setLevel(logging.DEBUG)
logging.getLogger("api.app_agent").setLevel(logging.DEBUG)
logging.getLogger("api.token_introspect").setLevel(logging.DEBUG)
logging.getLogger("api.authorization_policy").setLevel(logging.DEBUG)
logging.getLogger("api.capture").setLevel(logging.DEBUG)
logging.getLogger("api.relationships").setLevel(logging.DEBUG)
logging.getLogger("api.authzen").setLevel(logging.DEBUG)
logging.getLogger("api.ciq_policy").setLevel(logging.DEBUG)
logging.getLogger("api.ciq_knowledge_query").setLevel(logging.DEBUG)
logging.getLogger("api.ciq_execute").setLevel(logging.DEBUG)

# Log that the app is starting
logger.info("=" * 50)
logger.info("Flask application starting...")
logger.info("=" * 50)

load_dotenv()
sa_token = os.getenv("SA_TOKEN")
app_token = os.getenv("APP_TOKEN")
url = os.getenv("URL_ENDPOINTS")
project_id = os.getenv("PROJECT_ID")
# Initialize OpenAPI app
info = Info(title="whatever API", version="1.0.0")
security_schemes = {
    "ApiKeyAuth": SecurityScheme(
        type="apiKey",
        in_="header",  # could also be "query"
        name="X-IK-ClientKey",  # the header you expect
    ),
    "BearerToken": SecurityScheme(type="apiKey", in_="header", name="Authorization"),
}
app = OpenAPI(__name__, info=info, security_schemes=security_schemes)

app.register_api(api_project)
app.register_api(api_application)
app.register_api(api_app_agent)
app.register_api(api_token_introspect)
app.register_api(api_authorization_policy)
app.register_api(api_capture)
app.register_api(api_relationships)
app.register_api(api_authzen)
app.register_api(api_ciq_policy)
app.register_api(api_ciq_knowledge_query)
app.register_api(api_ciq_execute)


@app.get("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(debug=False)
