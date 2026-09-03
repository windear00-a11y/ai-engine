"""Knowledge Lifecycle package (Phase 5)."""

from .events import (
    KnowledgeLifecycleEvent,
    LifecycleEventStore,
    default_evidence_db_path,
    derive_event_id,
)
from .lifecycle import (
    approve_invalidate_knowledge,
    approve_supersede_knowledge,
    propose_invalidate_knowledge,
    propose_supersede_knowledge,
    restrict_context,
)
from .confidence import (
    base_confidence,
    get_knowledge_confidence,
    update_knowledge_confidence,
)
from .conflict import KnowledgeConflict, detect_conflicts
from .staleness import StalenessRecord, detect_staleness, staleness_score

__all__ = [
    "KnowledgeLifecycleEvent", "LifecycleEventStore", "derive_event_id",
    "default_evidence_db_path",
    "propose_supersede_knowledge", "approve_supersede_knowledge",
    "propose_invalidate_knowledge", "approve_invalidate_knowledge",
    "restrict_context",
    "base_confidence", "get_knowledge_confidence",
    "update_knowledge_confidence",
    "KnowledgeConflict", "detect_conflicts",
    "StalenessRecord", "detect_staleness", "staleness_score",
]
