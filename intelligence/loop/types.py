"""Loop type definitions (Phase 9)."""


class LoopResult:
    """Result of a full PERCEIVE→LEARN cycle."""

    __slots__ = (
        "task_id", "context_id", "reasoning_id", "decision_id",
        "plan_id", "outcome_id", "experience_id", "learning_event_id",
        "adaptations", "ok", "errors", "fallback_used",
        "approval_required", "status",
    )

    def __init__(self, task_id, context_id=None, reasoning_id=None,
                 decision_id=None, plan_id=None, outcome_id=None,
                 experience_id=None, learning_event_id=None,
                 adaptations=None, ok=False, errors=None,
                 fallback_used=False, approval_required=False,
                 status="completed"):
        self.task_id = task_id
        self.context_id = context_id
        self.reasoning_id = reasoning_id
        self.decision_id = decision_id
        self.plan_id = plan_id
        self.outcome_id = outcome_id
        self.experience_id = experience_id
        self.learning_event_id = learning_event_id
        self.adaptations = list(adaptations or [])
        self.ok = bool(ok)
        self.errors = list(errors or [])
        self.fallback_used = bool(fallback_used)
        self.approval_required = bool(approval_required)
        self.status = status

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "context_id": self.context_id,
            "reasoning_id": self.reasoning_id,
            "decision_id": self.decision_id,
            "plan_id": self.plan_id,
            "outcome_id": self.outcome_id,
            "experience_id": self.experience_id,
            "learning_event_id": self.learning_event_id,
            "adaptations": self.adaptations,
            "ok": self.ok,
            "errors": self.errors,
            "fallback_used": self.fallback_used,
            "approval_required": self.approval_required,
            "status": self.status,
        }
