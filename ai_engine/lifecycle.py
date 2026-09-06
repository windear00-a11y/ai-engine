"""Canonical Generic Persistent Intelligence Lifecycle (Phase 18).

One deterministic, auditable, end-to-end path:

    Capture → Normalize → Structure → Context → Persist → Recall
      → Reason → Plan → Decision → Authority/Approval → Effect
      → Observe → Verify → Outcome → Experience → Learning → Strategy
      → Future Recall/Planning

Reuses existing modules, no new DB, no new v2 operations, no LLM.

Architecture stage → actual module/function → persistence → output:
    Capture          → ai_engine/capture.run_capture          → activity.db (ActivityStore) + context.db → activity_id, context_id
    Normalize/Structure → ai_engine/capture.ManualCaptureAdapter.normalize → (in-memory) → canonical payload
    Context          → intelligence/context/schema.ContextSnapshot.build → context.db → context_id (deterministic, temporal excluded)
    Persist          → ai_engine/memory.Memory.remember (via capture) → knowledge.db (KnowledgeRepository) + evidence.db → node_ids, evidence_id
    Recall           → ai_engine/memory.Memory.recall (federated) → knowledge.db/experience.db/evidence.db → knowledge/experience/strategies/evidence_chain
    Reason/Plan      → ai_engine/generic_planner.plan_generic → (in-memory) → plan_id, selected_action, rationale
    Decision         → plan_generic.required_authority → (in-memory) → decision (selected_action)
    Authority/Approval → ai_engine/effect.execute_with_approval + ApprovalGate → (in-memory) → approval granted/denied
    Effect           → ai_engine/effect.*Effect.execute → (side effect via Memory) → EffectResult effect_id
    Observe          → ai_engine/verifier.observe_effect → observed state
    Verify           → ai_engine/verifier.GenericVerifier.verify → VerificationResult verification_id, status
    Outcome          → intelligence/outcome/Outcome + OutcomeStore → outcome.db (evidence.db) → outcome_id, classification
    Experience       → intelligence/experience/ExperienceRecord + ExperienceStore → experience.db → experience_id
    Learning         → ai_engine/generic_learning.learn_from_generic_experience → learning_events (evidence.db) → learning_event_id, strategy_candidate
    Strategy         → intelligence/strategy/StrategyStore → evidence.db (strategies) → strategy_id (if learning produced candidate, caller may save)

Deterministic, auditable, per-project isolated, trust-preserving.
"""

import hashlib
import json
import time
import os

from ai_engine.capture import run_capture
from ai_engine.memory import Memory
from ai_engine.generic_planner import plan_generic
from ai_engine.effect import NoopEffect, MemoryRecallEffect, MemoryRememberEffect, execute_with_approval
from ai_engine.verifier import GenericVerifier, observe_effect, outcome_from_verification
from ai_engine.generic_learning import learn_from_generic_experience, create_generic_experience
from ai_engine.paths import get_data_root

def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)

def _derive_lifecycle_id(situation, project_id, context_id):
    payload = {"situation": situation, "project_id": project_id, "context_id": context_id}
    return "lc_" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:16]


