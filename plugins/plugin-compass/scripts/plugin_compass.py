"""Portable source-tree launcher for Plugin Compass."""

from __future__ import annotations

import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from plugin_compass.cli import main  # noqa: E402


raise SystemExit(main())
