"""Canonical, deterministic Authority / Approval model (Phase 29).

Pure domain module: stdlib only, no database, no network, no LLM.

An authority decision answers the question "MAY this action be done?". It
never answers "CAN it be done" (capability is out of scope) and it never
performs the action. The decision is a pure, deterministic function of:

    plan identity, plan-step identity, requested effect/action, actor/source,
    project, applicable policy, risk/trust level, approval state, and any
    required evidence/conditions.

States (authority vocabulary):
    APPROVED           policy-based or explicitly approved — may proceed
    DENIED             explicitly denied (deny list / policy refusal)
    REQUIRES_APPROVAL  policy requires an approval that is not present
    INVALID_PLAN       unknown plan
    INVALID_STEP       requested plan step does not exist in the plan
    UNKNOWN            cannot be decided (policy/trust/risk unknown)

`UNKNOWN` is never approval.
"""

import hashlib
import json

APPROVED = "approved"
DENIED = "denied"
REQUIRES_APPROVAL = "requires_approval"
INVALID_PLAN = "invalid_plan"
INVALID_STEP = "invalid_step"
UNKNOWN = "unknown"

AUTHORITY_STATES = (APPROVED, DENIED, REQUIRES_APPROVAL, INVALID_PLAN,
                    INVALID_STEP, UNKNOWN)

# Effects treated as read-only / low-risk by the canonical default policy.
DEFAULT_AUTO_APPROVED = ("noop", "memory.recall", "memory.get", "context.get")

DEFAULT_POLICY = {
    "trust_level": "local",
    "auto_approved_effects": DEFAULT_AUTO_APPROVED,
    "deny_effects": (),
    "required_evidence": (),
    "max_risk": "low",
}


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


def derive_authority_id(plan_id, plan_step_ids, actor, project_id,
                        policy_ref, request_ref=None):
    """Deterministic authority identity over the request that provoked it."""
    digest = hashlib.sha256(_canonical({
        "plan_id": plan_id,
        "plan_step_ids": sorted({str(x) for x in (plan_step_ids or ())}),
        "actor": actor,
        "project_id": project_id,
        "policy_ref": policy_ref,
        "request_ref": request_ref,
    }).encode("utf-8")).hexdigest()
    return "au_" + digest[:32]


def derive_authorization_id(plan_id, plan_step_ids, actor, mechanism):
    """Deterministic explicit-approval grant identity.

    The same (plan, steps, actor, mechanism) always yields the same id, so a
    repeated grant is idempotent.
    """
    digest = hashlib.sha256(_canonical({
        "plan_id": plan_id,
        "plan_step_ids": sorted({str(x) for x in (plan_step_ids or ())}),
        "actor": actor,
        "mechanism": mechanism,
    }).encode("utf-8")).hexdigest()
    return "az_" + digest[:32]


def _canonical_pattern(value):
    if not isinstance(value, str):
        value = str(value)
    return " ".join(value.strip().lower().split())


def _risk_class(authority_class):
    """Map a plan-step authority class to a risk level (never grants)."""
    low = _canonical_pattern(authority_class)
    if low in ("low", "low-risk", "readonly", "read-only"):
        return "low"
    if low in ("elevated", "high", "write", "mutating"):
        return "elevated"
    return "unknown"


