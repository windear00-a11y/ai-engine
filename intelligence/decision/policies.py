"""Policy constraint checking for the decision engine (Phase 7).

Policies are human-authored configuration (Decision 8: JSON config files
under ``intelligence/policies/``), NOT learned and never modified by the
system (D5, Phase 8 invariant "learning never modifies policy"). The decision
engine checks candidates against these policies and eliminates any that
violate them, recording the violation in the decision rationale.

A Policy is a declarative rule:
    {
        "policy_id": "approval_required_for_mutating",
        "domain": "decide",
        "when": {"problem_class": ["bug_fix", "project_mutation"]},
        "effect": {"approval_required": True},
        "description": "mutating problem classes always require approval"
    }

``when`` matches are resolved against a fact dict (task_type, risk_level,
domain, strategy tool names, etc.). Matching rules: an empty ``when`` applies
always; otherwise every key must be present with a matching value or set
containing the value.
"""

import json
import os


class Policy:
    """A single declarative decision policy."""

    __slots__ = ("policy_id", "domain", "when", "effect", "description")

    def __init__(self, policy_id, domain="decide", when=None, effect=None,
                 description=""):
        self.policy_id = policy_id
        self.domain = domain or "decide"
        self.when = dict(when or {})
        self.effect = dict(effect or {})
        self.description = description

    def to_dict(self):
        return {
            "policy_id": self.policy_id,
            "domain": self.domain,
            "when": self.when,
            "effect": self.effect,
            "description": self.description,
        }


def _matches(when, facts):
    """True when ``when`` applies to ``facts``.

    An empty ``when`` applies always. For each key, ``facts[key]`` must equal
    ``value``, or be contained in ``value`` when ``value`` is a list/tuple/set.
    """
    if not when:
        return True
    for key, value in when.items():
        actual = facts.get(key)
        if isinstance(value, (list, tuple, set)):
            if actual not in value:
                return False
        else:
            if actual != value:
                return False
    return True


class PolicyEngine:
    """Checks candidate decisions against the policy set.

    ``check(facts)`` returns ``(approved, violations)`` where ``violations``
    lists the policies whose conditions matched. When a candidate matches a
    policy with effect ``{"approval_required": True}``, the engine marks the
    decision as requiring approval rather than eliminating it outright; other
    effects (e.g. ``blocked``) eliminate the candidate.
    """

    def __init__(self, policies=None, policies_dir=None):
        if policies is not None:
            self.policies = list(policies)
        else:
            self.policies = []
            if policies_dir:
                self.policies = _load_policies_dir(policies_dir)

    def applicable(self, facts):
        """Return the policies that apply to ``facts``, in policy_id order."""
        return [p for p in sorted(self.policies, key=lambda p: p.policy_id)
                if p.domain == (facts.get("domain") or "decide")
                and _matches(p.when, facts)]

    def check(self, facts):
        """Return ``(blocked, requires_approval, applicable_policies)``.

        ``blocked`` is True if any applicable policy has
        effect {"blocked": True}. ``requires_approval`` is True if any
        applicable policy has effect {"approval_required": True}.
        """
        applicable = self.applicable(facts)
        blocked = any(p.effect.get("blocked") for p in applicable)
        req_approval = any(p.effect.get("approval_required")
                           for p in applicable)
        return (blocked, req_approval, applicable)


def policy_from_dict(d):
    return Policy(
        policy_id=d.get("policy_id"),
        domain=d.get("domain"),
        when=d.get("when"),
        effect=d.get("effect"),
        description=d.get("description", ""),
    )


def _load_policies_dir(policies_dir):
    loaded = []
    if not os.path.isdir(policies_dir):
        return loaded
    for fname in sorted(os.listdir(policies_dir)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(policies_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if isinstance(data, list):
            for item in data:
                loaded.append(policy_from_dict(item))
        elif isinstance(data, dict):
            loaded.append(policy_from_dict(data))
    return loaded


def default_policies_dir():
    _root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(_root, "intelligence", "policies")


def load_default_policies():
    return _load_policies_dir(default_policies_dir())


__all__ = [
    "Policy", "PolicyEngine", "load_default_policies",
    "default_policies_dir", "policy_from_dict",
]
