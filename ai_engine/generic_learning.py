"""Generic Experience / Learning / Strategy Layer (Phase 15).

Small, deterministic, auditable, policy-safe.

Uses existing Experience/Learning/Strategy stores (no new DB).
Learning is additive, provenance to experiences/outcomes, deterministic IDs,
synthetic protection, confidence != authority.

No LLM, no embeddings, no network, stdlib-only, no side effects in planner.
"""

import hashlib
import json
import time

from intelligence.experience.schema import ExperienceRecord, derive_experience_id
from intelligence.outcome.types import OutcomeClassification
from intelligence.strategy.types import candidate_confidence
from intelligence.context.diff import context_similarity

def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)

def _stable_strategy_id(situation, approach, disambiguator=None):
    """Deterministic strategy ID for generic situation + approach.

    The optional ``disambiguator`` keeps materially incompatible context
    clusters under the same situation/approach patterns distinct; it is
    deliberately absent from the primary (compatible) identity so raw
    ``context_id`` is never mandatory, just a discriminator of last resort.
    """
    payload = {"situation": situation, "approach": approach}
    if disambiguator is not None:
        payload["disambiguator"] = disambiguator
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    return "st_" + digest[:32]  # same prefix as existing strategy, deterministic

def _is_synthetic_experience(exp):
    """Check if experience is synthetic (from Phase 13 fallback)."""
    summary = exp.summary if hasattr(exp, "summary") else exp.get("summary", {}) if isinstance(exp, dict) else {}
    if isinstance(summary, dict) and summary.get("synthetic"):
        return True
    # Also check evidence synthetic flag via outcome's evidence
    return False

def _is_synthetic_outcome(outcome):
    # Check if outcome's evidence is synthetic
    # Outcome's verification_evidence_ids may point to synthetic evidence
    # For Phase 15, we check outcome classification UNKNOWN + synthetic flag in experience
    # This is a heuristic: if experience is synthetic, outcome is synthetic
    return False  # handled via experience check

# -- Phase 25: deterministic pattern normalization ---------------------------

GENERALIZATION_MIN_SAMPLES = 3
CONTEXT_COMPATIBLE_SIMILARITY = 0.5

def _canonical_pattern(value):
    """Deterministic canonical pattern from a situation/approach string.

    Reuses the existing context normalization convention (lowercase + trim)
    and collapses interior whitespace runs, so equivalent phrasings of the
    same pattern map to one canonical form.
    """
    if not isinstance(value, str):
        value = str(value)
    return " ".join(value.strip().lower().split())

def _summary_of(exp):
    if hasattr(exp, "summary"):
        return exp.summary
    if isinstance(exp, dict):
        return exp.get("summary", {})
    return {}

def _situation_raw(exp):
    summary = _summary_of(exp)
    if isinstance(summary, dict) and "situation" in summary:
        sit_obj = summary["situation"]
        if isinstance(sit_obj, dict) and sit_obj.get("problem"):
            return str(sit_obj["problem"])
        if isinstance(sit_obj, str):
            return sit_obj
    if hasattr(exp, "task_type"):
        return exp.task_type
    if isinstance(exp, dict):
        return exp.get("task_type", "generic")
    return "generic"

def situation_pattern(exp):
    """Canonical problem/situation pattern for an experience."""
    return _canonical_pattern(_situation_raw(exp))

def _approach_raw(exp, default="generic_approach"):
    summary = _summary_of(exp)
    if isinstance(summary, dict) and summary.get("approach"):
        return str(summary["approach"])
    sid = exp.strategy_id if hasattr(exp, "strategy_id") else (exp.get("strategy_id") if isinstance(exp, dict) else None)
    return str(sid) if sid else default

def approach_pattern(exp, default="generic_approach"):
    """Canonical approach/tool-sequence pattern for an experience."""
    return _canonical_pattern(_approach_raw(exp, default=default))

def _exp_context_id(exp):
    if hasattr(exp, "context_id"):
        return exp.context_id
    if isinstance(exp, dict):
        return exp.get("context_id")
    return None

