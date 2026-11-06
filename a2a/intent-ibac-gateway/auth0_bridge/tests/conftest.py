# Copyright (c) 2026 IndyKite
"""Make `server` importable and give it a tenant to configure itself from, before the test modules import it."""

import os
import sys
from pathlib import Path

os.environ.setdefault("AUTH0_DOMAIN", "tenant.test.auth0.com")
os.environ.setdefault("AUTH0_DEFAULT_AUDIENCE", "ibac-agents")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
