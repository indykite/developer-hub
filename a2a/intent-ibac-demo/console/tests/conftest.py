# Copyright (c) 2026 IndyKite
"""Make `app` importable when pytest runs from the console directory."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