def _snapshot_index(context_snapshots):
    index = {}
    for snap in context_snapshots or ():
        if hasattr(snap, "context_id"):
            cid = snap.context_id
        elif isinstance(snap, dict):
            cid = snap.get("context_id")
        else:
            cid = None
        if cid:
            index[cid] = snap
    return index

def _contexts_mergeable(a_id, b_id, snap_index):
    """Whether two contexts may share a generalized strategy.

    Follows the existing context similarity semantics:
    - identical context ids merge;
    - contexts whose compatibility is unknown (no snapshot for either side)
      are NOT split apart -- identity is context-free by construction, and
      every observed context stays listed in the applicable restrictions;
    - contexts that are both known and materially incompatible (similarity
      below the threshold) are split into distinguishable strategies.
    """
    if a_id == b_id:
        return True
    a_snap = snap_index.get(a_id)
    b_snap = snap_index.get(b_id)
    if a_snap is None or b_snap is None:
        return True
    try:
        return context_similarity(a_snap, b_snap) >= CONTEXT_COMPATIBLE_SIMILARITY
    except Exception:
        return True

def _compatible_context_groups(experiences, snap_index):
    """Deterministic partition of experience context_ids into clusters.

    Union-first over experiences ordered by context_id; pairs are merged
    unless they are demonstrably incompatible under existing context
    similarity semantics. Each cluster is a frozenset of context_ids.
    Clustering is independent of the order experiences are provided in.
    """
    parent = {}

    def _find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a, b):
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[ra] = rb

    ordered = sorted(experiences, key=lambda e: _exp_context_id(e) or "")
    cids = [_exp_context_id(e) for e in ordered]
    for i in range(len(cids)):
        for j in range(i + 1, len(cids)):
            if _contexts_mergeable(cids[i], cids[j], snap_index):
                _union(cids[i], cids[j])
    groups = {}
    for c in cids:
        groups.setdefault(_find(c), set()).add(c)
    return [frozenset(members) for members in groups.values()]

def _experience_classification(exp, outcomes):
    oid = exp.outcome_id if hasattr(exp, "outcome_id") else (exp.get("outcome_id") if isinstance(exp, dict) else None)
    if oid and isinstance(outcomes, dict) and oid in outcomes:
        oc = outcomes[oid]
        if isinstance(oc, str):
            try:
                return OutcomeClassification(oc.lower())
            except (ValueError, KeyError):
                return None
        if isinstance(oc, OutcomeClassification):
            return oc
    summary = _summary_of(exp)
    raw = summary.get("outcome") if isinstance(summary, dict) else None
    if isinstance(raw, str):
        try:
            return OutcomeClassification(raw.lower())
        except (ValueError, KeyError):
            return None
    return None

def _aggregate_candidate(sit_pat, app_pat, strategy_id, experiences, outcomes=None):
    """Deterministic aggregation of one compatible context cluster.

    Preserves supporting experience ids, supporting evidence ids, success and
    failure counts, the existing deterministic confidence formula, and the
    applicable context restrictions.
    """
    exps = sorted(experiences, key=lambda e: e.experience_id if hasattr(e, "experience_id") else str(e.get("experience_id", "")))
    if not exps:
        return None
    success_count = 0
    failure_count = 0
    for e in exps:
        cls = _experience_classification(e, outcomes)
        if cls == OutcomeClassification.SUCCESS:
            success_count += 1
        elif cls == OutcomeClassification.FAILURE:
            failure_count += 1
    sample_count = len(exps)
    success_rate = success_count / sample_count if sample_count else 0.0
    confidence = candidate_confidence(sample_count, success_rate, GENERALIZATION_MIN_SAMPLES)
    evidence_ids = []
    for e in exps:
        ev = e.evidence_ids if hasattr(e, "evidence_ids") else (e.get("evidence_ids") or [])
        for eid in ev or ():
            if eid not in evidence_ids:
                evidence_ids.append(eid)
    evidence_ids = sorted(evidence_ids)
    exp_ids = sorted(e.experience_id if hasattr(e, "experience_id") else e.get("experience_id") for e in exps)
    cids = sorted({_exp_context_id(e) for e in exps})
    context_pattern = cids[0] if cids else None
    universally_successful = sample_count > 0 and success_count == sample_count
    provenance = {
        "experience_ids": exp_ids,
        "evidence_ids": evidence_ids,
        "context_ids": cids,
        "success_count": success_count,
        "failure_count": failure_count,
        "success_rate": round(success_rate, 6),
        "confidence": confidence,
    }
    return {
        "strategy_id": strategy_id,
        "situation": _situation_raw(exps[0]),
        "situation_pattern": sit_pat,
        "approach": _approach_raw(exps[0]),
        "approach_pattern": app_pat,
        "recommendation": f"For situation {sit_pat!r}, approach {app_pat!r} succeeded {success_count}/{sample_count} times",
        "sample_count": sample_count,
        "success_count": success_count,
        "failure_count": failure_count,
        "success_rate": round(success_rate, 6),
        "confidence": confidence,
        "supporting_experience_ids": exp_ids,
        "supporting_evidence_ids": evidence_ids,
        "context_pattern": context_pattern,
        "applicable_contexts": cids,
        "context_restrictions": {
            "allowed_contexts": cids,
            "compatible_similarity_threshold": CONTEXT_COMPATIBLE_SIMILARITY,
        },
        "universally_successful": universally_successful,
        "status": "generalized",
        "provenance": provenance,
    }

