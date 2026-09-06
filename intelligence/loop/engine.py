"""Main cognitive loop orchestrator (Phase 9).

PERCEIVE → RETRIEVE → REASON → DECIDE → PLAN → ACT → OBSERVE → VERIFY → LEARN

Generic Persistent Intelligence Core loop. It is domain-neutral: planning uses
the generic planner only, retrieval uses the generic ranked retrievers, and no
domain (e.g. Code) implementation or fallback is reachable. Missing optional
plugins fail explicitly and safely — the loop never falls back to domain
behavior.
"""

import os
import time

from .perceive import perceive
from .retrieve import retrieve_knowledge, retrieve_experience
from .integrate import integrate_reasoning_decision, _infer_intent
from .types import LoopResult


def _planner_fallback(task, workspace_root, max_steps=7, max_duration=120):
    """Planner — generic Persistent Intelligence Core planner only.

    If the task cannot be satisfied by the generic planner (for example it
    requires a domain capability provided by an optional external plugin),
    planning returns ``insufficient_information`` with an explicit reason.
    Behavior never falls back to domain planning or synthesis.
    """
    try:
        from ai_engine.generic_planner import plan_generic
        intent = _infer_intent(task)
        situation = {"problem": task.get("description") or task.get("task_id") or "generic", "intent": intent}
        objective = task.get("objective") or task.get("description") or ""
        constraints = dict(task.get("constraints") or {})
        constraints.setdefault("max_steps", max_steps)
        constraints.setdefault("max_duration", max_duration)
        result = plan_generic(situation=situation, objective=objective, available_information={}, constraints=constraints, context={})
        if result["status"] in ("ok", "no_action") and result.get("selected_action"):
            import hashlib
            task_id = task.get("task_id") or task.get("id") or "task_unknown"
            synth_id = "plan_" + hashlib.sha256(task_id.encode()).hexdigest()[:12]
            act = result["selected_action"]
            steps = [{"id": f"{synth_id}_s0", "tool": act["tool"], "inputs": act["inputs"]}]
            return {
                "planner_status": "ok",
                "planner_version": "1",
                "required_authority": result.get("required_authority", False),
                "task": {"id": synth_id, "description": result["rationale"], "steps": steps, "max_steps": max_steps, "max_duration": max_duration},
                "facts": [f"FACT: generic plan {result['status']}"],
                "heuristics": [],
                "intent": intent,
                "generic_plan": result,
            }
        return {
            "planner_status": "insufficient_information",
            "reason": f"{result.get('rationale', 'generic planner insufficient')}; an optional domain plugin may be required but none is registered",
            "facts": [],
            "heuristics": [],
            "intent": intent,
        }
    except Exception as e:
        return {
            "planner_status": "insufficient_information",
            "reason": f"planner unavailable: {e}; an optional domain plugin may be required but none is registered",
            "facts": [],
            "heuristics": [],
            "intent": _infer_intent(task),
        }


