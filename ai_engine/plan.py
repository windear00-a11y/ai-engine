"""Canonical, deterministic Plan model (Phase 28).

Pure domain module: stdlib only, no database, no network, no LLM.

A Plan is an intended sequence/structure of actions, never execution. It ends
the Phase 28 intelligence loop at the PLAN boundary and explicitly marks the
(Phase 29) PLAN -> ACTION handoff without performing any action. Each step has
a stable id and metadata (preconditions, expected observation, verification
requirement, authority class) sufficient for Phase 29 to consume.
"""

import hashlib
import json

PLAN_DRAFTED = "drafted"
PLAN_UNACTIONABLE = "unactionable"

MAX_PLAN_STEPS = 8


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


def canonical_pattern(value):
    if not isinstance(value, str):
        value = str(value)
    return " ".join(value.strip().lower().split())


def derive_plan_id(decision_id, situation, context_id=None):
    """Deterministic plan identity over its decision reference."""
    digest = hashlib.sha256(_canonical({
        "decision_id": decision_id,
        "situation": canonical_pattern(situation),
        "context_id": context_id,
    }).encode("utf-8")).hexdigest()
    return "pl_" + digest[:32]


def derive_plan_step_id(plan_id, index, action):
    """Deterministic stable per-step id."""
    digest = hashlib.sha256(_canonical({
        "plan_id": plan_id, "index": int(index), "action": action,
    }).encode("utf-8")).hexdigest()
    return "pls_" + digest[:32]


def _authority_class(course):
    """The authority class a step would require (never grants it)."""
    low = canonical_pattern(course)
    if low in ("memory.recall", "memory.get", "context.get", "noop"):
        return "low"
    return "elevated"


def build_plan(decision, situation, constraints=None, max_steps=MAX_PLAN_STEPS):
    """Deterministically draft an intended plan from a decision.

    ``decision`` is a DecisionResult dict (ai_engine.decision). If the decision
    is not ``decided``, the plan is drafted as unactionable with no steps and
    the handoff is marked not-ready.
    """
    decision = dict(decision or {})
    constraints = constraints or decision.get("constraints") or {}
    situation = situation if isinstance(situation, str) else str(situation)
    limit = max(0, int(max_steps or MAX_PLAN_STEPS))

    decision_id = decision.get("decision_id")
    selected_course = decision.get("selected_course")
    decided = decision.get("status") == "decided" and bool(selected_course)

    steps = []
    if decided:
        action = str(selected_course)
        step = {
            "step_id": derive_plan_step_id(decision_id, 0, action),
            "index": 0,
            "action": action,
            "action_contract": "ACTION (Phase 29 only)",
            "inputs": {"situation": situation},
            "rationale": "execute the decided course selected by decision %s"
                         % decision_id,
            "authority_class": _authority_class(action),
            "required_authority": True,
            "preconditions": [
                "approval granted by the Phase 29 authority layer",
                "the decision remains active (not superseded/invalidated)",
            ],
            "expected_observation": (
                "record what actually happened during the action "
                "(Phase 29 OBSERVATION)"),
            "verification_requirement": (
                "verify the expected result against observed evidence "
                "(Phase 29 VERIFICATION)"),
        }
        steps = [step][:limit]

    actionable = bool(steps)
    ready = False  # a plan is never ready by itself: it is not execution.

    return {
        "plan_id": derive_plan_id(decision_id, situation,
                                  context_id=decision.get("context_id")),
        "decision_id": decision_id,
        "status": PLAN_DRAFTED if decided else PLAN_UNACTIONABLE,
        "situation": situation,
        "context_id": decision.get("context_id"),
        "ordered_steps": steps,
        "preconditions": [
            "an approved, decided course is required",
            "all steps pass through the Phase 29 authority layer",
        ],
        "expected_observations": [
            "the record of what actually happened for each step "
            "(Phase 29 OBSERVATION)",
        ],
        "verification_requirements": [
            "each expected result is checked against observed evidence "
            "(Phase 29 VERIFICATION)",
        ],
        "constraints": constraints,
        "provenance": {
            "decision_id": decision_id,
            "reasoning_id": decision.get("reasoning_id"),
            "strategy_application_id": decision.get("strategy_application_id"),
            "applicable_strategy_id": decision.get("applicable_strategy_id"),
        },
        "action_handoff": {
            "stage": "PLAN->ACTION",
            "ready": ready,
            "actionable": actionable,
            "approval_pending": True,
            "authority_required": True,
            "executed": False,
            "phase_29_contracts": ("ACTION", "OBSERVATION", "VERIFICATION",
                                   "OUTCOME"),
            "note": ("this plan is an intended sequence of actions and is "
                     "NOT execution; Phase 29 owns implementation"),
        },
    }