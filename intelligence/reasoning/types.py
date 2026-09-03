"""Reasoning types (Phase 6).

Reasoning is deterministic inference: identical inputs produce identical
conclusions with identical confidence scores and identical evidence chains.
Reasoning is read-only and advisory.


Plot:
  ReasoningQuery   -- what the system is being asked to reason about.
  EvidenceStep     -- one supporting step in an evidence chain.
  EvidenceChain    -- a traceable, bounded multi-step chain to a conclusion.
  Conclusion       -- a single deterministic inferential claim.
  Contradiction    -- a reported inconsistency (never auto-resolved).
  InsufficientEvidence -- a flagged lack of support.
"""


class ReasoningQuery:
    """Structured query describing what to reason about."""

    __slots__ = ("question", "task_type", "domain", "target",
                 "knowledge_ids", "experience_filter")

    def __init__(self, question="", task_type="", domain="", target=None,
                 knowledge_ids=None, experience_filter=None):
        self.question = question
        self.task_type = task_type
        self.domain = domain
        self.target = dict(target or {})
        self.knowledge_ids = list(knowledge_ids) if knowledge_ids else None
        self.experience_filter = dict(experience_filter or {})

    def to_dict(self):
        return {
            "question": self.question,
            "task_type": self.task_type,
            "domain": self.domain,
            "target": self.target,
            "knowledge_ids": self.knowledge_ids,
            "experience_filter": self.experience_filter,
        }


class EvidenceStep:
    """One supporting step in an evidence chain."""

    __slots__ = ("source_id", "source_type", "claim", "confidence", "detail")

    def __init__(self, source_id, source_type, claim, confidence, detail=""):
        self.source_id = source_id
        self.source_type = source_type  # 'knowledge' | 'experience' | 'context'
        self.claim = claim
        self.confidence = round(float(confidence), 6)
        self.detail = detail

    def to_dict(self):
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "claim": self.claim,
            "confidence": self.confidence,
            "detail": self.detail,
        }


class EvidenceChain:
    """A multi-step, bounded evidence chain to a conclusion."""

    __slots__ = ("steps", "chain_quality", "propagated_confidence", "depth")

    MAX_DEPTH = 5

    def __init__(self, steps, chain_quality, propagated_confidence, depth):
        self.steps = list(steps)
        self.chain_quality = round(float(chain_quality), 6)
        self.propagated_confidence = round(float(propagated_confidence), 6)
        self.depth = int(depth)

    @property
    def min_support_confidence(self):
        if not self.steps:
            return 0.0
        return round(min(s.confidence for s in self.steps), 6)

    def to_dict(self):
        return {
            "steps": [s.to_dict() for s in self.steps],
            "chain_quality": self.chain_quality,
            "min_support_confidence": self.min_support_confidence,
            "propagated_confidence": self.propagated_confidence,
            "depth": self.depth,
        }


class Conclusion:
    """A single deterministic reasoning conclusion."""

    __slots__ = ("claim", "confidence", "evidence_chain", "context_id",
                 "source")

    def __init__(self, claim, confidence, evidence_chain, context_id,
                 source):
        self.claim = claim
        self.confidence = round(float(confidence), 6)
        self.evidence_chain = evidence_chain
        self.context_id = context_id
        self.source = source  # 'knowledge' | 'experience' | 'generalization'

    def to_dict(self):
        return {
            "claim": self.claim,
            "confidence": self.confidence,
            "evidence_chain": self.evidence_chain.to_dict(),
            "context_id": self.context_id,
            "source": self.source,
        }


class Contradiction:
    """A reported inconsistency (knowledge / experience / context)."""

    __slots__ = ("contradiction_type", "node_a", "node_b", "severity",
                 "description")

    def __init__(self, contradiction_type, node_a, node_b, severity,
                 description):
        self.contradiction_type = contradiction_type
        self.node_a = node_a
        self.node_b = node_b
        self.severity = round(float(severity), 6)
        self.description = description

    def to_dict(self):
        return {
            "type": self.contradiction_type,
            "node_a": self.node_a,
            "node_b": self.node_b,
            "severity": self.severity,
            "description": self.description,
        }


class InsufficientEvidence:
    """A flagged lack of support for a line of reasoning."""

    __slots__ = ("subject", "missing", "reason")

    def __init__(self, subject, missing, reason):
        self.subject = subject
        self.missing = list(missing)
        self.reason = reason

    def to_dict(self):
        return {
            "subject": self.subject,
            "missing": self.missing,
            "reason": self.reason,
        }
