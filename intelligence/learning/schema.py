"""Learning event data model and deterministic id (Phase 8)."""

import hashlib
import json


def derive_learning_event_id(outcome_id, context_id, pattern_detected,
                             adaptations_proposed, created_at_epoch):
    payload = {
        "outcome_id": outcome_id,
        "context_id": context_id,
        "pattern_detected": pattern_detected,
        "adaptations_proposed": sorted([
            (a["adaptation_type"] if isinstance(a, dict) else getattr(a, "adaptation_type", str(a)),
             a["target_id"] if isinstance(a, dict) else getattr(a, "target_id", ""))
            for a in (adaptations_proposed or [])
        ]),
        "created_at_epoch": created_at_epoch,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           default=str)
    return "le_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def derive_adaptation_id(adaptation_type, target_id, context_id, evidence_ids):
    payload = {
        "adaptation_type": adaptation_type.value if hasattr(adaptation_type, "value") else str(adaptation_type),
        "target_id": target_id,
        "context_id": context_id,
        "evidence_ids": sorted(evidence_ids or []),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           default=str)
    return "ad_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
