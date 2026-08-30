"""Safety & Permission Foundation (Phase 1A).

Deterministic-first permission/safety layer for the coding engine. It
provides:

* :class:`Policy`             -- declarative, versioned permission policy.
* :class:`PathPolicy`         -- workspace zone classification (on Workspace).
* :class:`ApprovalGate`       -- deterministic approval state machine.
* :class:`EngineState`        -- single SQLite state store (journal + audit).
* :func:`run_checked`         -- hardened command execution primitives.

No AI, no RBAC, no network, no git, no publish. All non-read operations
require explicit approval by default.
"""

from tools.permissions.policy import Policy, load_policy, POLICY_VERSION
from tools.permissions.pathpolicy import (
    PathPolicy, Zone, hard_write_guard,
)
from tools.permissions.operations import WriteTier, classify_write
from tools.permissions.decisions import (
    Decision, DecisionKind, Domain, allow, deny, require_approval,
)
from tools.permissions.approvalgate import (
    ApprovalGate, ApprovalStatus, OpStatus,
)
from tools.permissions.journal import EngineState, deterministic_id, \
    checksum_bytes
from tools.permissions.rollback import RollbackExecutor
from tools.permissions.audit import AuditLog
from tools.permissions.execution import (
    run_checked, check_args, filter_env, CommandDenied,
)

__all__ = [
    "Policy", "load_policy", "POLICY_VERSION",
    "PathPolicy", "Zone", "hard_write_guard",
    "WriteTier", "classify_write",
    "Decision", "DecisionKind", "Domain", "allow", "deny", "require_approval",
    "ApprovalGate", "ApprovalStatus", "OpStatus",
    "EngineState", "deterministic_id", "checksum_bytes",
    "RollbackExecutor",
    "AuditLog",
    "run_checked", "check_args", "filter_env", "CommandDenied",
]
