"""Intelligence API handler (read-only) (Phase 10)."""

from .contract import INTELLIGENCE_CONTRACT_VERSION, validate_request
from .types import IntelligenceAPIError


class IntelligenceAPI:
    """Read-only intelligence inspection API."""

    def __init__(self, experience_store=None, decision_store=None,
                 learning_store=None, context_store=None,
                 strategy_store=None, knowledge_repository=None):
        # Stores are injected for :memory: testing; otherwise defaults are lazy
        self._experience_store = experience_store
        self._decision_store = decision_store
        self._learning_store = learning_store
        self._context_store = context_store
        self._strategy_store = strategy_store
        self._knowledge_repository = knowledge_repository

    def _exp_store(self):
        if self._experience_store is not None:
            return self._experience_store, False
        from intelligence.experience.store import ExperienceStore
        return ExperienceStore(check_same_thread=False), True

    def _dec_store(self):
        if self._decision_store is not None:
            return self._decision_store, False
        from intelligence.decision.store import DecisionStore
        return DecisionStore(check_same_thread=False), True

    def _learn_store(self):
        if self._learning_store is not None:
            return self._learning_store, False
        from intelligence.learning.store import LearningStore
        return LearningStore(check_same_thread=False), True

    def _ctx_store(self):
        if self._context_store is not None:
            return self._context_store, False
        from intelligence.context.store import ContextStore
        return ContextStore(check_same_thread=False), True

    def _knowledge_repo(self):
        if self._knowledge_repository is not None:
            return self._knowledge_repository, False
        from retrieval.repository import KnowledgeRepository, DEFAULT_KNOWLEDGE_DB
        return KnowledgeRepository(db_path=DEFAULT_KNOWLEDGE_DB, check_same_thread=False), True

    # -- handlers ---------------------------------------------------------

    def experience_search(self, task_type=None, domain=None, context_id=None, limit=20):
        store, close = self._exp_store()
        try:
            # Retrieve by task_type if given, else all
            if task_type:
                exps = store.for_task_type(task_type, context_id=context_id) if context_id else store.for_task_type(task_type)
                # Further filter by domain if needed
                if domain:
                    exps = [e for e in exps if getattr(e, "domain", None) == domain]
            elif domain or context_id:
                exps = store.all()
                if domain:
                    exps = [e for e in exps if getattr(e, "domain", None) == domain]
                if context_id:
                    exps = [e for e in exps if getattr(e, "context_id", None) == context_id]
            else:
                exps = store.all()
            limit = min(int(limit), 100) if limit else 20
            exps = exps[:limit]
            return [e.as_dict() if hasattr(e, "as_dict") else dict(e) for e in exps]
        finally:
            if close:
                store.close()

    def experience_get(self, experience_id):
        store, close = self._exp_store()
        try:
            rec = store.get(experience_id)
            if rec is None:
                raise IntelligenceAPIError("not_found", f"experience {experience_id!r} not found")
            return rec.as_dict() if hasattr(rec, "as_dict") else dict(rec)
        finally:
            if close:
                store.close()

    def decision_get(self, decision_id):
        store, close = self._dec_store()
        try:
            row = store.get(decision_id)
            if row is None:
                raise IntelligenceAPIError("not_found", f"decision {decision_id!r} not found")
            return row
        finally:
            if close:
                store.close()

    def decision_audit(self, limit=20):
        store, close = self._dec_store()
        try:
            rows = store.all(limit=limit)
            return rows
        finally:
            if close:
                store.close()

    def learning_events(self, limit=20):
        store, close = self._learn_store()
        try:
            rows = store.all()
            return rows[:limit]
        finally:
            if close:
                store.close()

    def knowledge_confidence(self, node_id):
        repo, close = self._knowledge_repo()
        try:
            try:
                node = repo.get_node(node_id)
            except Exception as e:
                raise IntelligenceAPIError("not_found", f"node {node_id!r} not found") from e
            if node is None:
                raise IntelligenceAPIError("not_found", f"node {node_id!r} not found")
            lifecycle = node.get("lifecycle") or {}
            conf = lifecycle.get("confidence", 1.0)
            return {"node_id": node_id, "confidence": conf}
        finally:
            if close:
                try:
                    repo.close()
                except Exception:
                    pass

    def knowledge_lifecycle(self, node_id):
        repo, close = self._knowledge_repo()
        try:
            try:
                node = repo.get_node(node_id)
            except Exception as e:
                raise IntelligenceAPIError("not_found", f"node {node_id!r} not found") from e
            if node is None:
                raise IntelligenceAPIError("not_found", f"node {node_id!r} not found")
            lifecycle = node.get("lifecycle") or {}
            return {"node_id": node_id, "lifecycle": lifecycle}
        finally:
            if close:
                try:
                    repo.close()
                except Exception:
                    pass

    def context_get(self, context_id):
        store, close = self._ctx_store()
        try:
            snap = store.get(context_id)
            if snap is None:
                raise IntelligenceAPIError("not_found", f"context {context_id!r} not found")
            return snap.as_dict() if hasattr(snap, "as_dict") else dict(snap)
        finally:
            if close:
                store.close()

    def context_similar(self, context_id, limit=5):
        # Find similar contexts by simple id prefix or task similarity
        c_store, close_c = self._ctx_store()
        try:
            target = c_store.get(context_id)
            if target is None:
                raise IntelligenceAPIError("not_found", f"context {context_id!r} not found")
            all_snaps = c_store.all()
            # Score by task type/domain match
            def score(snap):
                if snap.context_id == context_id:
                    return -1
                t1 = target.task or {}
                t2 = snap.task or {}
                if t1.get("type") == t2.get("type") and t1.get("domain") == t2.get("domain"):
                    return 2
                if t1.get("type") == t2.get("type"):
                    return 1
                return 0
            ranked = sorted(all_snaps, key=lambda s: (-score(s), s.context_id))
            # Filter out self and take limit
            result = [s for s in ranked if s.context_id != context_id][:limit]
            return [s.as_dict() for s in result]
        finally:
            if close_c:
                c_store.close()


