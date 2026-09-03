"""Main learning engine (Phase 8).

Evaluates a verified outcome and proposes adaptations. Deterministic:
same outcome, context, and accumulated evidence yield same adaptations.
"""

from .positive import propose_positive_adaptation
from .negative import propose_negative_adaptations, propose_restore_adaptation
from .schema import derive_learning_event_id
from .store import LearningStore
from .types import LearningEvent
from intelligence.outcome.types import OutcomeClassification


def _get_outcome(outcome_id, outcome_store):
    if outcome_store is None:
        from intelligence.outcome.store import OutcomeStore
        outcome_store = OutcomeStore()
        close = True
    else:
        close = False
    try:
        return outcome_store.get(outcome_id)
    finally:
        if close:
            outcome_store.close()


def _get_strategy(strategy_id, strategy_store):
    if strategy_id is None:
        return None
    if strategy_store is None:
        from intelligence.strategy.store import StrategyStore
        strategy_store = StrategyStore()
        close = True
    else:
        close = False
    try:
        return strategy_store.get(strategy_id)
    finally:
        if close:
            strategy_store.close()


def _infer_strategy_from_experience(outcome_id, experience_store):
    if experience_store is None:
        from intelligence.experience.store import ExperienceStore
        experience_store = ExperienceStore()
        close = True
    else:
        close = False
    try:
        for exp in experience_store.all():
            if exp.outcome_id == outcome_id and exp.strategy_id:
                return exp.strategy_id, exp.task_type, exp.context_id
        return None, None, None
    finally:
        if close:
            experience_store.close()


def _count_successes_failures(strategy_id, experience_store, outcome_store):
    # Count verified successes/failures for strategy via experience + outcome.
    if strategy_id is None:
        return 0, 0
    # Load experiences for strategy
    if experience_store is None:
        from intelligence.experience.store import ExperienceStore
        experience_store = ExperienceStore()
        close_exp = True
    else:
        close_exp = False
    if outcome_store is None:
        from intelligence.outcome.store import OutcomeStore
        outcome_store = OutcomeStore()
        close_out = True
    else:
        close_out = False
    try:
        exps = experience_store.for_strategy(strategy_id) if hasattr(experience_store, "for_strategy") else []
        # Map outcome_id -> classification
        outcome_map = {}
        for o in outcome_store.all():
            outcome_map[o.outcome_id] = o.classification
        successes = 0
        failures = 0
        for exp in exps:
            cls = outcome_map.get(exp.outcome_id)
            if cls == OutcomeClassification.SUCCESS:
                successes += 1
            elif cls == OutcomeClassification.FAILURE:
                failures += 1
        return successes, failures
    finally:
        if close_exp:
            experience_store.close()
        if close_out:
            outcome_store.close()


