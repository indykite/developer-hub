# Copyright (c) 2026 IndyKite
"""Make `app` importable with the IBAC preview switched on, before the test modules import it."""

import os
import sys
from pathlib import Path

os.environ.setdefault("IBAC_CARD_URL", "http://orchestrator:7001/.well-known/agent-card.json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
