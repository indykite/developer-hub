import base64
import json
import logging
import os
from pathlib import Path

from api._music_data import CIQ_POLICIES, CIQ_QUERIES, EVALUATIONS, KBACS

# Register apis
from api.app_agent import api_app_agent
from api.application import api_application
from api.authorization_policy import api_authorization_policy
from api.authzen import api_authzen
from api.capture import api_capture
from api.chat import api_chat
from api.ciq_execute import api_ciq_execute
from api.ciq_knowledge_query import api_ciq_knowledge_query
from api.ciq_policy import api_ciq_policy
from api.mcp_server import api_mcp_server
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
logging.getLogger("api.mcp_server").setLevel(logging.DEBUG)
logging.getLogger("api.chat").setLevel(logging.DEBUG)

# Log that the app is starting
logger.info("=" * 50)
logger.info("Flask application starting...")
logger.info("=" * 50)

# Load .env from the same directory as app.py so `flask run` works regardless of
# the current working directory. override=True picks up edits made by /api_*/create
# routes (which append IDs/tokens to the same file) on subsequent reloads.
ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE, override=True)
sa_token = os.getenv("SA_TOKEN")
app_token = os.getenv("APP_TOKEN")
url = os.getenv("URL_ENDPOINTS")
project_id = os.getenv("PROJECT_ID")
# Initialize OpenAPI app
info = Info(title="Music API", version="1.0.0")
security_schemes = {
    "ApiKeyAuth": SecurityScheme(
        type="apiKey",
        in_="header",
        name="X-IK-ClientKey",
    ),
    "BearerToken": SecurityScheme(type="apiKey", in_="header", name="Authorization"),
}
app = OpenAPI(__name__, info=info, security_schemes=security_schemes)

app.register_api(api_project)
app.register_api(api_application)
app.register_api(api_app_agent)
app.register_api(api_token_introspect)
app.register_api(api_mcp_server)
app.register_api(api_authorization_policy)
app.register_api(api_capture)
app.register_api(api_relationships)
app.register_api(api_authzen)
app.register_api(api_ciq_policy)
app.register_api(api_ciq_knowledge_query)
app.register_api(api_ciq_execute)
app.register_api(api_chat)


def _slot_suffix(slot: str) -> str:
    return "" if slot == "1" else slot


def _decode_jwt_unsafe(token: str) -> dict:
    """Decode a JWT without verifying the signature — for diagnostics only."""
    try:
        payload_b64 = token.split(".")[1]
        # Pad to a multiple of 4 for urlsafe_b64decode.
        padding = "=" * ((4 - len(payload_b64) % 4) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    except Exception as e:
        return {"error": str(e)}


@app.get("/diagnostics")
def diagnostics():
    """Show which project/app each token currently in .env belongs to.

    Useful when AuthZen returns `decision: false` for evaluations that the dataset
    should allow — usually means PROJECT_ID and APP_TOKEN are pointing at different
    IndyKite projects (KBACs created in project A, data captured into project B).
    """
    sa = os.getenv("SA_TOKEN", "")
    at = os.getenv("APP_TOKEN", "")
    ut = os.getenv("USER_TOKEN", "")

    def summarize(name: str, tok: str) -> dict:
        if not tok:
            return {"name": name, "present": False}
        claims = _decode_jwt_unsafe(tok)
        # Common IndyKite claim names; show whichever are present.
        keep = {
            k: claims.get(k)
            for k in (
                "iss",
                "aud",
                "sub",
                "tid",
                "project_id",
                "app_id",
                "app_agent_id",
                "app_space_id",
                "service_account_id",
                "exp",
            )
            if k in claims
        }
        return {"name": name, "present": True, "length": len(tok), "claims": keep}

    return render_template(
        "diagnostics.html",
        env_file=str(ENV_FILE),
        env_summary={
            "PROJECT_ID": os.getenv("PROJECT_ID", ""),
            "APPLICATION_ID": os.getenv("APPLICATION_ID", ""),
            "APP_AGENT_ID": os.getenv("APP_AGENT_ID", ""),
            "TOKEN_INTROSPECT_ID": os.getenv("TOKEN_INTROSPECT_ID", ""),
            "URL_ENDPOINTS": os.getenv("URL_ENDPOINTS", ""),
            "ORGANIZATION_ID": os.getenv("ORGANIZATION_ID", ""),
        },
        tokens=[
            summarize("SA_TOKEN", sa),
            summarize("APP_TOKEN", at),
            summarize("USER_TOKEN", ut),
        ],
    )


@app.get("/")
def index():
    """Render the music app landing page."""
    # Build a list of (policy, queries[]) tuples so the index can show each policy
    # together with the variants of its knowledge query.
    ciq_use_cases = []
    for pol in CIQ_POLICIES:
        variants = [q for q in CIQ_QUERIES if q["policy_slot"] == pol["slot"]]
        ciq_use_cases.append({"policy": pol, "queries": variants})

    return render_template(
        "index.html",
        kbacs=KBACS,
        evaluations=EVALUATIONS,
        ciq_use_cases=ciq_use_cases,
        slot_suffix=_slot_suffix,
    )


if __name__ == "__main__":
    app.run(debug=False)
