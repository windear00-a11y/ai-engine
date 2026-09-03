"""Context capture for the loop (PERCEIVE) (Phase 9)."""

from intelligence.context.capture import capture_context


def perceive(task, workspace_root=None, context_store=None):
    """Capture operational context for a task.

    Returns a ContextSnapshot (with context_id). If capture fails, returns a
    minimal synthetic snapshot so the loop can degrade gracefully.
    """
    task_metadata = {
        "type": task.get("task_type") or task.get("intent") or "",
        "domain": task.get("domain") or "",
        "error_pattern": (task.get("target") or {}).get("error", "") if isinstance(task.get("target"), dict) else "",
    }
    try:
        snapshot = capture_context(project_root=workspace_root,
                                   task_metadata=task_metadata)
    except Exception:
        # Fallback synthetic context
        from intelligence.context.schema import ContextSnapshot
        from intelligence.context.types import ProjectContext, SystemContext, TaskContext, TemporalContext
        import time
        snapshot = ContextSnapshot.build(
            system=SystemContext(os="linux", arch="x86_64", python="3.11").as_dict(),
            project=ProjectContext(language="python", build="setuptools").as_dict(),
            task=TaskContext(type=task_metadata["type"], domain=task_metadata["domain"],
                             error_pattern=task_metadata["error_pattern"]).as_dict(),
            temporal=TemporalContext(captured_at_epoch=time.time(), timezone="UTC").as_dict(),
            captured_at_epoch=time.time(),
        )
    # Optionally persist
    if context_store is not None:
        try:
            context_store.save(snapshot)
        except Exception:
            pass
    else:
        try:
            from intelligence.context.store import ContextStore
            store = ContextStore()
            store.save(snapshot)
            store.close()
        except Exception:
            pass
    return snapshot
