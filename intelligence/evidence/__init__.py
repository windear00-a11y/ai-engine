"""Evidence recording and query (Phase 2).

Public API:
  record_evidence(...)      deterministic, idempotent evidence recording
  get_evidence(id)          fetch a single record
  evidence_for_claim(...)   records for a claim (optionally in a context)
"""

from intelligence.evidence.schema import EvidenceRecord, derive_evidence_id
from intelligence.evidence.store import EvidenceStore, default_evidence_db_path
from intelligence.evidence.types import EvidenceType


def record_evidence(source_observation_id, claim, context_id,
                    evidence_type, supporting_data=None,
                    store=None, created_at_epoch=None):
    """Record one verified observation deterministically.

    Same inputs always produce the same evidence_id (idempotent). Returns the
    stored :class:`EvidenceRecord`.
    """
    import time
    if isinstance(evidence_type, str):
        evidence_type = EvidenceType(evidence_type)
    if not isinstance(evidence_type, EvidenceType):
        raise ValueError("evidence_type must be an EvidenceType")
    if created_at_epoch is None:
        created_at_epoch = time.time()
    evidence_id = derive_evidence_id(
        source_observation_id, claim, context_id, evidence_type,
        supporting_data)
    record = EvidenceRecord(
        evidence_id=evidence_id,
        source_observation_id=source_observation_id,
        claim=claim,
        context_id=context_id,
        evidence_type=evidence_type,
        supporting_data=dict(supporting_data or {}),
        created_at_epoch=created_at_epoch,
    )
    ctx_store = store or EvidenceStore()
    try:
        ctx_store.save(record)
    finally:
        if store is None:
            ctx_store.close()
    return record


def get_evidence(evidence_id, store=None):
    ctx_store = store or EvidenceStore()
    try:
        return ctx_store.get(evidence_id)
    finally:
        if store is None:
            ctx_store.close()


def evidence_for_claim(claim, context_id=None, store=None):
    ctx_store = store or EvidenceStore()
    try:
        return ctx_store.for_claim(claim, context_id=context_id)
    finally:
        if store is None:
            ctx_store.close()


__all__ = [
    "record_evidence",
    "get_evidence",
    "evidence_for_claim",
    "EvidenceRecord",
    "EvidenceType",
    "EvidenceStore",
    "default_evidence_db_path",
]
