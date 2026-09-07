"""Action / Observation / Verification / Outcome boundary (Phase 28 + 29).

Phase 28 defined the contracts for the later stages WITHOUT implementing
them. Phase 29 supplies their canonical implementation (authority.py,
action.py, observation.py, verification.py plus the LifecycleService
execution methods). Nothing may bypass the trust/authority architecture:
every ACTION consumes an approved Plan step; OBSERVATION records what
actually happened; VERIFICATION determines whether the expected result was
satisfied from evidence; OUTCOME records the verified result.

These are pure contract dataclasses only. ``PHASE_29_IMPLEMENTED`` is True
because Phase 29 supplies the real canonical implementation.
"""

from dataclasses import dataclass, field
from typing import Optional

BOUNDARY_STAGES = ("ACTION", "OBSERVATION", "VERIFICATION", "OUTCOME")

PHASE_29_IMPLEMENTED = True


@dataclass(frozen=True)
class ActionStep:
    """One approved plan step consumed by the (Phase 29) ACTION stage."""

    plan_id: str
    step_id: str
    action: str
    inputs: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ActionRequest:
    """An ACTION request. ``approval_token`` must be supplied by the Phase 29
    authority layer; Phase 28 never generates approval."""

    plan_id: str
    step: ActionStep
    approval_token: Optional[str] = None


@dataclass(frozen=True)
class ActionResult:
    """The contract an ACTION implementation returns (Phase 29)."""

    step_id: str
    executed: bool = False
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ObservationRecord:
    """Records what actually happened for a step (Phase 29)."""

    step_id: str
    observed: dict = field(default_factory=dict)


@dataclass(frozen=True)
class VerificationResult:
    """Determines whether the expected result was satisfied (Phase 29)."""

    step_id: str
    satisfied: bool = False
    evidence_ids: tuple = ()


@dataclass(frozen=True)
class OutcomeRecord:
    """Records the verified result (Phase 29)."""

    step_id: str
    classification: str = "unknown"
    verification_result: Optional[VerificationResult] = None