from __future__ import annotations

import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "athena-graphs"
sys.path.insert(0, str(PLUGIN_ROOT))
