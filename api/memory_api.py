"""Memory API — deterministic, per-project, over Memory (v2).

Wraps ai_engine.memory.Memory for the v2 contract. No trust bypass:
project isolation via get_project_dir, vocabulary validation via Memory,
deterministic ids via Memory, no network, no LLM.
"""

from ai_engine.memory import Memory
from ai_engine.paths import DEFAULT_PROJECT_ID


class MemoryAPI:
    """Thin, project-aware wrapper over Memory.

    Each operation creates a per-project Memory instance (isolated via
    data_root + project_id). No shared state, no global DB.

    data_root: optional override for XDG resolution (for testing).
    default_vocabulary: fallback vocabulary id (diary_v1).
    """

    def __init__(self, data_root=None, default_vocabulary="diary_v1"):
        self.data_root = data_root
        self.default_vocabulary = default_vocabulary

    def _memory_for(self, project_id=None, vocabulary_id=None):
        from api.errors import KnowledgeArgumentError
        pid = project_id or DEFAULT_PROJECT_ID
        vocab = vocabulary_id or self.default_vocabulary
        try:
            return Memory(project_id=pid, data_root=self.data_root, vocabulary_id=vocab)
        except ValueError as exc:
            raise KnowledgeArgumentError(str(exc)) from exc

    @staticmethod
    def _contract_error(res, fallback):
        """Map a Memory failure to a stable contract error message.

        Validation failures (invalid_argument) surface their user-facing
        message; internal failures never surface raw exception text, paths,
        or sqlite details across the public boundary.
        """
        from api.errors import KnowledgeArgumentError
        if res.get("code") == "invalid_argument":
            return KnowledgeArgumentError(res.get("error") or fallback)
        return KnowledgeArgumentError(fallback)

    def remember(self, payload, context_hints=None, project_id=None, vocabulary_id=None):
        # Generic payload (arbitrary structured memory payload). Text can be inside payload as {"text": "hello"}.
        if not isinstance(payload, dict):
            from api.errors import KnowledgeArgumentError
            raise KnowledgeArgumentError("payload must be a JSON object")
        mem = self._memory_for(project_id, vocabulary_id)
        # Memory.remember handles both text and structured payloads generically via capture pipeline
        # For generic v2, payload is the raw to capture; context_hints passed separately
        res = mem.remember(payload=payload, context_hints=context_hints)
        if not res.get("ok"):
            raise self._contract_error(res, "remember failed")
        return res

    def recall(self, query, limit=None, candidate_limit=None, context=None,
               project_id=None, vocabulary_id=None):
        mem = self._memory_for(project_id, vocabulary_id)
        kwargs = {}
        if limit is not None:
            kwargs["limit"] = limit
        if candidate_limit is not None:
            kwargs["candidate_limit"] = candidate_limit
        if context is not None:
            kwargs["context"] = context
        result = mem.recall(query=query, **kwargs)
        # For v2 contract, we return the result dict directly; ToolInterface will wrap
        if not result.get("ok"):
            raise self._contract_error(result, "recall failed")
        return result["result"]

    def get(self, node_id, project_id=None, vocabulary_id=None):
        mem = self._memory_for(project_id, vocabulary_id)
        node = mem.get_node(node_id)
        if node is None:
            from api.errors import NodeNotFoundError
            raise NodeNotFoundError(node_id)
        return node

    def provenance(self, node_id, project_id=None, vocabulary_id=None):
        mem = self._memory_for(project_id, vocabulary_id)
        node = mem.get_node(node_id)
        if node is None:
            from api.errors import NodeNotFoundError
            raise NodeNotFoundError(node_id)
        prov = node.get("provenance")
        if prov is None:
            # Fallback: construct minimal provenance from node
            prov = {"node_id": node_id, "source_id": node.get("source_id")}
        else:
            prov = dict(prov)
            prov["node_id"] = node_id
        return prov

    def inspect(self, project_id=None, vocabulary_id=None):
        mem = self._memory_for(project_id, vocabulary_id)
        # Return aggregate counts for this project
        from ai_engine.paths import get_knowledge_db, get_context_db, get_evidence_db, get_activity_db
        import sqlite3
        pid = project_id or DEFAULT_PROJECT_ID
        data_root = self.data_root
        # Knowledge counts
        kdb = get_knowledge_db(pid, data_root)
        try:
            repo_cnt = mem.count_nodes()
        except Exception:
            repo_cnt = 0
        # Context, evidence, activity counts (best effort)
        ctx_cnt = 0
        ev_cnt = 0
        act_cnt = 0
        try:
            cdb = get_context_db(pid, data_root)
            con = sqlite3.connect(f"file:{cdb}?mode=ro", uri=True)
            ctx_cnt = con.execute("SELECT COUNT(*) FROM context_snapshots").fetchone()[0]
            con.close()
        except Exception:
            pass
        try:
            edb = get_evidence_db(pid, data_root)
            con = sqlite3.connect(f"file:{edb}?mode=ro", uri=True)
            ev_cnt = con.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
            con.close()
        except Exception:
            pass
        try:
            adb = get_activity_db(pid, data_root)
            con = sqlite3.connect(f"file:{adb}?mode=ro", uri=True)
            act_cnt = con.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
            con.close()
        except Exception:
            pass
        return {
            "project_id": pid,
            "node_count": repo_cnt,
            "context_count": ctx_cnt,
            "evidence_count": ev_cnt,
            "activity_count": act_cnt,
        }

    def context_get(self, context_id, project_id=None):
        pid = project_id or DEFAULT_PROJECT_ID
        from ai_engine.paths import get_context_db
        from intelligence.context.store import ContextStore
        store = ContextStore(db_path=get_context_db(pid, self.data_root))
        snap = store.get(context_id)
        store.close()
        if snap is None:
            from api.errors import NodeNotFoundError
            raise NodeNotFoundError(context_id)
        return snap.as_dict()

    # ------------------------------------------------------------------
    # Phase 26: canonical lifecycle operations (additive)
    # ------------------------------------------------------------------

    def _lifecycle_for(self, project_id=None):
        from ai_engine.lifecycle_service import LifecycleService
        pid = project_id or DEFAULT_PROJECT_ID
        try:
            return LifecycleService(project_id=pid, data_root=self.data_root)
        except ValueError as exc:
            from api.errors import KnowledgeArgumentError
            raise KnowledgeArgumentError(str(exc)) from exc

    @staticmethod
    def _lifecycle_error(exc):
        from api.errors import KnowledgeArgumentError
        from api.errors import NodeNotFoundError
        message = str(exc) if isinstance(exc, ValueError) else None
        if message and message.startswith("no such lifecycle record"):
            return NodeNotFoundError(message)
        return KnowledgeArgumentError(message or "lifecycle operation failed")

    def lifecycle_ingest(self, content, origin=None, source=None, uri=None,
                         role=None, actor=None, project_id=None,
                         context_hints=None):
        """Ingest information into the lifecycle (user-provided memory or
        external knowledge grounded to its source)."""
        svc = self._lifecycle_for(project_id)
        try:
            if origin == "external":
                result = svc.ingest_external_knowledge(
                    content, source=source or "external", uri=uri,
                    actor=actor, context_hints=context_hints)
            else:
                result = svc.ingest_user_fact(
                    content, source=source, actor=actor,
                    context_hints=context_hints)
            return result
        except ValueError as exc:
            raise self._lifecycle_error(exc)

    def lifecycle_experience(self, situation, attempt, result,
                             context_id=None, evidence_ids=None, task_id=None,
                             outcome_classification=None, outcome_id=None,
                             task_type=None, domain=None, strategy_id=None,
                             actor=None, source=None, project_id=None):
        """Record an interpreted experience for an executed attempt."""
        svc = self._lifecycle_for(project_id)
        try:
            return svc.record_experience(
                situation=situation, attempt=attempt, result=result,
                context_id=context_id, evidence_ids=evidence_ids,
                task_id=task_id,
                outcome_classification=outcome_classification,
                outcome_id=outcome_id, task_type=task_type, domain=domain,
                strategy_id=strategy_id, actor=actor, source=source,
                project_id=project_id)
        except ValueError as exc:
            raise self._lifecycle_error(exc)

    def lifecycle_learning(self, experience_ids, project_id=None):
        """Derive a learning record from experiences (deterministic)."""
        svc = self._lifecycle_for(project_id)
        try:
            return svc.derive_learning(experience_ids, project_id=project_id)
        except ValueError as exc:
            raise self._lifecycle_error(exc)

    def lifecycle_strategy(self, experience_ids, project_id=None,
                           min_samples=None):
        """Derive generalized strategies from experiences (Phase 25 engine)."""
        svc = self._lifecycle_for(project_id)
        try:
            return svc.derive_strategies(
                experience_ids, project_id=project_id, min_samples=min_samples)
        except ValueError as exc:
            raise self._lifecycle_error(exc)

    def lifecycle_trace(self, record_id, role=None, project_id=None,
                        max_depth=None):
        """Deterministic provenance trace from a lifecycle record."""
        svc = self._lifecycle_for(project_id)
        try:
            return svc.trace(record_id, role=role,
                             max_depth=max_depth or 16)
        except ValueError as exc:
            raise self._lifecycle_error(exc)

    def lifecycle_describe(self, record_id, role=None, project_id=None):
        """Describe one lifecycle record with full provenance."""
        svc = self._lifecycle_for(project_id)
        try:
            return svc.describe(record_id, role=role)
        except ValueError as exc:
            raise self._lifecycle_error(exc)

    def lifecycle_summary(self, project_id=None):
        """Aggregate lifecycle counts for a project."""
        svc = self._lifecycle_for(project_id)
        try:
            return svc.summary(project_id=project_id)
        except ValueError as exc:
            raise self._lifecycle_error(exc)