class IntelligenceToolInterface:
    """Transport-independent handler with Contract envelope."""

    def __init__(self, api=None, **kwargs):
        self.api = api or IntelligenceAPI(**kwargs)

    def execute(self, request):
        try:
            operation, args = validate_request(request)
        except IntelligenceAPIError as e:
            return {
                "ok": False,
                "operation": request.get("operation") if isinstance(request, dict) else None,
                "contract_version": INTELLIGENCE_CONTRACT_VERSION,
                "error": {"code": e.code, "message": e.message},
            }
        except Exception as e:
            return {
                "ok": False,
                "operation": request.get("operation") if isinstance(request, dict) else None,
                "contract_version": INTELLIGENCE_CONTRACT_VERSION,
                "error": {"code": "invalid_request", "message": str(e)},
            }
        try:
            result = self._dispatch(operation, args)
            return {
                "ok": True,
                "operation": operation,
                "contract_version": INTELLIGENCE_CONTRACT_VERSION,
                "result": result,
            }
        except IntelligenceAPIError as e:
            code = e.code
            # Map not_found to 404-like, invalid to 400
            return {
                "ok": False,
                "operation": operation,
                "contract_version": INTELLIGENCE_CONTRACT_VERSION,
                "error": {"code": code, "message": e.message},
            }
        except Exception as e:
            return {
                "ok": False,
                "operation": operation,
                "contract_version": INTELLIGENCE_CONTRACT_VERSION,
                "error": {"code": "internal_error", "message": str(e)},
            }

    def _dispatch(self, operation, args):
        if operation == "experience.search":
            return self.api.experience_search(**args)
        if operation == "experience.get":
            return self.api.experience_get(**args)
        if operation == "decision.get":
            return self.api.decision_get(**args)
        if operation == "decision.audit":
            return self.api.decision_audit(**args)
        if operation == "learning.events":
            return self.api.learning_events(**args)
        if operation == "knowledge.confidence":
            return self.api.knowledge_confidence(**args)
        if operation == "knowledge.lifecycle":
            return self.api.knowledge_lifecycle(**args)
        if operation == "context.get":
            return self.api.context_get(**args)
        if operation == "context.similar":
            return self.api.context_similar(**args)
        raise IntelligenceAPIError("unknown_operation", f"unknown {operation!r}")
