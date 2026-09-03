"""Strategy Registry package (Phase 4)."""

from .registry import (
    approve_deprecation,
    get_strategy,
    initialize_strategies_from_planner,
    propose_deprecation,
    register_strategy,
    strategies_for_problem_class,
    update_strategy_confidence,
)
from .schema import Strategy, derive_strategy_id
from .store import StrategyStore, default_evidence_db_path
from .types import StrategyCandidate, StrategyType, candidate_confidence
from .recognizer import recognize_strategies_from_experience

__all__ = [
    "Strategy", "derive_strategy_id", "StrategyStore",
    "default_evidence_db_path", "StrategyCandidate", "StrategyType",
    "candidate_confidence", "register_strategy", "get_strategy",
    "strategies_for_problem_class", "update_strategy_confidence",
    "propose_deprecation", "approve_deprecation",
    "initialize_strategies_from_planner",
    "recognize_strategies_from_experience",
]
