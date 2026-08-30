"""Permission decision types and reason codes for the Safety/Permission layer.

A decision is a pure, deterministic value: it carries no side effects, no
timestamps, and no randomness. The classification logic that produces these
decisions therefore never depends on when something happened -- only on the
policy and the subject being evaluated.
"""

from enum import Enum


class DecisionKind(Enum):
    """The three possible outcomes of a permission check (fail-closed)."""
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class Domain(Enum):
    """Permission domains evaluated by the engine."""
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    ROLLBACK = "rollback"
    GIT = "git"
    NETWORK = "network"
    PUBLISH = "publish"          # publish/deploy


# -- reason codes -----------------------------------------------------------
# Stable, machine-readable codes. Tests and audit records reference these
# exact strings, so they are part of the stable interface.

REASON_OUTSIDE_WORKSPACE = "outside_workspace"
REASON_BLOCKED = "blocked"
REASON_READONLY = "readonly"
REASON_PROTECTED = "protected"
REASON_NO_APPROVAL = "approval_required"
REASON_NO_SNAPSHOT = "snapshot_required"
REASON_NOT_ALLOWED = "not_allowed"
REASON_NETWORK_DENIED = "network_denied"
REASON_GIT_DENIED = "git_denied"
REASON_PUBLISH_DENIED = "publish_denied"
REASON_ROLLBACK_CONFLICT = "rollback_conflict"


class Decision:
    """An immutable permission decision.

    Attributes
    ----------
    kind : DecisionKind
    reason_code : str or None
    approval_id : str or None
        Set only when ``kind == REQUIRE_APPROVAL`` and an approval record has
        been requested (deterministic operation id).
    required_scope : str or None
        Human/operator-facing scope describing what must be approved.
    """

    __slots__ = ("kind", "reason_code", "approval_id", "required_scope")

    def __init__(self, kind, reason_code=None, approval_id=None,
                 required_scope=None):
        self.kind = kind
        self.reason_code = reason_code
        self.approval_id = approval_id
        self.required_scope = required_scope

    def as_dict(self):
        out = {"decision": self.kind.value}
        if self.reason_code is not None:
            out["reason_code"] = self.reason_code
        if self.approval_id is not None:
            out["approval_id"] = self.approval_id
        if self.required_scope is not None:
            out["required_scope"] = self.required_scope
        return out

    def __repr__(self):
        return ("Decision(kind=%s, reason_code=%s, approval_id=%s)"
                % (self.kind.value, self.reason_code, self.approval_id))


def allow():
    return Decision(DecisionKind.ALLOW)


def deny(reason_code=REASON_NOT_ALLOWED):
    return Decision(DecisionKind.DENY, reason_code=reason_code)


def require_approval(reason_code, approval_id=None, required_scope=None):
    return Decision(DecisionKind.REQUIRE_APPROVAL,
                    reason_code=reason_code, approval_id=approval_id,
                    required_scope=required_scope)
