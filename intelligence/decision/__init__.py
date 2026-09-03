"""Decision engine package (Phase 7).

The Decision Engine selects strategies based on reasoning outputs, policy
constraints, context matching, and authority boundaries. Deterministic,
policy-respecting, and auditable.
"""

from .audit import decisions_for_task, decision_audit_summary, get_decision
from .engine import decide, MIN_CONFIDENCE
from .policies import Policy, PolicyEngine, load_default_policies
from .scorer import effective_score, raw_score
from .store import DecisionStore
from .types import CandidateStrategy, Decision, RiskLevel

__all__ = [
    "decide", "MIN_CONFIDENCE",
    "CandidateStrategy", "Decision", "RiskLevel",
    "DecisionStore",
    "Policy", "PolicyEngine", "load_default_policies",
    "effective_score", "raw_score",
    "get_decision", "decisions_for_task", "decision_audit_summary",
]