def generalize_strategies_from_experiences(experiences, outcomes=None, context_snapshots=None, min_samples=None):
    """Deterministic generalized strategy aggregation across experiences.

    Groups experiences by canonical situation + canonical approach pattern,
    then splits each group by context compatibility (existing semantics).
    Each resulting cluster yields one generalized strategy preserving counts,
    confidence (existing formula), supporting ids, and applicable contexts.

    ``min_samples`` defaults to the existing minimum-evidence policy (3); a
    weak cluster is never promoted to a generalized strategy.
    """
    min_samples = GENERALIZATION_MIN_SAMPLES if min_samples is None else int(min_samples)
    snap_index = _snapshot_index(context_snapshots)
    work = sorted(experiences, key=lambda e: e.experience_id if hasattr(e, "experience_id") else str(e.get("experience_id", "")))
    groups = {}
    for e in work:
        key = (situation_pattern(e), approach_pattern(e))
        groups.setdefault(key, []).append(e)
    results = []
    for (sit_pat, app_pat) in sorted(groups):
        members = groups[(sit_pat, app_pat)]
        clusters = _compatible_context_groups(members, snap_index)
        if len(clusters) <= 1:
            strat_id = _stable_strategy_id(sit_pat, app_pat)
            if len(members) >= min_samples:
                cand = _aggregate_candidate(sit_pat, app_pat, strat_id, members, outcomes=outcomes)
                if cand:
                    results.append(cand)
            continue
        for group in sorted(clusters, key=lambda g: sorted(g) or [""]):
            cluster = [e for e in members if _exp_context_id(e) in group]
            if len(cluster) < min_samples:
                continue
            prototype = min(sorted(group), key=lambda x: x or "")
            strat_id = _stable_strategy_id(sit_pat, app_pat, prototype)
            cand = _aggregate_candidate(sit_pat, app_pat, strat_id, cluster, outcomes=outcomes)
            if cand:
                results.append(cand)
    results.sort(key=lambda c: (c["situation_pattern"], c["approach_pattern"], c["strategy_id"]))
    return results

def create_generic_experience(task_id, context_id, outcome_id, evidence_ids, strategy_id=None, task_type="generic", domain="generic", summary_extra=None, synthesized_at_epoch=None):
    """Create a generic, domain-neutral ExperienceRecord.

    Preserves coding compat: task_type/domain can be generic values like "diary" or coding values, all handled.
    """
    synthesized_at_epoch = synthesized_at_epoch or time.time()
    # Derive deterministic id
    exp_id = derive_experience_id(task_id, context_id, outcome_id, evidence_ids or [], strategy_id)
    summary = {
        "what": f"task {task_id} with {task_type}/{domain} -> outcome {outcome_id}",
        "lesson": summary_extra or {},
    }
    # Preserve any existing summary_extra as lesson
    if isinstance(summary_extra, dict):
        summary.update(summary_extra)
    return ExperienceRecord(
        experience_id=exp_id,
        task_id=task_id,
        task_type=task_type,
        domain=domain,
        context_id=context_id,
        outcome_id=outcome_id,
        evidence_ids=tuple(evidence_ids or []),
        strategy_id=strategy_id,
        summary=summary,
        synthesized_at_epoch=synthesized_at_epoch,
    )

