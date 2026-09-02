"""Verification — deterministic parser foundation (Layer 5).

Read-only diagnostics, no AI, no file writes. Also re-exports the Layer 5B
FixProposal model and the deterministic fix-rule dispatcher.
"""

from .diagnostic import Diagnostic
from .parser import parse
from .fix_proposal import FixProposal, make_fix_proposal
from .fix_rules import dispatch, match

__all__ = ["Diagnostic", "parse", "FixProposal", "make_fix_proposal",
           "dispatch", "match"]
