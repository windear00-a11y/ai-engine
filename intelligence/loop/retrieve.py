"""Retrieval for the loop: knowledge + experience (RETRIEVE) (Phase 9).

Generic Persistent Intelligence Core retrieval only. Ranked knowledge and
ranked experience live in :mod:`retrieval.ranked_knowledge` and
:mod:`retrieval.ranked_experience`. No domain import is reachable from here.
"""


def retrieve_knowledge(task, context_snapshot, knowledge_nodes=None, knowledge_client=None):
    """Return knowledge nodes relevant to the task.

    If ``knowledge_nodes`` is supplied, it is returned as-is (caller-provided,
    deterministic, backward compatible). Otherwise performs deterministic
    ranked retrieval derived from StructuredIntent/task (v1.1).
    Uses only the KnowledgeClient abstraction, never SQL.
    """
    if knowledge_nodes is not None:
        return list(knowledge_nodes)
    # Build deterministic intent-like object from task for v1.1 retrieval
    intent_obj = {
        "intent": task.get("task_type") or task.get("intent") or "generic",
        "target": task.get("target") or {},
        "error": task.get("error") or {},
        "domain": task.get("domain") or "",
    }
    # Normalize error: if target contains error string, move to error.message
    if isinstance(intent_obj["target"], dict) and "error" in intent_obj["target"] and not intent_obj["error"]:
        intent_obj["error"] = {"message": intent_obj["target"].get("error")}
    try:
        from retrieval.ranked_knowledge import retrieve_ranked_knowledge
        res = retrieve_ranked_knowledge(intent_obj, context_snapshot, knowledge_client=knowledge_client, candidate_limit=50, max_knowledge=5)
        # Return only the top nodes; filtered/ambiguous are for audit via separate call
        return res.get("knowledge", [])
    except Exception:
        return []


def retrieve_experience(task, context_snapshot, experience_store=None):
    """Return experience records relevant to the task (v1.1 ranked)."""
    # Build intent-like object for scoring
    intent_obj = {
        "intent": task.get("task_type") or task.get("intent") or "generic",
        "target": task.get("target") or {},
        "error": task.get("error") or {},
        "domain": task.get("domain") or "",
    }
    try:
        from retrieval.ranked_experience import retrieve_ranked_experience
        res = retrieve_ranked_experience(intent_obj, context_snapshot, experience_store=experience_store, max_experience=5)
        return res.get("experience", [])
    except Exception:
        # Fallback to simple task_type filter
        task_type = intent_obj["intent"]
        if experience_store is not None:
            try:
                return experience_store.for_task_type(task_type) if task_type else experience_store.all()
            except Exception:
                return []
        try:
            from intelligence.experience.store import ExperienceStore
            store = ExperienceStore()
            exps = store.for_task_type(task_type) if task_type else store.all()
            store.close()
            return exps
        except Exception:
            return []