def learn_from_generic_experience(experience, outcome, context=None, strategy_store=None, learning_store=None, experience_store=None, outcome_store=None):
    """Deterministic generic learning from a single experience.

    Rules:
        SUCCESS -> strategy candidate: "For this situation, this approach succeeded"
        FAILURE -> avoidance lesson: "This approach failed under this context; consider alternative"
        UNKNOWN -> no success strategy, remains as experience only
        Repeated -> strengthen evidence/count, not blindly multiply confidence, preserve provenance

    Returns dict with strategy_candidate, provenance, confidence handling, synthetic protection.

    This is a thin wrapper over existing learning engine, but generic and not coding-specific.
    It does NOT modify Policy/ApprovalGate.
    """
    # Synthetic protection: never create successful strategy from synthetic
    if _is_synthetic_experience(experience):
        return {
            "status": "synthetic_ignored",
            "strategy_candidate": None,
            "provenance": {"experience_id": experience.experience_id, "outcome_id": outcome.outcome_id if hasattr(outcome, "outcome_id") else outcome.get("outcome_id")},
            "reason": "synthetic experience, not treated as successful",
        }

    # Determine outcome classification
    classification = outcome.classification if hasattr(outcome, "classification") else outcome.get("classification")
    if isinstance(classification, str):
        # Normalize string to enum
        try:
            classification = OutcomeClassification(classification)
        except Exception:
            pass
    # Handle string values
    if hasattr(classification, "value"):
        cls_val = classification.value
    else:
        cls_val = str(classification)

    # Get situation pattern from experience's summary or task
    # Prefer the original problem text from summary if available (more specific than task_type)
    summary_for_sit = experience.summary if hasattr(experience, "summary") else experience.get("summary", {}) if isinstance(experience, dict) else {}
    if isinstance(summary_for_sit, dict) and "situation" in summary_for_sit:
        sit_obj = summary_for_sit["situation"]
        if isinstance(sit_obj, dict) and sit_obj.get("problem"):
            situation = sit_obj["problem"]
        elif isinstance(sit_obj, str):
            situation = sit_obj
        else:
            situation = experience.task_type if hasattr(experience, "task_type") else experience.get("task_type", "generic")
    else:
        situation = experience.task_type if hasattr(experience, "task_type") else experience.get("task_type", "generic")
    # Use context_id as situation pattern
    context_pattern = experience.context_id if hasattr(experience, "context_id") else experience.get("context_id")

    if cls_val == "success":
        # Successful outcome -> strategy candidate
        approach = _approach_raw(experience, default="generic_approach")
        # Generalized identity: canonical situation + canonical approach patterns
        # (raw context_id is NOT part of the mandatory identity).
        sit_pat = _canonical_pattern(situation)
        app_pat = _canonical_pattern(approach)
        strat_id = _stable_strategy_id(sit_pat, app_pat)
        # Check for repeated: count existing experiences with same situation and outcome SUCCESS
        # For determinism, we count via experience_store if provided
        success_count = 1
        if experience_store is not None:
            try:
                # Count successes for same task_type
                exps = experience_store.for_task_type(situation)
                for e in exps:
                    # Check if same context and success
                    if getattr(e, "context_id", None) == context_pattern and getattr(e, "outcome_id", None) != outcome.outcome_id:
                        # Need to check outcome classification
                        if outcome_store:
                            o = outcome_store.get(e.outcome_id)
                            if o and getattr(o, "classification", None) == OutcomeClassification.SUCCESS:
                                success_count += 1
            except Exception:
                pass
        # Confidence: not blindly multiplied, strengthen evidence count
        # For Phase 15, confidence is 0.5 + 0.1 * min(success_count, 5) capped at 0.9, deterministic
        confidence = min(0.9, 0.5 + 0.1 * min(success_count, 5))
        # Provenance
        provenance = {
            "experience_id": experience.experience_id if hasattr(experience, "experience_id") else experience.get("experience_id"),
            "outcome_id": outcome.outcome_id if hasattr(outcome, "outcome_id") else outcome.get("outcome_id"),
            "context_id": context_pattern,
            "evidence_ids": list(experience.evidence_ids if hasattr(experience, "evidence_ids") else experience.get("evidence_ids", [])),
            "success_count": success_count,
        }
        candidate = {
            "strategy_id": strat_id,
            "situation": situation,
            "situation_pattern": sit_pat,
            "context_pattern": context_pattern,
            "approach": approach,
            "approach_pattern": app_pat,
            "recommendation": f"For situation {situation}, approach {approach} previously succeeded",
            "supporting_experience_ids": [provenance["experience_id"]],
            "supporting_evidence_ids": provenance["evidence_ids"],
            "success_count": success_count,
            "confidence": confidence,
            "context_restrictions": {"allowed_contexts": [context_pattern]},
            "applicable_contexts": [context_pattern],
            "status": "candidate",
            "provenance": provenance,
        }
        return {"status": "success_strategy", "strategy_candidate": candidate, "provenance": provenance}

    elif cls_val == "failure":
        # Failed outcome -> avoidance lesson
        approach = _approach_raw(experience, default="generic_approach")
        sit_pat = _canonical_pattern(situation)
        app_pat = _canonical_pattern(approach)
        strat_id = _stable_strategy_id(f"{sit_pat}:avoid", app_pat)
        provenance = {
            "experience_id": experience.experience_id if hasattr(experience, "experience_id") else experience.get("experience_id"),
            "outcome_id": outcome.outcome_id if hasattr(outcome, "outcome_id") else outcome.get("outcome_id"),
            "context_id": context_pattern,
            "evidence_ids": list(experience.evidence_ids if hasattr(experience, "evidence_ids") else experience.get("evidence_ids", [])),
        }
        candidate = {
            "strategy_id": strat_id,
            "situation": situation,
            "situation_pattern": sit_pat,
            "context_pattern": context_pattern,
            "approach": approach,
            "approach_pattern": app_pat,
            "recommendation": f"Approach {approach} failed under {situation}/{context_pattern}; consider alternative",
            "supporting_experience_ids": [provenance["experience_id"]],
            "supporting_evidence_ids": provenance["evidence_ids"],
            "failure_count": 1,
            "confidence": 0.3,
            "context_restrictions": {"allowed_contexts": [context_pattern]},
            "applicable_contexts": [context_pattern],
            "status": "avoidance",
            "provenance": provenance,
        }
        return {"status": "failure_lesson", "strategy_candidate": candidate, "provenance": provenance}

    else:  # UNKNOWN
        return {
            "status": "no_strategy",
            "strategy_candidate": None,
            "provenance": {"experience_id": experience.experience_id if hasattr(experience, "experience_id") else experience.get("experience_id")},
            "reason": "unknown outcome, no success strategy",
        }

