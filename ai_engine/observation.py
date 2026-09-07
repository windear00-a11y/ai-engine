"""Canonical, deterministic Observation model (Phase 29).

Pure domain module: stdlib only, no database, no network, no LLM.

An Observation is *what happened* during/after an action: the observed
state/data collected at the Effect Executor boundary. It must remain distinct
from intent, the action request, the caller's claim, and verification.

States: OBSERVED, PARTIAL, FAILED, UNKNOWN. Success is never inferred merely
because no error was reported — an effect that reports nothing observed
produces an UNKNOWN observation.
"""

import hashlib
import json

from ai_engine.action import (
    EFFECT_STATUS_FAILURE,
    EFFECT_STATUS_PARTIAL,
    EFFECT_STATUS_SUCCESS,
    EFFECT_STATUS_UNKNOWN,
)

OBSERVED = "observed"
PARTIAL = "partial"
FAILED = "failed"
UNKNOWN = "unknown"

OBSERVATION_STATES = (OBSERVED, PARTIAL, FAILED, UNKNOWN)


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


def derive_observation_id(action_id, source, observed_state):
    """Deterministic observation identity over what was actually seen.

    The same action + source + observed state always yields the same id, so a
    repeated observation of identical reality is idempotent; distinct reality
    or a distinct source yields a distinct observation.
    """
    digest = hashlib.sha256(_canonical({
        "action_id": action_id,
        "source": source,
        "observed_state": observed_state or {},
    }).encode("utf-8")).hexdigest()
    return "ob_" + digest[:32]


def observation_status_from_effect(status):
    low = (status or "").strip().lower()
    if low == EFFECT_STATUS_SUCCESS:
        return OBSERVED
    if low == EFFECT_STATUS_FAILURE:
        return FAILED
    if low == EFFECT_STATUS_PARTIAL:
        return PARTIAL
    return UNKNOWN