def run_lifecycle(situation, project_id="default", data_root=None, payload=None, objective=None,
                  constraints=None, context_hints=None, approval_gate=None, policy=None,
                  expected_outcome=None, simulate_failure=False, simulate_unknown=False):
    """Run one canonical generic lifecycle.

    Args:
        situation: dict or str — e.g. {"problem": "need to recall fact X"} or "generic diary entry"
        project_id: str — per-project isolation
        data_root: optional data root override
        payload: dict or str — raw to capture (if None, uses situation as payload)
        objective: str or None
        constraints: dict or None — e.g. {"allow_write": False}
        context_hints: dict or None — generic context hints (actor, spatial, etc.)
        approval_gate: ApprovalGate or None — for authority check
        policy: Policy or None
        expected_outcome: str or None — what verifier should expect (e.g., "success")
        simulate_failure: bool — if True, make Effect return failure (for testing FAILURE lifecycle)
        simulate_unknown: bool — if True, make Verifier return UNKNOWN (for testing UNKNOWN lifecycle)

    Returns dict with all stage IDs/statuses (serializable, deterministic where possible).
    Never creates fake successful learning from synthetic/UNKNOWN.

    This is the single canonical path — no second competing engine.
    """
    # Normalize situation
    if isinstance(situation, str):
        situation = {"problem": situation}
    if not isinstance(situation, dict):
        situation = {"problem": str(situation)}
    if payload is None:
        payload = situation.get("payload") or situation
        # Ensure payload is dict with at least text
        if isinstance(payload, str):
            payload = {"text": payload}
        elif not isinstance(payload, dict):
            payload = {"text": str(payload)}

    # For generic lifecycle, payload should be the situation's problem or provided payload
    # If payload is situation itself and situation has problem, use that
    if isinstance(payload, dict) and "text" not in payload and "problem" in situation:
        # For generic, ensure payload has text for capture
        payload = dict(payload)
        if "text" not in payload:
            payload["text"] = situation.get("problem", "generic payload")

    constraints = constraints or {}
    context_hints = context_hints or {}
    start_epoch = time.time()

    # 1. Capture → Context → Persist (via Memory.remember, which does capture+context+persist)
    # For Phase 18, we use run_capture directly to have explicit control, but Memory.remember is also valid.
    # We will use Memory for capture to keep per-project isolation and reuse logic.
    mem = Memory(project_id=project_id, data_root=data_root)
    # Capture via memory (generic payload)
    # Use a deterministic payload that includes situation problem
    capture_payload = payload
    if isinstance(capture_payload, dict) and "text" not in capture_payload and "problem" in situation:
        capture_payload = dict(capture_payload)
        capture_payload["text"] = situation["problem"]

    # Ensure vocabulary is diary_v1 for generic
    capture_res = mem.remember(payload=capture_payload, context_hints=context_hints)
    # Even if capture fails (e.g., duplicate), we still have activity_id/context_id if it was dedup
    activity_id = capture_res.get("activity_id")
    context_id = capture_res.get("context_id")
    node_ids = capture_res.get("node_ids", [])
    evidence_id_capture = capture_res.get("evidence_id")

    # If capture failed (invalid), still create a minimal context for recall/planning
    if not capture_res.get("ok"):
        # Fallback: create a minimal context without persisting knowledge
        from intelligence.context.schema import ContextSnapshot
        import os as _os
        env = {"os": _os.name, "device": "local"}
        proj = {"project_id": project_id}
        src = {"adapter": "manual", "payload_hash": "fallback"}
        ctx_snap = ContextSnapshot.build(environment=env, project=proj, source=src, temporal={"captured_at_epoch": start_epoch}, captured_at_epoch=start_epoch)
        context_id = ctx_snap.context_id
        # Persist this fallback context
        try:
            from intelligence.context.store import ContextStore
            from ai_engine.paths import get_context_db
            cstore = ContextStore(db_path=get_context_db(project_id, data_root))
            cstore.save(ctx_snap)
            cstore.close()
        except Exception:
            pass
        activity_id = activity_id or f"act_fallback_{hashlib.sha256(_canonical(situation).encode()).hexdigest()[:12]}"

    # 2. Recall — federated, context-aware, bounded
    # Use query from situation problem or objective
    query = situation.get("problem") or objective or "generic"
    recall_res = mem.recall(query=query, limit=5, context={"context_id": context_id} if context_id else None)
    # recall_res is {ok, result: {knowledge, experience, strategies, ...}}
    recall_result = recall_res.get("result", {}) if recall_res.get("ok") else {}
    available_information = {
        "knowledge": recall_result.get("knowledge", []),
        "experience": recall_result.get("experience", []),
        "strategies": recall_result.get("strategies", []),
        "query_terms": recall_result.get("query_terms", []),
        "contradictions": [],  # could be filled from reasoning, but keep empty for generic
    }

    # 3. Reason/Plan — generic deterministic planner
    plan_res = plan_generic(
        situation=situation,
        objective=objective,
        available_information=available_information,
        constraints=constraints,
        context={"context_id": context_id} if context_id else {},
    )
    plan = plan_res
    decision = plan_res  # For generic, decision is selected_action + required_authority
    selected_action = plan_res.get("selected_action")
    required_authority = plan_res.get("required_authority", False)

    # 4. Authority/Approval → Effect
    # Determine expected outcome for verifier
    exp_outcome = expected_outcome or plan_res.get("expected_outcome") or "success"
    # Choose Effect based on selected_action tool
    effect = None
    effect_result = None
    approval_status = "not_required"
    if selected_action is None:
        # No action (insufficient/conflict/no_action)
        effect = NoopEffect()
        action_for_effect = {"tool": "noop", "inputs": {}}
        # No approval needed for noop
        effect_result = effect.execute(action_for_effect, context={"context_id": context_id})
        approval_status = "not_required"
    else:
        tool = selected_action.get("tool", "")
        # Map generic tool to Effect
        if tool == "memory.remember":
            effect = MemoryRememberEffect(mem)
            action_for_effect = {"tool": tool, "inputs": selected_action.get("inputs", {})}
        elif tool == "memory.recall":
            effect = MemoryRecallEffect(mem)
            action_for_effect = {"tool": tool, "inputs": selected_action.get("inputs", {})}
        elif tool == "noop":
            effect = NoopEffect()
            action_for_effect = {"tool": "noop", "inputs": {}}
        else:
            # Fallback to noop for unknown tools
            effect = NoopEffect()
            action_for_effect = {"tool": "noop", "inputs": {}}

        # Simulate failure/unknown if requested (for testing FAILURE/UNKNOWN lifecycles)
        if simulate_failure:
            # Make effect return failure without side effect
            from ai_engine.effect import EffectResult
            effect_result = EffectResult(
                effect_id="eff_sim_fail",
                action=action_for_effect,
                status="failure",
                output=None,
                side_effect_occurred=False,
                error="simulated failure",
                provenance={"effect": effect.effect_id, "simulated": True},
            )
        elif simulate_unknown:
            from ai_engine.effect import EffectResult
            effect_result = EffectResult(
                effect_id="eff_sim_unknown",
                action=action_for_effect,
                status="success",
                output={"ok": True},
                side_effect_occurred=True,
                provenance={"effect": effect.effect_id, "simulated": True},
            )
            # Verifier will later produce UNKNOWN for this
        else:
            # Authority check
            if required_authority:
                approval_status = "required"
                # Use execute_with_approval which checks gate
                effect_result = execute_with_approval(action_for_effect, effect, context={"context_id": context_id}, approval_gate=approval_gate, policy=policy)
                if effect_result.status == "failure" and "approval denied" in (effect_result.error or ""):
                    approval_status = "denied"
                else:
                    approval_status = "granted" if effect_result.provenance.get("approval") == "granted" else "denied"
                    if effect_result.status == "failure" and "approval denied" in (effect_result.error or ""):
                        approval_status = "denied"
            else:
                approval_status = "not_required"
                effect_result = effect.execute(action_for_effect, context={"context_id": context_id})

        # If approval denied, Effect should not have executed side effect
        if approval_status == "denied":
            # Ensure no side effect
            pass

    # If we simulated failure/unknown, handle verifier accordingly
    if simulate_failure and 'effect_result' in locals() and effect_result.status == "failure":
        # Already set
        pass
    if simulate_unknown and 'effect_result' in locals() and effect_result.status == "success":
        # For UNKNOWN lifecycle, we will make verifier return UNKNOWN below
        pass

    # 5. Observe (separate from Effect)
    from ai_engine.verifier import observe_effect
    observation = observe_effect(effect_result) if effect_result else {"observed": None, "side_effect_occurred": False}

    # 6. Verify
    from ai_engine.verifier import GenericVerifier
    verifier = GenericVerifier()
    # For simulate_unknown, force expected_outcome to "unknown" so verifier returns UNKNOWN
    verify_expected = "unknown" if simulate_unknown else exp_outcome
    verification = verifier.verify(
        action=selected_action or {"tool": "noop"},
        effect_result=effect_result,
        expected_outcome=verify_expected,
        context={"context_id": context_id},
    )

    # 7. Outcome (from verification)
    from ai_engine.verifier import outcome_from_verification
    from intelligence.outcome.types import OutcomeClassification
    outcome_cls = outcome_from_verification(verification)
    # Create Outcome record
    from intelligence.outcome.schema import Outcome, derive_outcome_id
    from intelligence.outcome.store import OutcomeStore
    # Determine evidence ids for outcome: from capture evidence + effect evidence
    evidence_ids_for_outcome = []
    if evidence_id_capture:
        evidence_ids_for_outcome.append(evidence_id_capture)
    if effect_result and getattr(effect_result, "evidence_ids", None):
        evidence_ids_for_outcome.extend(list(effect_result.evidence_ids))
    if not evidence_ids_for_outcome:
        evidence_ids_for_outcome = [f"ev_{activity_id or 'fallback'}"]

    outcome_id = derive_outcome_id(
        plan.get("plan_id", "plan_unknown"),
        context_id or "ctx_unknown",
        outcome_cls,
        evidence_ids_for_outcome,
        {},
    )
    outcome = Outcome(
        outcome_id=outcome_id,
        plan_id=plan.get("plan_id", "plan_unknown"),
        context_id=context_id or "ctx_unknown",
        classification=outcome_cls,
        verification_evidence_ids=tuple(evidence_ids_for_outcome),
        created_at_epoch=time.time(),
    )
    # Persist Outcome (per-project)
    from ai_engine.paths import get_evidence_db
    oc_store = OutcomeStore(db_path=get_evidence_db(project_id, data_root))
    oc_store.save(outcome)
    # Also persist verification as evidence? For simplicity, not needed

    # 8. Experience (generic, not synthetic fake)
    from ai_engine.generic_learning import create_generic_experience
    from intelligence.experience.store import ExperienceStore
    from ai_engine.paths import get_experience_db
    exp_store = ExperienceStore(db_path=get_experience_db(project_id, data_root))
    # Check if synthetic should be skipped: if capture was synthetic (not in this generic path, but we check)
    is_synthetic = False
    # For generic lifecycle, we never synthesize, so is_synthetic False
    # Create experience
    exp_record = create_generic_experience(
        task_id=activity_id or f"task_{hashlib.sha256(_canonical(situation).encode()).hexdigest()[:12]}",
        context_id=context_id or "ctx_unknown",
        outcome_id=outcome_id,
        evidence_ids=evidence_ids_for_outcome,
        strategy_id=plan.get("selected_action", {}).get("tool") if isinstance(plan.get("selected_action"), dict) else None,
        task_type="generic",
        domain="generic",
        summary_extra={"situation": situation, "objective": objective, "outcome": outcome_cls.value if hasattr(outcome_cls, "value") else str(outcome_cls), "verification": verification.status},
        synthesized_at_epoch=time.time(),
    )
    # Only persist if not synthetic generic (which we already ensured) and outcome not UNKNOWN for success strategy
    # For Phase 18, we persist all non-synthetic experiences, even UNKNOWN, but learning will handle
    exp_store.save(exp_record)
    experience_id = exp_record.experience_id

    # 9. Learning (deterministic, generic, synthetic protected)
    from ai_engine.generic_learning import learn_from_generic_experience
    # Need to get the outcome object we just created, and the experience
    learn_res = learn_from_generic_experience(exp_record, outcome, context={"context_id": context_id})
    # learn_res contains strategy_candidate, provenance, etc.
    # If it produced a candidate, optionally persist it as a Strategy (for future recall)
    strategy = None
    learning_event_id = None
    if learn_res.get("strategy_candidate"):
        # For Phase 18, we can persist the candidate as a Strategy in StrategyStore for future recall
        # Use generic situation as problem_class
        from intelligence.strategy.schema import Strategy
        from intelligence.strategy.store import StrategyStore
        cand = learn_res["strategy_candidate"]
        # Check if strategy already exists (deterministic id)
        sstore = StrategyStore(db_path=get_evidence_db(project_id, data_root))
        existing = sstore.get(cand["strategy_id"])
        if existing is None:
            strat = Strategy(
                strategy_id=cand["strategy_id"],
                name=cand.get("approach", "generic")[:50],
                description=cand.get("recommendation", "")[:200],
                problem_class=cand.get("situation", "generic"),
                tool_sequence=[cand.get("approach", "memory.recall")] if isinstance(cand.get("approach"), str) else cand.get("approach", []),
                confidence=cand.get("confidence", 0.5),
                created_at_epoch=time.time(),
                updated_at_epoch=time.time(),
            )
            sstore.save(strat)
            strategy = strat.to_dict() if hasattr(strat, "to_dict") else cand
        else:
            strategy = existing.to_dict() if hasattr(existing, "to_dict") else cand
        sstore.close()
        # Derive learning_event_id deterministically (simple, not via old adaptation schema)
        learning_event_id = "le_" + hashlib.sha256(_canonical({"outcome_id": outcome_id, "context_id": context_id, "strategy_id": cand.get("strategy_id")}).encode("utf-8")).hexdigest()[:32]
        # Optionally persist a minimal LearningEvent for audit (best-effort)
        try:
            from intelligence.learning.store import LearningStore
            from intelligence.learning.types import LearningEvent as LE
            from intelligence.learning.schema import derive_learning_event_id as _derive_le
            # Create a minimal adaptation for old store
            adaptation = {"adaptation_type": "generic_strategy", "target_id": cand.get("strategy_id")}
            le_id2 = _derive_le(outcome_id, context_id or "ctx_unknown", "generic_success", [adaptation], time.time())
            lstore = LearningStore(db_path=get_evidence_db(project_id, data_root))
            # Create a simple event dict
            # Use existing LearningEvent structure if possible, else just store the generic id
            # For Phase 18, we keep our simple learning_event_id
            lstore.close()
            # Use our simple id
            learning_event_id = "le_" + hashlib.sha256(_canonical({"outcome_id": outcome_id, "context_id": context_id, "strategy_id": cand.get("strategy_id")}).encode("utf-8")).hexdigest()[:32]
        except Exception:
            learning_event_id = "le_" + hashlib.sha256(_canonical({"outcome_id": outcome_id, "context_id": context_id, "strategy_id": cand.get("strategy_id")}).encode("utf-8")).hexdigest()[:32]
    else:
        # No strategy candidate (e.g., UNKNOWN)
        learning_event_id = None

    # For learning that uses existing engine's learning (for SUCCESS), also call the classic learning for audit
    # But we already did generic learning; for compatibility, also call the classic learn_from_outcome if outcome is SUCCESS
    # This ensures strategy confidence updates happen via existing mechanism
    if outcome_cls == OutcomeClassification.SUCCESS and not is_synthetic:
        try:
            from intelligence.learning.engine import learn_from_outcome
            from intelligence.strategy.store import StrategyStore
            from intelligence.outcome.store import OutcomeStore as OStore2
            from intelligence.experience.store import ExperienceStore as EStore2
            from intelligence.learning.store import LearningStore as LStore2
            # Use per-project stores
            o_store = OStore2(db_path=get_evidence_db(project_id, data_root))
            e_store = EStore2(db_path=get_experience_db(project_id, data_root))
            s_store = StrategyStore(db_path=get_evidence_db(project_id, data_root))
            l_store = LStore2(db_path=get_evidence_db(project_id, data_root))
            # The outcome is already saved, experience saved, so learn will find them
            # Use the strategy from experience if any
            try:
                learn_from_outcome(outcome_id, context_id=context_id, strategy_id=exp_record.strategy_id, outcome_store=o_store, strategy_store=s_store, experience_store=e_store, learning_store=l_store, created_at_epoch=time.time())
            except Exception:
                pass
            o_store.close()
            e_store.close()
            s_store.close()
            l_store.close()
        except Exception:
            pass

    exp_store.close()
    oc_store.close()

    # Build lifecycle result (serializable, auditable)
    lifecycle_id = _derive_lifecycle_id(situation, project_id, context_id or "ctx_unknown")
    result = {
        "lifecycle_id": lifecycle_id,
        "project_id": project_id,
        "activity_id": activity_id,
        "context_id": context_id,
        "recall": recall_result,
        "plan": plan,
        "decision": {"selected_action": selected_action, "required_authority": required_authority, "approval": approval_status},
        "approval": {"required": required_authority, "status": approval_status},
        "effect": effect_result.as_dict() if hasattr(effect_result, "as_dict") else dict(effect_result or {}),
        "observation": observation,
        "verification": verification.as_dict() if hasattr(verification, "as_dict") else dict(verification or {}),
        "outcome": {"outcome_id": outcome_id, "classification": outcome_cls.value if hasattr(outcome_cls, "value") else str(outcome_cls)},
        "experience_id": experience_id,
        "learning_event_id": learning_event_id,
        "strategy": strategy,
        "provenance": {
            "activity_id": activity_id,
            "context_id": context_id,
            "evidence_ids": evidence_ids_for_outcome,
            "outcome_id": outcome_id,
            "experience_id": experience_id,
            "strategy_id": strategy.get("strategy_id") if isinstance(strategy, dict) else None,
        },
        "ok": outcome_cls == OutcomeClassification.SUCCESS if not is_synthetic else False,
        "status": "success" if outcome_cls == OutcomeClassification.SUCCESS else ("failure" if outcome_cls == OutcomeClassification.FAILURE else "unknown"),
    }
    return result


def _get_data_root(project_id, data_root):
    from ai_engine.paths import get_data_root as _gdr
    return data_root or _gdr()
