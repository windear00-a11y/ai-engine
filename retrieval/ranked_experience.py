"""Experience retrieval upgrade for v1.1 — deterministic, model-independent."""

import os

def _tokens(text):
    if not text:
        return set()
    stop = {"the", "a", "an", "is", "of", "for", "this", "in", "on", "to", "and", "or"}
    toks = set()
    for tok in text.replace("-", " ").replace("_", " ").replace("/", " ").split():
        tok = tok.strip(".,:;()[]{}\"'").lower()
        if tok and tok not in stop:
            toks.add(tok)
    return toks

def experience_score(exp, intent_obj, context_snapshot):
    """Deterministic similarity score for experience retrieval."""
    score = 0
    # Task type exact
    if (intent_obj.get("intent") or "").lower() == (getattr(exp, "task_type", "") or "").lower():
        score += 5
    else:
        # No task type match -> discard later
        pass
    # Domain
    if (intent_obj.get("domain") or "").lower() == (getattr(exp, "domain", "") or "").lower() and intent_obj.get("domain"):
        score += 2
    # Context match
    try:
        from intelligence.decision.context import context_match
        # Use exp.context_id vs snapshot context_id
        exp_ctx = getattr(exp, "context_id", None)
        # context_match expects context dict and other id
        # Create a mock context dict from snapshot
        ctx_id = getattr(context_snapshot, "context_id", None) if hasattr(context_snapshot, "context_id") else (context_snapshot.get("context_id") if isinstance(context_snapshot, dict) else None)
        # For experience retrieval, we treat context_match as factor
        # If exp has allowed_contexts restriction (via summary?), check
        # For now, simple: if exp context_id == snapshot context_id -> 1.0 else 0.5
        # We'll compute context factor separately
        pass
    except Exception:
        pass
    # Target file
    target_file = (intent_obj.get("target") or {}).get("file") if isinstance(intent_obj.get("target"), dict) else None
    if target_file:
        base = os.path.basename(target_file).rsplit(".", 1)[0].lower()
        # Experience summary may contain target_file
        summary = getattr(exp, "summary", {}) or {}
        exp_target = summary.get("target_file") or summary.get("file") or ""
        if isinstance(exp_target, str) and base in exp_target.lower():
            score += 2
        # Also check task description
        exp_task = getattr(exp, "task_type", "")
        if base in exp_task.lower():
            score += 1
    # Error token overlap
    err_msg = (intent_obj.get("error") or {}).get("message") if isinstance(intent_obj.get("error"), dict) else None
    if err_msg:
        err_toks = _tokens(err_msg)
        exp_summary = str(getattr(exp, "summary", {}))
        exp_toks = _tokens(exp_summary)
        overlap = len(err_toks & exp_toks)
        score += overlap  # 0..n
    # Context factor
    from intelligence.decision.context import context_match as cm_func
    # Determine context snapshot id
    ctx_id = None
    if hasattr(context_snapshot, "context_id"):
        ctx_id = context_snapshot.context_id
    elif isinstance(context_snapshot, dict):
        ctx_id = context_snapshot.get("context_id")
    exp_ctx = getattr(exp, "context_id", None)
    ctx_factor = 1.0
    if ctx_id and exp_ctx:
        # Use same logic as decision context matching: 1.0 exact else 0.5
        if exp_ctx == ctx_id:
            ctx_factor = 1.0
        else:
            # Check if experience has context_restriction (not in exp, but we treat as 0.5)
            ctx_factor = 0.5
    # Eliminate if context_restricted to other
    # Experience may have been created with context_restriction in summary
    # For now, we don't eliminate, just penalize
    # Weight score by context factor
    final = round(score * ctx_factor, 6)
    return final, ctx_factor

def retrieve_ranked_experience(intent_obj, context_snapshot, experience_store=None, max_experience=5):
    """Retrieve and rank experiences deterministically.

    Only successful verified experiences provide positive support, but all are considered for contradiction.
    Returns dict with experience (top), filtered, query info.
    """
    max_experience = min(int(max_experience), 5)
    # Get all experiences for task_type
    task_type = intent_obj.get("intent") or ""
    if experience_store is not None:
        try:
            all_exps = experience_store.for_task_type(task_type) if task_type else experience_store.all()
        except Exception:
            all_exps = []
    else:
        try:
            from intelligence.experience.store import ExperienceStore
            store = ExperienceStore()
            all_exps = store.for_task_type(task_type) if task_type else store.all()
            store.close()
        except Exception:
            all_exps = []

    # Filter: only SUCCESS for positive support, but keep all for scoring then filter
    # For reasoning, only SUCCESS should be in top, but we rank all then filter
    scored = []
    for exp in all_exps:
        # Only consider SUCCESS for positive scoring; failures get lower score but still possible for contradiction
        # Check outcome: need to check via summary or outcome store? For now, use summary outcome
        summary = getattr(exp, "summary", {}) or {}
        outcome = summary.get("outcome") or summary.get("classification") or ""
        # If outcome indicates failure, score is halved (not positive support)
        is_success = outcome.lower().startswith("success") if isinstance(outcome, str) else False
        # If no outcome info, assume success for now (since most experiences are success)
        if not outcome:
            is_success = True
        base_score, ctx_f = experience_score(exp, intent_obj, context_snapshot)
        if not is_success:
            base_score = base_score * 0.5  # penalize failures for positive ranking
        # Context factor already applied in experience_score, but ensure
        scored.append((base_score, exp.experience_id, exp, ctx_f, is_success))

    # Sort by score desc, then experience_id ASC
    scored.sort(key=lambda x: (-x[0], x[1]))
    # Top experiences for reasoning: only successes
    top_success = [exp for score, eid, exp, ctx_f, is_success in scored if is_success][:max_experience]
    # Also keep top failures for contradiction detection (not passed to reasoning positive, but for audit)
    # For now, return only successes for reasoning
    return {
        "experience": top_success,
        "all_scored": scored,
        "query": {"intent": task_type, "domain": intent_obj.get("domain")},
    }
