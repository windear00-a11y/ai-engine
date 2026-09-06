"""Generic Effect abstraction — domain-neutral (Phase 17).

Effect.execute(action, context) -> EffectResult

EffectResult is serializable and represents whether a side effect occurred,
its output, evidence, and provenance. It does NOT verify whether the expected
outcome was achieved — that is the Verifier's job.

No coding concepts are required.
No LLM, no network, no direct arbitrary SQLite.
"""

import abc
import hashlib
import json
import time
import uuid

def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)

def _derive_effect_id(action, context_id=None):
    payload = {"action": action, "context_id": context_id or ""}
    return "eff_" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:16]


class EffectResult:
    """Serializable result of an Effect execution."""

    def __init__(self, effect_id, action, status, output=None, side_effect_occurred=False,
                 evidence_ids=None, error=None, provenance=None, created_at_epoch=None):
        self.effect_id = effect_id
        self.action = dict(action or {})
        self.status = status  # "success" | "failure" | "skipped"
        self.output = output
        self.side_effect_occurred = bool(side_effect_occurred)
        self.evidence_ids = tuple(evidence_ids or [])
        self.error = error
        self.provenance = dict(provenance or {})
        self.created_at_epoch = float(created_at_epoch or time.time())

    def as_dict(self):
        return {
            "effect_id": self.effect_id,
            "action": self.action,
            "status": self.status,
            "output": self.output,
            "side_effect_occurred": self.side_effect_occurred,
            "evidence_ids": list(self.evidence_ids),
            "error": self.error,
            "provenance": self.provenance,
            "created_at_epoch": self.created_at_epoch,
        }

    def to_json(self):
        return json.dumps(self.as_dict(), sort_keys=True, default=str)


class Effect(abc.ABC):
    """Abstract Effect — domain-neutral.

    Implementations must be deterministic for identical inputs (where possible),
    must not bypass ApprovalGate, and must not directly access arbitrary SQLite.
    """

    @property
    @abc.abstractmethod
    def effect_id(self) -> str:
        """Stable effect identifier, e.g. "memory.remember", "noop"."""
        raise NotImplementedError

    @abc.abstractmethod
    def execute(self, action, context=None):
        """Execute the action in the given context.

        Args:
            action: dict — e.g. {"tool": "memory.remember", "inputs": {...}}
            context: dict or ContextSnapshot — optional, for provenance

        Returns:
            EffectResult — serializable, never raises for domain errors (encodes error in result).
        """
        raise NotImplementedError


class NoopEffect(Effect):
    """Generic no-op effect — for 'no action required' plans."""

    @property
    def effect_id(self):
        return "noop"

    def execute(self, action, context=None):
        # No side effect, just return success with no evidence
        ctx_id = None
        if isinstance(context, dict):
            ctx_id = context.get("context_id")
        elif hasattr(context, "context_id"):
            ctx_id = getattr(context, "context_id", None)
        eid = _derive_effect_id(action, ctx_id)
        return EffectResult(
            effect_id=eid,
            action=action,
            status="success",
            output={"message": "no action required"},
            side_effect_occurred=False,
            evidence_ids=[],
            provenance={"effect": self.effect_id, "context_id": ctx_id},
        )


class MemoryRememberEffect(Effect):
    """Generic memory remember effect — uses Memory facade, not direct DB.

    Domain-neutral and does not depend on coding-specific tools.
    """

    def __init__(self, memory):
        # Memory instance is per-project, injected
        from ai_engine.memory import Memory
        if not isinstance(memory, Memory):
            raise ValueError("memory must be Memory instance")
        self.memory = memory

    @property
    def effect_id(self):
        return "memory.remember"

    def execute(self, action, context=None):
        inputs = action.get("inputs", {}) if isinstance(action, dict) else {}
        # Expected inputs: {"payload": {...}, "context_hints": {...}}
        payload = inputs.get("payload") or inputs.get("text") or inputs
        # Normalize: if inputs is directly payload, handle
        if isinstance(payload, dict) and "payload" in inputs:
            payload = inputs["payload"]
        elif isinstance(inputs.get("payload"), dict):
            payload = inputs["payload"]
        elif isinstance(action.get("payload"), dict):
            payload = action["payload"]
        # Fallback: if action itself is payload
        if not isinstance(payload, dict):
            payload = {"text": str(payload)} if payload else {}

        ctx_hints = inputs.get("context_hints") if isinstance(inputs, dict) else None
        # Context for provenance
        ctx_id = None
        if isinstance(context, dict):
            ctx_id = context.get("context_id")
        elif hasattr(context, "context_id"):
            ctx_id = getattr(context, "context_id", None)

        eid = _derive_effect_id(action, ctx_id)
        try:
            # Use Memory.remember via generic payload
            # Memory.remember expects payload dict, context_hints
            res = self.memory.remember(payload=payload, context_hints=ctx_hints)
            if not res.get("ok"):
                return EffectResult(
                    effect_id=eid,
                    action=action,
                    status="failure",
                    output=res,
                    side_effect_occurred=False,
                    error=res.get("error"),
                    provenance={"effect": self.effect_id, "context_id": ctx_id},
                )
            return EffectResult(
                effect_id=eid,
                action=action,
                status="success",
                output=res,
                side_effect_occurred=True,
                evidence_ids=[res.get("evidence_id")] if res.get("evidence_id") else [],
                provenance={"effect": self.effect_id, "context_id": ctx_id, "node_id": res.get("node_id")},
            )
        except Exception as e:
            return EffectResult(
                effect_id=eid,
                action=action,
                status="failure",
                output=None,
                side_effect_occurred=False,
                error=str(e),
                provenance={"effect": self.effect_id, "context_id": ctx_id},
            )