def evaluate_authority(plan_id, plan_step_ids, actor, project_id, policy=None,
                       steps=None, approvals=None, required_claims=None,
                       policy_ref=None, request_ref=None):
    """Deterministically decide whether the requested steps may be executed.

    ``steps`` are the plan's actual step dicts (each carrying ``step_id``,
    ``action`` and ``authority_class``). ``approvals`` are explicit approval
    grants (each: ``plan_id``, ``step_id``, ``actor`` or None for project-wide,
    ``state``). ``required_claims`` are condition claims that must be met.

    Returns an AuthorityDecision dict with a stable ``authority_id``. A caller
    that receives anything other than APPROVED must not execute the action.
    """
    plan_id = str(plan_id)
    plan_step_ids = sorted({str(x) for x in (plan_step_ids or ())})
    steps = {str(s.get("step_id")): dict(s) for s in (steps or [])}
    policy = dict(policy or DEFAULT_POLICY)
    policy_ref = policy_ref or "canonical"
    per_step = {}

    if not plan_id:
        return _decision(INVALID_PLAN, plan_id, plan_step_ids, actor,
                         project_id, policy_ref, request_ref, per_step,
                         "a plan id is required for authority evaluation")

    actual_ids = {str(sid) for sid in steps}
    if not plan_step_ids or not (set(plan_step_ids) <= actual_ids):
        missing = sorted(set(plan_step_ids) - actual_ids)
        return _decision(INVALID_STEP, plan_id, plan_step_ids, actor,
                         project_id, policy_ref, request_ref, per_step,
                         "requested plan steps do not exist in plan %s "
                         "(unmatched: %s)" % (plan_id, missing))

    approvals = [_normalize_approval(a) for a in (approvals or [])]
    required_claims = tuple(str(c) for c in (required_claims or ()))
    trust_level = _canonical_pattern(policy.get("trust_level"))
    deny_effects = {_canonical_pattern(e) for e in
                    (policy.get("deny_effects") or ())}
    auto_approved = {_canonical_pattern(e) for e in
                     (policy.get("auto_approved_effects") or ())}
    max_risk = _canonical_pattern(policy.get("max_risk"))

    ordered_step_ids = sorted(plan_step_ids)
    for step_id in ordered_step_ids:
        step = steps[step_id]
        action = _canonical_pattern(step.get("action"))
        risk = _risk_class(step.get("authority_class"))
        evidence_ids = {str(e) for e in (step.get("evidence_ids") or ())}
        step_claims = tuple(str(c) for c in (
            step.get("required_evidence") or ()))
        step_claims = step_claims if step_claims else required_claims

        if risk == "unknown":
            per_step[step_id] = _step(UNKNOWN, risk,
                                      "authority class for step %s is "
                                      "unknown" % step_id)
            continue
        if action in deny_effects:
            per_step[step_id] = _step(DENIED, risk,
                                      "effect %s is on the policy deny "
                                      "list" % action)
            continue
        if trust_level != "local":
            per_step[step_id] = _step(UNKNOWN, risk,
                                      "trust level %s is not supported"
                                      % trust_level)
            continue

        granted = _matching_approval(plan_id, step_id, actor, approvals)
        if granted is not None:
            per_step[step_id] = _step(APPROVED, risk,
                                      "explicit approval grant %s"
                                      % granted)
            continue

        if (risk == "low" and (max_risk == "low" or max_risk == "high")
                and action in auto_approved):
            if all(cls in evidence_ids for cls in step_claims):
                per_step[step_id] = _step(APPROVED, risk,
                                          "auto-approved low-risk read-only "
                                          "effect %s" % action)
            else:
                per_step[step_id] = _step(REQUIRES_APPROVAL, risk,
                                          "required evidence/conditions for "
                                          "%s are not satisfied" % action)
            continue

        if step_claims and not all(cls in evidence_ids for cls in step_claims):
            per_step[step_id] = _step(REQUIRES_APPROVAL, risk,
                                      "approval requires evidence/conditions "
                                      "for effect %s that are not present"
                                      % action)
            continue

        per_step[step_id] = _step(REQUIRES_APPROVAL, risk,
                                  "effect %s requires explicit approval"
                                  % action)

    return _aggregate(plan_id, plan_step_ids, actor, project_id, policy_ref,
                      request_ref, per_step)


def _decision(decision, plan_id, plan_step_ids, actor, project_id, policy_ref,
              request_ref, per_step, rationale):
    return {
        "authority_id": derive_authority_id(plan_id, plan_step_ids, actor,
                                            project_id, policy_ref,
                                            request_ref),
        "plan_id": plan_id,
        "plan_step_ids": sorted(plan_step_ids),
        "actor": actor,
        "project_id": project_id,
        "policy_ref": policy_ref,
        "decision": decision,
        "per_step": {k: dict(v) for k, v in per_step.items()},
        "rationale": rationale,
    }


def _aggregate(plan_id, plan_step_ids, actor, project_id, policy_ref,
               request_ref, per_step):
    if any(v["decision"] == DENIED for v in per_step.values()):
        decision = DENIED
        rationale = "at least one requested step is explicitly denied"
    elif any(v["decision"] == UNKNOWN for v in per_step.values()):
        decision = UNKNOWN
        rationale = "authority cannot be determined for every requested step"
    elif all(v["decision"] == APPROVED for v in per_step.values()):
        decision = APPROVED
        rationale = "every requested step is approved"
    elif any(v["decision"] == REQUIRES_APPROVAL
             for v in per_step.values()):
        decision = REQUIRES_APPROVAL
        rationale = "at least one requested step requires explicit approval"
    else:
        decision = UNKNOWN
        rationale = "authority could not be determined"
    return _decision(decision, plan_id, plan_step_ids, actor, project_id,
                     policy_ref, request_ref, per_step, rationale)


def _step(decision, risk, rationale):
    return {"decision": decision, "risk": risk, "rationale": rationale}


def _normalize_approval(approval):
    approval = dict(approval or {})
    actor = approval.get("actor")
    return {
        "plan_id": str(approval.get("plan_id") or ""),
        "step_id": str(approval.get("step_id") or ""),
        "actor": None if actor is None else str(actor),
        "state": _canonical_pattern(approval.get("state")),
    }


def _matching_approval(plan_id, step_id, actor, approvals):
    for i, approval in enumerate(approvals):
        if (approval["plan_id"] == plan_id
                and approval["step_id"] == step_id
                and approval["state"] == "approved"
                and (approval["actor"] is None
                     or approval["actor"] == str(actor))):
            return "approval[%d]" % i
    return None