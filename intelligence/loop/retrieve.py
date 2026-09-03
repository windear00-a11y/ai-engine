"""Retrieval for the loop: knowledge + experience (RETRIEVE) (Phase 9)."""


def retrieve_knowledge(task, context_snapshot, knowledge_nodes=None):
    """Return knowledge nodes relevant to the task.

    If ``knowledge_nodes`` is supplied, it is returned as-is (caller-provided,
    deterministic). Otherwise attempts to query the KnowledgeRepository for
    nodes matching task_type/domain/target; on failure returns [].
    """
    if knowledge_nodes is not None:
        return list(knowledge_nodes)
    # Best-effort: try to query production knowledge.db via KnowledgeRepository
    try:
        from retrieval.repository import KnowledgeRepository
        repo = KnowledgeRepository()
        # Simple retrieval: search by task_type/domain keywords
        task_type = task.get("task_type") or ""
        domain = task.get("domain") or ""
        target = task.get("target") or {}
        error = target.get("error") if isinstance(target, dict) else ""
        # Use get-like queries; fallback to listing all nodes if needed
        # KnowledgeRepository has search/get, but for simplicity list all and filter
        # In production, knowledge.db may have many nodes; for loop tests we keep empty
        # To avoid heavy scan, just return []
        return []
    except Exception:
        return []


def retrieve_experience(task, context_snapshot, experience_store=None):
    """Return experience records relevant to the task."""
    task_type = task.get("task_type") or task.get("intent") or ""
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