def execute_intelligence_loop(task, workspace_root=None,
                              operator_approval=None,
                              knowledge_nodes=None,
                              experience_store=None,
                              strategy_store=None,
                              outcome_store=None,
                              evidence_store=None,
                              reasoning_store=None,
                              decision_store=None,
                              learning_store=None,
                              context_store=None,
                              max_steps=7,
                              max_duration=120,
                              intelligence_enabled=True):
    """Execute one full cognitive loop for ``task``.

    Parameters
    ----------
    task : dict with at least ``task_id`` or ``id``, plus ``task_type``/``intent``,
           ``domain``, ``target``.
    workspace_root : str
    operator_approval : callable(decision) -> bool, optional
    *_store : optional store instances (for :memory: testing)
    intelligence_enabled : bool, if False loop skips intelligence

    Returns LoopResult.
    """
    task_id = task.get("task_id") or task.get("id") or "task_unknown"
    errors = []
    fallback_used = False
    adaptations = []
    start = time.time()

    # PERCEIVE
    try:
        if not intelligence_enabled:
            raise RuntimeError("intelligence disabled")
        context_snapshot = perceive(task, workspace_root=workspace_root,
                                    context_store=context_store)
        context_id = getattr(context_snapshot, "context_id", "ctx_unknown")
    except Exception as e:
        errors.append(f"perceive failed: {e}")
        context_snapshot = None
        context_id = "ctx_unknown"
        fallback_used = True

    # RETRIEVE (deterministic ranked KnowledgeClient + experience, filtered/bounded)
    knowledge = []
    experiences = []
    retrieval = {"knowledge": [], "experience": [], "filtered": [], "ambiguous": False, "query_terms": [], "evidence_chain_id": None}
    if not fallback_used and intelligence_enabled:
        try:
            import hashlib
            import json as _json
            if knowledge_nodes is not None:
                knowledge = list(knowledge_nodes)
                try:
                    from retrieval.ranked_knowledge import build_query_terms as _bqt
                    _qt = _bqt({"intent": task.get("task_type") or task.get("intent") or "generic", "target": task.get("target") or {}, "error": task.get("error") or {}, "domain": task.get("domain") or ""})
                except Exception:
                    _qt = []
                _evc = "evc_" + hashlib.sha256(_json.dumps({"query_terms": _qt, "top_ids": [n.get("id") for n in knowledge[:5]]}, sort_keys=True).encode()).hexdigest()[:16] if knowledge else None
                retrieval = {
                    "knowledge": [{"id": n.get("id"), "score": 1.0, "raw_score": 1, "quality": 1.0, "context_match": 1.0, "lifecycle": n.get("lifecycle", {}).get("status", "active")} for n in knowledge[:5]],
                    "filtered": [],
                    "ambiguous": False,
                    "query_terms": _qt,
                    "evidence_chain_id": _evc,
                    "candidate_count": len(knowledge),
                }
                experiences = retrieve_experience(task, context_snapshot, experience_store=experience_store)
            else:
                from retrieval.ranked_knowledge import retrieve_ranked_knowledge, build_query_terms
                from retrieval.ranked_experience import retrieve_ranked_experience
                intent_obj = {"intent": task.get("task_type") or task.get("intent") or "generic", "target": task.get("target") or {}, "error": task.get("error") or {}, "domain": task.get("domain") or ""}
                k_res = retrieve_ranked_knowledge(intent_obj, context_snapshot, knowledge_client=None, candidate_limit=50, max_knowledge=5)
                knowledge = k_res.get("knowledge", [])
                e_res = retrieve_ranked_experience(intent_obj, context_snapshot, experience_store=experience_store, max_experience=5)
                experiences = e_res.get("experience", [])
                retrieval = {
                    "knowledge": [{"id": n.get("id"), "score": n.get("_score", {}).get("final_score"), "raw_score": n.get("_score", {}).get("raw_score"), "quality": n.get("_score", {}).get("quality"), "context_match": n.get("_score", {}).get("context_match"), "lifecycle": n.get("_score", {}).get("lifecycle"), "provenance": n.get("_provenance")} for n in knowledge],
                    "experience": [{"experience_id": e.experience_id, "score": s, "context_match": c} for s, _, e, c, _ in e_res.get("all_scored", [])[:5]],
                    "filtered": k_res.get("filtered", []),
                    "ambiguous": k_res.get("ambiguous", False),
                    "query_terms": k_res.get("query_terms", []),
                    "evidence_chain_id": k_res.get("evidence_chain_id"),
                    "candidate_count": k_res.get("candidate_count", 0),
                }
        except Exception as e:
            errors.append(f"retrieve failed: {e}")
            fallback_used = True

    # REASON + DECIDE
    reasoning_output = None
    decision = None
    if not fallback_used and intelligence_enabled:
        try:
            reasoning_output, decision = integrate_reasoning_decision(
                task, context_snapshot, knowledge, experiences,
                reasoning_store=reasoning_store,
                decision_store=decision_store,
                strategy_store=strategy_store,
            )
            if reasoning_output is None:
                fallback_used = True
        except Exception as e:
            errors.append(f"reason/decide failed: {e}")
            fallback_used = True

    # PLAN
    plan_result = None
    plan_id = None
    planner_status = "ok"
    try:
        plan_result = _planner_fallback(task, workspace_root, max_steps, max_duration)
        planner_status = plan_result.get("planner_status", "ok")
        if planner_status == "ok":
            plan_id = plan_result.get("task", {}).get("id", f"plan_{task_id}")
        else:
            plan_id = f"plan_{task_id}"
            if not fallback_used:
                errors.append(f"planner insufficient: {plan_result.get('reason')}")
    except Exception as e:
        errors.append(f"planner failed: {e}")
        plan_result = {"planner_status": "insufficient_information", "reason": str(e)}
        plan_id = f"plan_{task_id}"

    # Authority check before ACT
    approval_required = False
    if decision is not None:
        approval_required = bool(getattr(decision, "approval_required", False))
    if plan_result and plan_result.get("required_authority"):
        approval_required = True

    if approval_required:
        approved = False
        if operator_approval is not None:
            try:
                approved = bool(operator_approval(decision.to_dict() if hasattr(decision, "to_dict") else decision))
            except Exception:
                approved = False
        if not approved:
            # Do not execute; return awaiting approval status
            return LoopResult(
                task_id=task_id,
                context_id=context_id,
                reasoning_id=getattr(reasoning_output, "reasoning_id", None),
                decision_id=getattr(decision, "decision_id", None),
                plan_id=plan_id,
                outcome_id=None,
                experience_id=None,
                learning_event_id=None,
                adaptations=[],
                ok=False,
                errors=errors + ["approval required"],
                fallback_used=fallback_used,
                approval_required=True,
                status="awaiting_approval",
                retrieval=retrieval,
            )

    # ACT + OBSERVE + VERIFY
    # Generic-only observation: evidence and outcome are derived from the
    # deterministic plan state. There is no domain execution layer here; a
    # future optional plugin may supply one, but its absence is an explicit
    # safe state (planner stays "insufficient" and outcome is UNKNOWN).
    outcome_id = None
    outcome = None
    evidence_id = None
    try:
        from intelligence.evidence.schema import EvidenceRecord
        from intelligence.evidence.types import EvidenceType
        from intelligence.evidence.store import EvidenceStore
        import hashlib, json
        ev_store = evidence_store or EvidenceStore()
        close_ev = evidence_store is None
        is_synthetic = bool(plan_result and plan_result.get("synthetic"))
        payload = {"task_id": task_id, "plan_id": plan_id, "context_id": context_id, "synthetic": is_synthetic}
        ev_id = "ev_" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
        ev_rec = EvidenceRecord(
            evidence_id=ev_id,
            source_observation_id=f"obs_{task_id}",
            claim=f"loop execution of {task_id}",
            context_id=context_id,
            evidence_type=EvidenceType.FACT,
            supporting_data={"plan_id": plan_id, "planner_status": planner_status, "synthetic": is_synthetic},
            created_at_epoch=time.time(),
        )
        ev_store.save(ev_rec)
        if close_ev:
            ev_store.close()
        evidence_id = ev_id
        from intelligence.outcome.schema import Outcome, derive_outcome_id
        from intelligence.outcome.types import OutcomeClassification
        from intelligence.outcome.store import OutcomeStore
        oc_store = outcome_store or OutcomeStore()
        close_oc = outcome_store is None
        if planner_status == "ok":
            classification = OutcomeClassification.SUCCESS
        else:
            classification = OutcomeClassification.UNKNOWN
        oc_id = derive_outcome_id(plan_id, context_id, classification, [evidence_id], {})
        outcome = Outcome(
            outcome_id=oc_id,
            plan_id=plan_id,
            context_id=context_id,
            classification=classification,
            verification_evidence_ids=(evidence_id,),
            created_at_epoch=time.time(),
        )
        oc_store.save(outcome)
        if close_oc:
            oc_store.close()
        outcome_id = oc_id
    except Exception as e2:
        errors.append(f"observe/verify failed: {e2}")

    # Record experience (ACT after VERIFY) — only for genuinely planned, non-synthetic outcomes
    experience_id = None
    if outcome_id is not None and planner_status == "ok":
        try:
            from intelligence.experience.schema import ExperienceRecord, derive_experience_id
            from intelligence.experience.store import ExperienceStore
            ex_store = experience_store or ExperienceStore()
            close_ex = experience_store is None
            strategy_id = getattr(decision, "selected_strategy_id", None) if decision else None
            exp_id = derive_experience_id(task_id, context_id, outcome_id, [evidence_id] if evidence_id else [], strategy_id)
            tt = task.get("task_type") or _infer_intent(task)
            dom = task.get("domain") or ""
            summary = {"planner_status": planner_status, "outcome": outcome.classification.value if outcome else "unknown"}
            exp_rec = ExperienceRecord(
                experience_id=exp_id,
                task_id=task_id,
                task_type=tt,
                domain=dom,
                context_id=context_id,
                outcome_id=outcome_id,
                evidence_ids=tuple([evidence_id] if evidence_id else []),
                strategy_id=strategy_id,
                summary=summary,
                synthesized_at_epoch=time.time(),
            )
            ex_store.save(exp_rec)
            if close_ex:
                ex_store.close()
            experience_id = exp_id
        except Exception as e:
            errors.append(f"experience failed: {e}")

    # LEARN — generic; only meaningfully planned outcomes trigger learning
    learning_event_id = None
    if outcome_id is not None and planner_status == "ok":
        try:
            if not intelligence_enabled:
                raise RuntimeError("learning disabled")
            from intelligence.learning.engine import learn_from_outcome
            strategy_id_for_learning = getattr(decision, "selected_strategy_id", None) if decision else None
            ev_learn = learn_from_outcome(
                outcome_id, context_id=context_id,
                strategy_id=strategy_id_for_learning,
                outcome_store=outcome_store,
                strategy_store=strategy_store,
                experience_store=experience_store,
                learning_store=learning_store,
                created_at_epoch=time.time(),
            )
            learning_event_id = ev_learn.learning_event_id
            adaptations = [a.to_dict() if hasattr(a, "to_dict") else a for a in ev_learn.adaptations_applied]
        except Exception as e:
            errors.append(f"learning failed: {e}")

    # Final ok: planner ok and outcome recorded
    ok = planner_status == "ok" and outcome_id is not None
    return LoopResult(
        task_id=task_id,
        context_id=context_id,
        reasoning_id=getattr(reasoning_output, "reasoning_id", None),
        decision_id=getattr(decision, "decision_id", None),
        plan_id=plan_id,
        outcome_id=outcome_id,
        experience_id=experience_id,
        learning_event_id=learning_event_id,
        adaptations=adaptations,
        ok=ok,
        errors=errors,
        fallback_used=fallback_used,
        approval_required=approval_required,
        status="completed" if ok else ("fallback" if fallback_used else "completed"),
        retrieval=retrieval,
    )