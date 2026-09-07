"""Canonical, deterministic Action model and executor boundary (Phase 29).

Pure domain module: stdlib only, no database, no network, no LLM.

An Action is *what was attempted* — an intended plan step plus its executed
execution status. It is never the observation (what happened) nor the
verification (whether reality satisfied the expectation).

The Effect Executor boundary is deliberately narrow::

    Action Service
        |  owns lifecycle state, decides nothing about authorization
        v
    Effect Executor
        |  must not decide authorization, must not manufacture verification
        v
    External/System Effect

Unsupported effects return explicit unsupported/UNKNOWN results.
"""

import hashlib
import json

REQUESTED = "requested"
APPROVED = "approved"
EXECUTING = "executing"
SUCCEEDED = "succeeded"
FAILED = "failed"
PARTIAL = "partial"
UNKNOWN = "unknown"
DENIED = "denied"

ACTION_STATES = (REQUESTED, APPROVED, EXECUTING, SUCCEEDED, FAILED, PARTIAL,
                 UNKNOWN, DENIED)

EFFECT_STATUS_SUCCESS = "success"
EFFECT_STATUS_FAILURE = "failure"
EFFECT_STATUS_PARTIAL = "partial"
EFFECT_STATUS_UNKNOWN = "unknown"
EFFECT_STATUS_UNSUPPORTED = "unsupported"

EFFECT_STATUSES = (EFFECT_STATUS_SUCCESS, EFFECT_STATUS_FAILURE,
                   EFFECT_STATUS_PARTIAL, EFFECT_STATUS_UNKNOWN,
                   EFFECT_STATUS_UNSUPPORTED)

# Canonical builtin executor for the no-op effect (deterministic, safe).
NOOP_EXECUTOR = {
    EFFECT_STATUS_SUCCESS: {
        "status": EFFECT_STATUS_SUCCESS,
        "observed_state": {"completed": True,
                           "detail": "no-op completed without side effects"},
        "detail": "no-op executed",
    },
}

MAX_OBSERVED_KEYS = 32


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


def derive_action_id(plan_id, plan_step_id, actor, request_id, project_id):
    """Deterministic canonical action identity.

    Same (plan, plan step, actor, request id, project) always yields the same
    action id, so retries of the same logical request are idempotent while a
    distinct ``request_id`` (a new legitimate action) never collides.
    """
    digest = hashlib.sha256(_canonical({
        "plan_id": plan_id,
        "plan_step_id": plan_step_id,
        "actor": actor,
        "request_id": request_id,
        "project_id": project_id,
    }).encode("utf-8")).hexdigest()
    return "ac_" + digest[:32]


def derive_default_request_id(plan_id, plan_step_id, actor, project_id):
    """Deterministic default request identity for idempotent retries."""
    digest = hashlib.sha256(_canonical({
        "plan_id": plan_id,
        "plan_step_id": plan_step_id,
        "actor": actor,
        "project_id": project_id,
    }).encode("utf-8")).hexdigest()
    return "req_" + digest[:32]


def action_status_from_effect(status):
    """Map an effect result status to an Action execution status."""
    low = (status or "").strip().lower()
    if low == EFFECT_STATUS_SUCCESS:
        return SUCCEEDED
    if low == EFFECT_STATUS_FAILURE:
        return FAILED
    if low == EFFECT_STATUS_PARTIAL:
        return PARTIAL
    return UNKNOWN


def execute_effect(effect, inputs, executor=None):
    """Run a single effect through the executor boundary.

    The executor is either a callable ``(effect, inputs) -> result dict`` or a
    mapping of ``effect -> callable`` or ``effect -> static result dict``.
    Results are validated and normalized. Unsupported effects always produce
    an explicit ``unsupported`` result (Action UNKNOWN), never a fabricated
    success.
    """
    result = _resolve_executor_result(effect, inputs, executor)
    return _normalize_effect_result(effect, result)


def _static_or_callable(executor, effect, inputs):
    if callable(executor):
        try:
            return executor(effect, inputs)
        except Exception as exc:
            return {"status": EFFECT_STATUS_UNKNOWN,
                    "detail": "executor raised: %s" % (exc,)}
    if isinstance(executor, dict):
        if effect in executor:
            entry = executor[effect]
            if callable(entry):
                try:
                    return entry(effect, inputs)
                except Exception as exc:
                    return {"status": EFFECT_STATUS_UNKNOWN,
                            "detail": "executor raised: %s" % (exc,)}
            if not isinstance(entry, dict):
                return {"status": EFFECT_STATUS_UNKNOWN,
                        "detail": "static executor result for %s is not a "
                                  "result dict" % effect}
            return dict(entry)
        return {"status": EFFECT_STATUS_UNSUPPORTED,
                "detail": "effect %s has no executor" % effect}
    return {"status": EFFECT_STATUS_UNSUPPORTED,
            "detail": "no executor boundary available"}


def _resolve_executor_result(effect, inputs, executor):
    if executor is None:
        if effect == "noop":
            return dict(NOOP_EXECUTOR[EFFECT_STATUS_SUCCESS])
        return {"status": EFFECT_STATUS_UNSUPPORTED,
                "detail": "effect %s has no canonical executor (unsupported)"
                          % effect}
    return _static_or_callable(executor, effect, inputs)


def _normalize_effect_result(effect, result):
    if not isinstance(result, dict):
        return {
            "effect": effect,
            "status": EFFECT_STATUS_UNKNOWN,
            "observed_state": {},
            "detail": "executor returned a non-dict result",
            "unsupported": False,
        }
    status = (result.get("status") or EFFECT_STATUS_UNKNOWN).lower()
    if status not in EFFECT_STATUSES:
        status = EFFECT_STATUS_UNKNOWN
    observed = result.get("observed_state")
    if observed is None:
        observed = {}
    if not isinstance(observed, dict):
        observed = {"raw": _canonical(observed)}
    observed = {str(k): v for k, v in list(observed.items())[
        :MAX_OBSERVED_KEYS]}
    return {
        "effect": effect,
        "status": status,
        "observed_state": observed,
        "detail": result.get("detail") or status,
        "unsupported": status == EFFECT_STATUS_UNSUPPORTED,
    }