def learn_from_outcome(outcome_id, context_id=None, strategy_id=None,
                       outcome_store=None, strategy_store=None,
                       experience_store=None, learning_store=None,
                       created_at_epoch=0.0):
    """Evaluate an outcome and produce a LearningEvent.

    Applies automatic adaptations (confidence updates) and proposes
    approval-required adaptations (deprecation). Returns LearningEvent.
    """
    outcome = _get_outcome(outcome_id, outcome_store)
    if outcome is None:
        raise KeyError(f"outcome not found: {outcome_id}")

    # Resolve context_id and strategy_id if not supplied.
    ctx = context_id or getattr(outcome, "context_id", None) or "ctx_unknown"
    sid = strategy_id
    inferred_task_type = None
    if sid is None:
        sid, inferred_task_type, exp_ctx = _infer_strategy_from_experience(
            outcome_id, experience_store)
        if ctx == "ctx_unknown" and exp_ctx:
            ctx = exp_ctx

    # Determine classification
    classification = getattr(outcome, "classification", None)
    # Support string classification
    if isinstance(classification, str):
        from intelligence.outcome.types import classify_outcome
        classification = classify_outcome(classification)

    successes, failures = _count_successes_failures(
        sid, experience_store, outcome_store)

    # Include current outcome in counts if not already counted via experience
    # (experience may not yet include the current outcome beyond the store;
    # for determinism in tests, counts are as stored; the current outcome's
    # classification is already reflected if experience exists, else we add it)
    # For simplicity, if sid and outcome not yet in experience, adjust:
    # Check if outcome_id already represented in experience count above.
    # If not, bump the corresponding counter.
    # We detect by seeing if any experience for sid has this outcome_id.
    has_exp_for_outcome = False
    if sid is not None and experience_store is not None:
        try:
            for exp in experience_store.for_strategy(sid):
                if exp.outcome_id == outcome_id:
                    has_exp_for_outcome = True
                    break
        except Exception:
            pass
    elif sid is not None:
        # Use fresh store check
        from intelligence.experience.store import ExperienceStore
        tmp = ExperienceStore()
        try:
            for exp in tmp.for_strategy(sid):
                if exp.outcome_id == outcome_id:
                    has_exp_for_outcome = True
                    break
        finally:
            tmp.close()
    if not has_exp_for_outcome and sid is not None:
        if classification == OutcomeClassification.SUCCESS:
            successes += 1
        elif classification == OutcomeClassification.FAILURE:
            failures += 1

    evidence_ids = list(getattr(outcome, "verification_evidence_ids", []) or
                        getattr(outcome, "evidence_ids", []) or [])

    adaptations_proposed = []
    adaptations_applied = []
    adaptations_rejected = []
    pattern = None

    # Resolve strategy object for deprecated check
    strat = _get_strategy(sid, strategy_store) if sid else None

    if classification == OutcomeClassification.SUCCESS:
        if strat is not None and getattr(strat, "deprecated", False):
            # Reversibility: restore deprecated strategy on new success
            adapt, valid = propose_restore_adaptation(sid, ctx, evidence_ids)
            adaptations_proposed.append(adapt)
            if valid.approved:
                adaptations_applied.append(adapt)
                # Apply: clear deprecated flag (audited)
                if strategy_store is None:
                    from intelligence.strategy.store import StrategyStore
                    s_store = StrategyStore()
                    close = True
                else:
                    s_store = strategy_store
                    close = False
                try:
                    s_store.set_deprecated(sid, False, created_at_epoch,
                                           note="restored via learning reversibility")
                finally:
                    if close:
                        s_store.close()
            else:
                adaptations_rejected.append(adapt)
            pattern = f"restore_{sid}_after_success"
        elif sid is not None:
            adapt, valid = propose_positive_adaptation(sid, ctx, evidence_ids, successes)
            adaptations_proposed.append(adapt)
            pattern = f"success_{sid}_{successes}"
            if valid.approved:
                adaptations_applied.append(adapt)
                # Apply confidence increase
                if strategy_store is None:
                    from intelligence.strategy.store import StrategyStore
                    s_store = StrategyStore()
                    close = True
                else:
                    s_store = strategy_store
                    close = False
                try:
                    cur = s_store.get(sid)
                    if cur is not None:
                        new_conf = round(min(1.0, cur.confidence + adapt.delta), 6)
                        s_store.update_confidence(sid, new_conf, evidence_ids,
                                                  adapt.delta, created_at_epoch,
                                                  note="positive learning")
                finally:
                    if close:
                        s_store.close()
            else:
                adaptations_rejected.append(adapt)
        else:
            pattern = "no_strategy_for_success"
    elif classification == OutcomeClassification.FAILURE:
        if sid is not None:
            proposals = propose_negative_adaptations(sid, ctx, evidence_ids, failures)
            pattern = f"failure_{sid}_{failures}"
            for adapt, valid in proposals:
                adaptations_proposed.append(adapt)
                if valid.approved:
                    # Only auto-apply non-approval adaptations
                    if adapt.requires_approval:
                        # Deprecation stays proposed, not applied
                        adaptations_rejected.append(adapt)
                        # Still record as proposed; but per spec, deprecation
                        # is 'proposed' not applied until approval.
                        # We keep it in proposed, and mark rejected as
                        # 'requires_approval' (still proposed but not applied).
                        # For clarity, move to applied list only if not approval.
                        # So dep goes to rejected as pending approval.
                        continue
                    # Check if strategy is already context-restricted? still apply
                    adaptations_applied.append(adapt)
                    if adapt.adaptation_type.value == "decrease_strategy_confidence":
                        if strategy_store is None:
                            from intelligence.strategy.store import StrategyStore
                            s_store = StrategyStore()
                            close = True
                        else:
                            s_store = strategy_store
                            close = False
                        try:
                            cur = s_store.get(sid)
                            if cur is not None:
                                new_conf = round(max(0.0, cur.confidence + adapt.delta), 6)
                                s_store.update_confidence(sid, new_conf, evidence_ids,
                                                          adapt.delta, created_at_epoch,
                                                          note="negative learning (context-restricted)")
                                # Also apply context restriction via strategy's
                                # context_restrictions (if not already)
                                if adapt.context_restriction:
                                    cur2 = s_store.get(sid)
                                    if cur2 is not None:
                                        # Merge restriction
                                        merged = dict(cur2.context_restrictions or {})
                                        merged.update(adapt.context_restriction)
                                        # Directly update the store's JSON column
                                        # via an internal update (we lack a public
                                        # setter, so we update via save-like path:
                                        # use strategy object and re-save with
                                        # updated restriction, or just store
                                        # the restriction as audit note.
                                        # For testability, we store via a
                                        # dedicated path: update strategy row's
                                        # context_restrictions_json.
                                        s_store.conn.execute(
                                            "UPDATE strategies SET context_restrictions_json=?, updated_at_epoch=? WHERE strategy_id=?",
                                            ( __import__("json").dumps(merged, sort_keys=True),
                                              created_at_epoch, sid))
                                        s_store.conn.commit()
                        finally:
                            if close:
                                s_store.close()
                else:
                    adaptations_rejected.append(adapt)
        else:
            pattern = "failure_no_strategy"
    else:
        pattern = f"no_learning_for_{classification.value if hasattr(classification, 'value') else str(classification)}"

    # Build evidence chain for audit: outcome + evidence ids
    evidence_chain = {
        "outcome_id": outcome_id,
        "classification": classification.value if hasattr(classification, "value") else str(classification),
        "evidence_ids": evidence_ids,
        "context_id": ctx,
        "successes": successes,
        "failures": failures,
    }

    learning_event_id = derive_learning_event_id(
        outcome_id, ctx, pattern, adaptations_proposed, created_at_epoch)

    event = LearningEvent(
        learning_event_id=learning_event_id,
        outcome_id=outcome_id,
        context_id=ctx,
        pattern_detected=pattern,
        adaptations_proposed=adaptations_proposed,
        adaptations_applied=adaptations_applied,
        adaptations_rejected=adaptations_rejected,
        evidence_chain=evidence_chain,
        created_at_epoch=created_at_epoch,
    )

    # Persist
    if learning_store is None:
        learning_store = LearningStore()
        close = True
    else:
        close = False
    try:
        learning_store.save(event)
    finally:
        if close:
            learning_store.close()

    return event
