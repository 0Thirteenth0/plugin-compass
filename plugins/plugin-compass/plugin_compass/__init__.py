"""Plugin Compass: read-only Codex capability decision support."""

from .adapters.codex import discover_plugins
from .adapters.standalone import (
    ConfiguredSkillRoot,
    DiscoveryDiagnostic,
    DiscoveryLimits,
    StandaloneDiscoveryResult,
    discover_standalone_skills,
)
from .decision import build_recommendation_plan
from .metadata import enrich_plugins
from .repository import inspect_repository

__all__ = [
    "build_recommendation_plan",
    "ConfiguredSkillRoot",
    "DiscoveryDiagnostic",
    "DiscoveryLimits",
    "StandaloneDiscoveryResult",
    "discover_plugins",
    "discover_standalone_skills",
    "enrich_plugins",
    "inspect_repository",
]
__version__ = "0.1.0"
