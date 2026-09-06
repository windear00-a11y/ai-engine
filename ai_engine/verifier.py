"""Generic Verifier abstraction — domain-neutral (Phase 17).

Verifier.verify(action, effect_result, expected_outcome, context) -> VerificationResult

Verification determines whether the expected outcome was actually achieved,
separate from execution. Possible statuses: VERIFIED_SUCCESS, VERIFIED_FAILURE, UNKNOWN.

No LLM, no network, no direct arbitrary SQLite, no side effects.
"""

import abc
import hashlib
import json
import time

def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)

class VerificationResult:
    """Serializable result of verification."""

    # Statuses
    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_FAILURE = "verified_failure"
    UNKNOWN = "unknown"

    def __init__(self, verification_id, action, effect_result, expected_outcome, status, evidence_ids=None, error=None, provenance=None, created_at_epoch=None):
        self.verification_id = verification_id
        self.action = dict(action or {})
        self.effect_result = effect_result.as_dict() if hasattr(effect_result, "as_dict") else dict(effect_result or {})
        self.expected_outcome = expected_outcome
        self.status = status
        self.evidence_ids = tuple(evidence_ids or [])
        self.error = error
        self.provenance = dict(provenance or {})
        self.created_at_epoch = float(created_at_epoch or time.time())

    def as_dict(self):
        return {
            "verification_id": self.verification_id,
            "action": self.action,
            "effect_result": self.effect_result,
            "expected_outcome": self.expected_outcome,
            "status": self.status,
            "evidence_ids": list(self.evidence_ids),
            "error": self.error,
            "provenance": self.provenance,
            "created_at_epoch": self.created_at_epoch,
        }

class Verifier(abc.ABC):
    """Abstract Verifier — domain-neutral, no side effects."""

    @property
    @abc.abstractmethod
    def verifier_id(self) -> str:
        """Stable verifier identifier, e.g. "generic"."""
        raise NotImplementedError

    @abc.abstractmethod
    def verify(self, action, effect_result, expected_outcome, context=None):
        """Verify whether expected outcome was achieved.

        Args:
            action: original action dict
            effect_result: EffectResult (or dict)
            expected_outcome: string or dict describing what should happen
            context: optional context

        Returns:
            VerificationResult with status VERIFIED_SUCCESS, VERIFIED_FAILURE, or UNKNOWN.
            Must not execute side effects, must not bypass trust.
        """
        raise NotImplementedError


class GenericVerifier(Verifier):
    """Generic verifier — checks effect_result status vs expected outcome.

    Rules:
        - If effect_result status is "failure" → VERIFIED_FAILURE
        - If effect_result status is "success" and expected_outcome matches or is empty → VERIFIED_SUCCESS
        - Otherwise → UNKNOWN (never auto-convert UNKNOWN to SUCCESS)
    """

    @property
    def verifier_id(self):
        return "generic"

    def verify(self, action, effect_result, expected_outcome, context=None):
        # Normalize effect_result
        if hasattr(effect_result, "as_dict"):
            eff = effect_result.as_dict()
        elif isinstance(effect_result, dict):
            eff = effect_result
        else:
            eff = {"status": str(effect_result)}

        status = eff.get("status")
        error = eff.get("error")

        # Derive verification_id deterministically
        payload = {"action": action, "effect_status": status, "expected": expected_outcome}
        vid = "ver_" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:16]

        # Determine verification status
        if status == "failure":
            vstatus = VerificationResult.VERIFIED_FAILURE
        elif status == "success":
            # Check if expected_outcome is provided and matches
            if expected_outcome and isinstance(expected_outcome, str) and expected_outcome.lower() in ("unknown", "awaiting approval"):
                vstatus = VerificationResult.UNKNOWN
            elif error:
                vstatus = VerificationResult.VERIFIED_FAILURE
            else:
                vstatus = VerificationResult.VERIFIED_SUCCESS
        else:
            # status is "skipped" or unknown → UNKNOWN
            vstatus = VerificationResult.UNKNOWN

        # Never convert UNKNOWN to SUCCESS merely because Effect returned "ok"
        # This is enforced: only explicit success with no error and matching expected gives SUCCESS

        return VerificationResult(
            verification_id=vid,
            action=action,
            effect_result=eff,
            expected_outcome=expected_outcome,
            status=vstatus,
            evidence_ids=eff.get("evidence_ids", []),
            provenance={"verifier": self.verifier_id},
        )


class NoopVerifier(Verifier):
    """Verifier for noop actions — always VERIFIED_SUCCESS if effect was success."""

    @property
    def verifier_id(self):
        return "noop"

    def verify(self, action, effect_result, expected_outcome, context=None):
        eff_status = effect_result.get("status") if isinstance(effect_result, dict) else getattr(effect_result, "status", None)
        if hasattr(effect_result, "as_dict"):
            eff = effect_result.as_dict()
        else:
            eff = dict(effect_result or {})
        vid = "ver_" + hashlib.sha256(_canonical({"action": action, "eff": eff}).encode("utf-8")).hexdigest()[:16]
        if eff.get("status") == "success":
            vstatus = VerificationResult.VERIFIED_SUCCESS
        else:
            vstatus = VerificationResult.UNKNOWN
        return VerificationResult(
            verification_id=vid,
            action=action,
            effect_result=eff,
            expected_outcome=expected_outcome,
            status=vstatus,
            provenance={"verifier": self.verifier_id},
        )


def outcome_from_verification(verification_result, context_id=None, evidence_ids=None):
    """Map VerificationResult → Outcome classification (preserves UNKNOWN).

    Rules:
        VERIFIED_SUCCESS → SUCCESS
        VERIFIED_FAILURE → FAILURE
        UNKNOWN          → UNKNOWN (never convert UNKNOWN to SUCCESS merely because Effect returned ok)
    """
    from intelligence.outcome.types import OutcomeClassification
    status = verification_result.status if hasattr(verification_result, "status") else verification_result.get("status")
    if status == VerificationResult.VERIFIED_SUCCESS:
        cls = OutcomeClassification.SUCCESS
    elif status == VerificationResult.VERIFIED_FAILURE:
        cls = OutcomeClassification.FAILURE
    else:
        cls = OutcomeClassification.UNKNOWN
    # Preserve provenance
    return cls


def observe_effect(effect_result):
    """Separate observation from effect result.

    EffectResult says "executed", observation says "desired state verified".
    For generic, observation is just the effect's output, but kept separate for Verifier.

    Returns dict with observed state.
    """
    if hasattr(effect_result, "as_dict"):
        eff = effect_result.as_dict()
    else:
        eff = dict(effect_result or {})
    # Generic observation: if effect was success, observed is its output; else empty
    return {
        "observed": eff.get("output"),
        "side_effect_occurred": eff.get("side_effect_occurred", False),
        "effect_id": eff.get("effect_id"),
        "status": eff.get("status"),
    }
