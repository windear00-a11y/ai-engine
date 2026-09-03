"""Policy-driven risk assessment for the decision engine (Phase 7).

Risk levels follow D4: low (read-only), medium (reversible write), high
(irreversible). Risk is inferred deterministically from the strategy's tool
sequence plus caller-supplied facts, and can be overridden by the risk
policies (``intelligence/policies/risk.json``).
"""

from .types import RiskLevel

# Tool families that indicate a strategy mutates state vs is read-only.
_MUTATING_TOKENS = ("write", "edit", "create", "delete", "remove", "move",
                    "update", "execute", "run", "git", "publish", "network")
_IRREVERSIBLE_TOKENS = ("delete", "remove", "drop", "publish", "force",
                        "reset", "purge", "merge", "rebase")


def _tool_is_mutating(tool):
    lower = str(tool).lower().replace("_", ".")
    return any(tok in lower for tok in _MUTATING_TOKENS)


def _tool_is_irreversible(tool):
    lower = str(tool).lower().replace("_", ".")
    return any(tok in lower for tok in _IRREVERSIBLE_TOKENS)


def infer_risk_from_tools(tool_sequence, mutation=None, irreversible=None):
    """Determine risk level from tool usage.

    ``mutation`` / ``irreversible`` may be passed explicitly to override
    tool-derived inference (higher authority wins: an explicit irreversible
    flag forces high).
    """
    tools = list(tool_sequence or [])
    mutating = any(_tool_is_mutating(t) for t in tools)
    irreversible = any(_tool_is_irreversible(t) for t in tools)
    if irreversible:
        return RiskLevel.HIGH
    if mutating:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def apply_risk_policy(risk_level, policy_engine, facts=None):
    """Override an inferred risk level using the risk policies, if any.

    ``facts`` should include booleans ``mutation`` and ``irreversible`` so the
    risk.json policies can map them. Returns the effective risk level.
    """
    if policy_engine is None:
        return risk_level
    f = dict(facts or {})
    f.setdefault("mutation", risk_level in (RiskLevel.MEDIUM,
                                            RiskLevel.HIGH))
    f.setdefault("irreversible", risk_level == RiskLevel.HIGH)
    f["domain"] = "risk"
    applicable = [p for p in policy_engine.applicable(f)
                  if p.effect.get("risk_level")]
    if not applicable:
        return risk_level
    # Risk mapping is conservative: take the highest risk among applicable.
    order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
    return max((p.effect["risk_level"] for p in applicable),
               key=lambda r: order.get(r, 0))
