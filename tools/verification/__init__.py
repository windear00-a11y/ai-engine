"""Verification — deterministic parser foundation (Layer 5 5A-1).

Read-only diagnostics, no AI, no file writes.
"""

from .diagnostic import Diagnostic
from .parser import parse

__all__ = ["Diagnostic", "parse"]