class MemoryRecallEffect(Effect):
    """Generic memory recall effect — read-only, never mutating."""

    def __init__(self, memory):
        from ai_engine.memory import Memory
        if not isinstance(memory, Memory):
            raise ValueError("memory must be Memory instance")
        self.memory = memory

    @property
    def effect_id(self):
        return "memory.recall"

    def execute(self, action, context=None):
        inputs = action.get("inputs", {}) if isinstance(action, dict) else {}
        query = inputs.get("query") or action.get("query") or ""
        limit = inputs.get("limit", 20)
        ctx_id = None
        if isinstance(context, dict):
            ctx_id = context.get("context_id")
        elif hasattr(context, "context_id"):
            ctx_id = getattr(context, "context_id", None)
        eid = _derive_effect_id(action, ctx_id)
        try:
            res = self.memory.recall(query=query, limit=limit, context=context)
            if not res.get("ok"):
                return EffectResult(
                    effect_id=eid,
                    action=action,
                    status="failure",
                    output=res,
                    side_effect_occurred=False,
                    error=res.get("error"),
                    provenance={"effect": self.effect_id, "context_id": ctx_id},
                )
            return EffectResult(
                effect_id=eid,
                action=action,
                status="success",
                output=res["result"],
                side_effect_occurred=False,  # recall is read-only
                evidence_ids=[],
                provenance={"effect": self.effect_id, "context_id": ctx_id},
            )
        except Exception as e:
            return EffectResult(
                effect_id=eid,
                action=action,
                status="failure",
                output=None,
                side_effect_occurred=False,
                error=str(e),
                provenance={"effect": self.effect_id, "context_id": ctx_id},
            )


# Registry helpers (generic, no hardcoded coding)
def register_generic_effects(registry, memory):
    """Register generic effects (memory.*, noop) into registry."""
    from ai_engine.registry import AdapterRegistry
    if not isinstance(registry, AdapterRegistry):
        raise ValueError("registry must be AdapterRegistry")
    # Generic effects are domain-neutral
    registry.register_effect("memory.remember", MemoryRememberEffect(memory).execute)
    registry.register_effect("memory.recall", MemoryRecallEffect(memory).execute)
    registry.register_effect("noop", NoopEffect().execute)
    return registry


def execute_with_approval(action, effect, context=None, approval_gate=None, policy=None):
    """Execute an Effect only after required authority/approval.

    Preserves: Policy → authority requirement → ApprovalGate → Effect
    Does NOT automatically authorize; confidence != authority.
    Generic effects like memory.remember require approval; memory.recall does not.
    """
    tool = action.get("tool") if isinstance(action, dict) else None
    # Determine if this generic action requires authority
    requires_auth = False
    if tool in ("memory.remember",):
        requires_auth = True
    # Policy default: if policy says require_approval_by_default, only mutating generic tools need it
    # For Phase 17, only memory.remember is mutating; memory.recall is read-only
    # Do not treat all tools as requiring approval

    if requires_auth:
        # Need explicit approval via ApprovalGate's approver or direct approver
        approved = False
        # Try ApprovalGate's approver if available
        approver = None
        if approval_gate is not None:
            approver = getattr(approval_gate, "approver", None)
            # Also try to use ApprovalGate.check for generic domain if it supports it
            # For generic memory.remember, we treat it as WRITE-like and require approver
            if approver is not None:
                try:
                    # Build a minimal proposal for generic memory remember
                    proposal = {"tool": tool, "action": action, "context": context}
                    approved = bool(approver(proposal))
                except Exception:
                    approved = False
            else:
                approved = False
        else:
            # No gate at all -> fail closed (deny)
            approved = False

        if not approved:
            return EffectResult(
                effect_id=_derive_effect_id(action, None),
                action=action,
                status="failure",
                output=None,
                side_effect_occurred=False,
                error="approval denied (authority required for %r)" % tool,
                provenance={"effect": effect.effect_id, "approval": "denied"},
            )
    # Approved or not requiring auth -> execute
    result = effect.execute(action, context)
    # Append approval provenance if was required
    if requires_auth:
        result.provenance["approval"] = "granted"
    return result
