"""Strategy registry: CRUD, retrieval, deprecation, and seeding (Phase 4).

Strategies are additive (new strategies only) and changes to confidence /
deprecation are audited. Deprecation is *proposed*, never auto-applied: it
requires an explicit ``approve_deprecation`` call (the approval gate).
"""

from .schema import Strategy, derive_strategy_id
from .store import StrategyStore, default_evidence_db_path

# Seed template drawn from the deterministic planner's step templates
# (tools/planner/deterministic.py: ALLOWED_INTENTS = {bug_fix, test_verify,
# generic}). Each plan type maps to its canonical deterministic tool_sequence.
_PLANNER_SEEDS = [
    {
        "name": "bug_fix",
        "description": "Resolve a defect in a target file: read, analyze, "
                       "search, preview diff, then verify by test/build.",
        "problem_class": "bug_fix",
        "tool_sequence": ["file.read", "code.analyze", "file.search",
                          "file.diff", "project.check", "project.test"],
    },
    {
        "name": "test_verify",
        "description": "Validate a test target: read the test file, run the "
                       "test suite, then sanity-check the project.",
        "problem_class": "test_verify",
        "tool_sequence": ["file.read", "project.test", "project.check"],
    },
    {
        "name": "generic",
        "description": "Inspect a project and read/diff the target file "
                       "(read-only intelligence workflow).",
        "problem_class": "generic",
        "tool_sequence": ["project.inspect", "file.read", "file.diff"],
    },
]


def register_strategy(strategy_id, name, description, problem_class,
                      tool_sequence, constraints=None, confidence=0.0,
                      context_restrictions=None, strategy_type="explicit",
                      store=None, created_at_epoch=0.0):
    """Register a strategy. Additive only; re-registration of the same id is
    a no-op (idempotent)."""
    store = store or StrategyStore()
    strategy = Strategy(
        strategy_id=strategy_id,
        name=name,
        description=description,
        problem_class=problem_class,
        tool_sequence=tool_sequence,
        constraints=constraints,
        confidence=confidence,
        context_restrictions=context_restrictions,
        strategy_type=strategy_type,
        created_at_epoch=created_at_epoch,
        updated_at_epoch=created_at_epoch,
    )
    store.save(strategy)
    return strategy


def get_strategy(strategy_id, store=None):
    store = store or StrategyStore()
    strategy = store.get(strategy_id)
    if strategy is None:
        raise KeyError(strategy_id)
    return strategy


def strategies_for_problem_class(problem_class, store=None):
    store = store or StrategyStore()
    return store.list_by_problem_class(problem_class)


def update_strategy_confidence(strategy_id, delta, evidence_ids,
                               store=None, created_at_epoch=0.0):
    """Apply ``delta`` to a strategy's confidence (clamped to [0, 1]) and
    audit the change against the given evidence ids."""
    store = store or StrategyStore()
    strategy = store.get(strategy_id)
    if strategy is None:
        raise KeyError(strategy_id)
    new_confidence = round(max(0.0, min(1.0, strategy.confidence + delta)),
                           6)
    store.update_confidence(strategy_id, new_confidence, evidence_ids, delta,
                            created_at_epoch)
    return store.get(strategy_id)


def propose_deprecation(strategy_id, store=None, created_at_epoch=0.0,
                        note=None):
    """Record a deprecation request WITHOUT applying it.

    Returns the strategy unchanged (deprecated still False) and writes an
    audit note so the pending deprecation is visible. Satisfies the invariant
    that deprecation is flagged, not auto-applied.
    """
    store = store or StrategyStore()
    strategy = store.get(strategy_id)
    if strategy is None:
        raise KeyError(strategy_id)
    if not strategy.deprecated:
        store.set_deprecated(strategy_id, False, created_at_epoch,
                             note=note or "deprecation proposed (pending "
                                          "approval) -- not auto-applied")
    return store.get(strategy_id)


def approve_deprecation(strategy_id, store=None, created_at_epoch=0.0,
                        note=None):
    """Explicitly approve and apply deprecation (must be called by an
    authorized approval decision)."""
    store = store or StrategyStore()
    strategy = store.get(strategy_id)
    if strategy is None:
        raise KeyError(strategy_id)
    store.set_deprecated(strategy_id, True, created_at_epoch,
                         note=note or "deprecation approved")
    return store.get(strategy_id)


def initialize_strategies_from_planner(store=None, created_at_epoch=0.0):
    """Seed initial strategies from the deterministic planner's plan types.

    Deterministic and additive: each seed has a fixed id derived from
    (problem_class, name), so repeated seeding is a no-op.
    """
    store = store or StrategyStore()
    seeded = []
    for seed in _PLANNER_SEEDS:
        strategy_id = derive_strategy_id(seed["problem_class"], seed["name"])
        strategy = Strategy(
            strategy_id=strategy_id,
            name=seed["name"],
            description=seed["description"],
            problem_class=seed["problem_class"],
            tool_sequence=seed["tool_sequence"],
            strategy_type="explicit",
            created_at_epoch=created_at_epoch,
            updated_at_epoch=created_at_epoch,
        )
        store.save(strategy)
        seeded.append(strategy)
    return seeded


# Re-export for callers that want the derived-id helper or default path.
def _default_db_path():
    return default_evidence_db_path()


__all__ = [
    "register_strategy", "get_strategy", "strategies_for_problem_class",
    "update_strategy_confidence", "propose_deprecation",
    "approve_deprecation", "initialize_strategies_from_planner",
]
