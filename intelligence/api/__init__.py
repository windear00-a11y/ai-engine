"""Intelligence API package (Phase 10)."""

from .contract import INTELLIGENCE_CONTRACT_VERSION, operations, validate_request
from .handler import IntelligenceAPI, IntelligenceToolInterface

__all__ = ["INTELLIGENCE_CONTRACT_VERSION", "operations", "validate_request",
           "IntelligenceAPI", "IntelligenceToolInterface"]
