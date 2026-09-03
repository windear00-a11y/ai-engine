"""Intelligence layer (Model-independent, explicit, deterministic.

This package is the container for the model-independent intelligence system
that accumulates verified knowledge and experience, reasons, decides, plans,
acts, verifies outcomes, and adapts -- WITHOUT neural networks or model
retraining as the core mechanism.

Phase 0 (Foundation) establishes this package root only. No components exist
yet; the package is intentionally empty and importable. Subsequent phases add
modules under the reserved namespaces:

    intelligence/context/    -- operational context capture/query/diff (Ph 1)
    intelligence/evidence/   -- verified observations and outcomes   (Ph 2)
    intelligence/knowledge/  -- knowledge lifecycle (supersede/invalidate)
    intelligence/experience/ -- experience synthesis from evidence + context
    intelligence/reasoning/  -- explicit reasoning (R0-R5)
    intelligence/decisions/  -- decision layer (D0-D8)
    intelligence/policies/   -- JSON policy configuration
    intelligence/plans/      -- plan synthesis from strategy + context
    intelligence/actions/    -- action execution boundary
    intelligence/verification/ -- outcome verification
    intelligence/learning/   -- evidence-based learning/adaptation

Architectural invariants enforced across all phases (see Phase 0 tests):
  * model-independent: no AI/ML framework dependency, no model retraining.
  * permanent-record: all writes are append-only evidence/log entries.
  * frozen contract: ``api.contract.CONTRACT_VERSION`` stays "1".
  * immutable production store: ``database/knowledge.db`` is never modified.
"""

__version__ = "0"  # Phase 0: foundation/package root only.
