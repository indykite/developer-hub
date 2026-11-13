# Copyright (c) 2026 IndyKite
"""Make `server` importable before the test modules import it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