def should_apply_strategy(strategy_candidate, current_evidence, current_context):
    """Planner integration: if strategy conflicts with current evidence/context, current wins.

    Returns True if strategy should be applied, False if it conflicts.
    """
    if strategy_candidate is None:
        return False
    # If current evidence contradicts strategy's supporting evidence, surface conflict
    # For Phase 15, simple check: if current context differs from strategy's context pattern, don't blindly follow
    strat_ctx = strategy_candidate.get("context_pattern")
    allowed = (strategy_candidate.get("context_restrictions") or {}).get("allowed_contexts") or []
    curr_ctx = current_context_id(current_context) if isinstance(current_context, dict) else getattr(current_context, "context_id", None)
    # Phase 25: a generalized strategy applies only within its applicable contexts.
    if allowed and curr_ctx is not None:
        return curr_ctx in allowed or (strat_ctx is not None and curr_ctx == strat_ctx)
    # If contexts differ significantly, don't apply (would be in diff's low similarity)
    # For now, only apply if same context or no context filter
    if strat_ctx and curr_ctx and strat_ctx != curr_ctx:
        # Check similarity: if very different, don't apply
        # For simplicity, require exact match for now
        return False
    return True

def current_context_id(ctx):
    if isinstance(ctx, dict):
        return ctx.get("context_id") or ctx.get("ctx_id")
    return getattr(ctx, "context_id", None)
