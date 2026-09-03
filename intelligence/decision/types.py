"""Decision type definitions (Phase 7).

The Decision Engine is the bridge between "what is true" (reasoning) and
"what should we do" (planning). It is deterministic, policy-respecting, and
authority-aware: it scores candidate strategies (D1-D4), filters by context
and policy (D2, D5), assesses risk (D4), and respects authority boundaries
(D6) by proposing (not executing) mutating decisions that require approval.

Decision types are immutable records of what was decided and why.
"""


class RiskLevel:
    """Risk levels for a decision (D4).

    ``LOW``   -- read-only action; the system decides autonomously.
    ``MEDIUM``-- reversible write; system proposes, ApprovalGate decides.
    ``HIGH``  -- irreversible action; system recommends, human decides.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CandidateStrategy:
    """A single scored strategy candidate considered by the decision engine.

    Deterministic: identical inputs yield identical scores and the same
    selected/filtered status.
    """

    __slots__ = (
        "strategy_id",
        "name",
        "problem_class",
        "tool_sequence",
        "strategy_confidence",
        "context_match",
        "evidence_quality",
        "recency_weight",
        "risk_level",
        "score",
        "filtered",
        "filter_reason",
        "requires_approval",
    )

    def __init__(self, strategy_id, name, problem_class, tool_sequence,
                 strategy_confidence, context_match=1.0,
                 evidence_quality=1.0, recency_weight=1.0,
                 risk_level=RiskLevel.LOW, score=None, filtered=False,
                 filter_reason=None, requires_approval=False):
        self.strategy_id = strategy_id
        self.name = name
        self.problem_class = problem_class
        self.tool_sequence = tuple(tool_sequence or ())
        self.strategy_confidence = float(strategy_confidence)
        self.context_match = round(float(context_match), 6)
        self.evidence_quality = round(float(evidence_quality), 6)
        self.recency_weight = round(float(recency_weight), 6)
        self.risk_level = risk_level
        self.score = None if score is None else round(float(score), 6)
        self.filtered = bool(filtered)
        self.filter_reason = filter_reason
        self.requires_approval = bool(requires_approval)

    def to_dict(self):
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "problem_class": self.problem_class,
            "tool_sequence": list(self.tool_sequence),
            "strategy_confidence": self.strategy_confidence,
            "context_match": self.context_match,
            "evidence_quality": self.evidence_quality,
            "recency_weight": self.recency_weight,
            "risk_level": self.risk_level,
            "score": self.score,
            "filtered": self.filtered,
            "filter_reason": self.filter_reason,
            "requires_approval": self.requires_approval,
        }


class Decision:
    """A decision record produced by the decision engine.

    Immutable snapshot of the selected strategy, the rationale (evidence
    chain), alternatives considered and rejected, risk level, and whether
    human approval is required. The Decision proposes strategies only; it
    never executes and never bypasses an approval gate.
    """

    __slots__ = (
        "decision_id",
        "task_id",
        "task_type",
        "selected_strategy_id",
        "reasoning_id",
        "confidence",
        "rationale",
        "alternatives_rejected",
        "risk_level",
        "approval_required",
        "status",
        "context_id",
        "created_at_epoch",
        "kind",
    )

    def __init__(self, decision_id, task_id, task_type, selected_strategy_id,
                 reasoning_id, confidence, rationale, alternatives_rejected,
                 risk_level, approval_required, status, context_id,
                 created_at_epoch=0.0, kind=None):
        self.decision_id = decision_id
        self.task_id = task_id
        self.task_type = task_type
        self.selected_strategy_id = selected_strategy_id
        self.reasoning_id = reasoning_id
        self.confidence = round(float(confidence), 6)
        self.rationale = dict(rationale or {})
        self.alternatives_rejected = list(alternatives_rejected or [])
        self.risk_level = risk_level
        self.approval_required = bool(approval_required)
        self.status = status
        self.context_id = context_id
        self.created_at_epoch = float(created_at_epoch)
        self.kind = kind

    @property
    def is_human_guidance_required(self):
        return self.status == "needs_guidance"

    def to_dict(self):
        return {
            "decision_id": self.decision_id,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "selected_strategy_id": self.selected_strategy_id,
            "reasoning_id": self.reasoning_id,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "alternatives_rejected": self.alternatives_rejected,
            "risk_level": self.risk_level,
            "approval_required": self.approval_required,
            "status": self.status,
            "context_id": self.context_id,
            "created_at_epoch": self.created_at_epoch,
            "kind": self.kind,
        }
