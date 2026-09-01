"""Plugin Compass: read-only Codex capability decision support."""

from .adapters.codex import discover_plugins
from .decision import build_recommendation_plan
from .metadata import enrich_plugins
from .repository import inspect_repository

__all__ = [
    "build_recommendation_plan",
    "discover_plugins",
    "enrich_plugins",
    "inspect_repository",
]
__version__ = "0.1.0"
