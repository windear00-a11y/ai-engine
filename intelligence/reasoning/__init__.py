"""Reasoning Engine package (Phase 6)."""

from .types import (
    Conclusion,
    Contradiction,
    EvidenceChain,
    EvidenceStep,
    InsufficientEvidence,
    ReasoningQuery,
)
from .schema import ReasoningOutput, derive_reasoning_id
from .evidence_chain import build_chain, chain_quality
from .contradiction import (
    detect_all_contradictions,
    detect_context_contradictions,
    detect_experience_contradictions,
    detect_knowledge_contradictions,
)
from .inference import (
    conservative_generalization,
    experience_aggregation,
    knowledge_relevance,
    knowledge_support_steps,
)
from .store import ReasoningStore, default_evidence_db_path
from .engine import reason

__all__ = [
    "Conclusion", "Contradiction", "EvidenceChain", "EvidenceStep",
    "InsufficientEvidence", "ReasoningQuery", "ReasoningOutput",
    "derive_reasoning_id", "build_chain", "chain_quality",
    "detect_all_contradictions", "detect_context_contradictions",
    "detect_experience_contradictions", "detect_knowledge_contradictions",
    "conservative_generalization", "experience_aggregation",
    "knowledge_relevance", "knowledge_support_steps", "ReasoningStore",
    "default_evidence_db_path", "reason",
]
