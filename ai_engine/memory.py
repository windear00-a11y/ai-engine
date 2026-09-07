"""Memory facade — clean, generic, project-isolated (Phase 1).

Provides:
    Memory(project_id, data_root, vocabulary_id) -> remember() / recall()

- Vocabulary is injectable (diary_v1 by default).
- Activity is stored in <data_root>/<project>/activity.db (NOT engine_state.db).
- Knowledge is stored in <data_root>/<project>/knowledge.db (via KnowledgeRepository).
- Context is stored in <data_root>/<project>/context.db (via ContextStore).
- Evidence is stored in <data_root>/<project>/evidence.db (via EvidenceStore).

No LLM, no network, no trust bypass, stdlib-only, deterministic ids.
API v1 (search/get/…) remains untouched; this is additive.

The facade never imports tools/permissions — it validates and persists
through the same deterministic paths the ingestion pipeline uses.
"""

import hashlib
import json
import os
import time

from ai_engine.paths import (
    DEFAULT_PROJECT_ID,
    ensure_default_project,
    get_activity_db,
    get_context_db,
    get_evidence_db,
    get_knowledge_db,
    _validate_project_id,
)
from ai_engine.vocabulary import Vocabulary
from ai_engine.ranker import KeywordRanker, Ranker

# Generic capture pipeline (Phase 3) — Memory delegates to it for manual capture
from ai_engine.capture import ManualCaptureAdapter, run_capture

import hashlib
import json


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


class Memory:
    """Generic memory facade.

    Example:
        mem = Memory(project_id="default", data_root="/tmp/data", vocabulary_id="diary_v1")
        res = mem.remember(text="evening walk helps sleep", type="fact")
        out = mem.recall(query="sleep routine")
    """

    def __init__(self, project_id=DEFAULT_PROJECT_ID, data_root=None, vocabulary_id="diary_v1", vocabulary=None, ranker=None, registry=None):
        pid = _validate_project_id(project_id)
        self.project_id = pid
        self.data_root = data_root  # may be None -> paths resolves via env
        # Ensure project registry/dirs exist (idempotent)
        ensure_default_project(data_root)
        # If project is not default and not yet exists, create entry
        from ai_engine.paths import get_project_entry, create_project
        if pid != DEFAULT_PROJECT_ID and get_project_entry(pid, data_root) is None:
            try:
                create_project(pid, data_root=data_root, vocabulary_id=vocabulary_id or "diary_v1")
            except ValueError:
                pass  # collision means already exists (race)

        # Vocabulary (injectable, generic)
        if vocabulary is not None:
            if not isinstance(vocabulary, Vocabulary):
                raise ValueError("vocabulary must be Vocabulary instance")
            self.vocabulary = vocabulary
        elif vocabulary_id is not None:
            self.vocabulary = Vocabulary.load(vocabulary_id)
        else:
            self.vocabulary = None

        # Optional registry — if provided, may supply ranker/capture adapters
        self.registry = registry
        # Ranker (injectable, deterministic default, registry-aware)
        if ranker is not None:
            if not isinstance(ranker, Ranker):
                raise ValueError("ranker must be Ranker instance")
            self.ranker = ranker
        elif registry is not None:
            # Try to get ranker from registry (e.g. "keyword")
            try:
                reg_ranker = registry.get_ranker("keyword")
                if reg_ranker is not None:
                    self.ranker = reg_ranker
                else:
                    self.ranker = KeywordRanker()
            except Exception:
                self.ranker = KeywordRanker()
        else:
            self.ranker = KeywordRanker()

        # Per-project stores (paths resolved lazily via helpers)
        self._knowledge_db = get_knowledge_db(pid, data_root)
        self._context_db = get_context_db(pid, data_root)
        self._evidence_db = get_evidence_db(pid, data_root)
        # Activity is separate file (NOT engine_state.db)
        self._activity_db = get_activity_db(pid, data_root)

    # ------------------------------------------------------------------
    # remember — via generic capture pipeline (Phase 3, domain-neutral)
    # ------------------------------------------------------------------
    def remember(self, text=None, payload=None, type=None, node_type=None,
                 name=None, description=None, relationships=None,
                 context_hints=None, source="manual", activity_type="manual",
                 node_id=None, adapter=None, registry=None):
        """Remember a fact/observation via generic capture pipeline.

        Delegates to ai_engine.capture.run_capture for normalization,
        vocabulary validation, provenance, deterministic dedup, and persistence
        (reuses ingestion.validator + KnowledgeRepository, no duplication).
        """
        # Build raw for capture pipeline — preserve backward compat with Memory args
        if payload is not None and isinstance(payload, dict):
            raw = dict(payload)
            # Merge explicit args into raw if not already present
            if text is not None and "text" not in raw:
                raw["text"] = text
            if (type or node_type) is not None and "type" not in raw:
                raw["type"] = type or node_type
            if name is not None and "name" not in raw:
                raw["name"] = name
            if description is not None and "description" not in raw:
                raw["description"] = description
            if relationships is not None and "relationships" not in raw:
                raw["relationships"] = relationships
            if node_id is not None and "id" not in raw:
                raw["id"] = node_id
        elif payload is not None and isinstance(payload, str):
            raw = payload
            # If explicit type/name etc also given, merge into dict
            if any(x is not None for x in [type, node_type, name, description, relationships, node_id]):
                raw = {"text": payload}
                if type or node_type:
                    raw["type"] = type or node_type
                if name is not None:
                    raw["name"] = name
                if description is not None:
                    raw["description"] = description
                if relationships is not None:
                    raw["relationships"] = relationships
                if node_id is not None:
                    raw["id"] = node_id
        else:
            # payload is None -> use text + explicit fields
            if text is None:
                return {"ok": False, "code": "invalid_argument", "error": "text or payload is required"}
            raw = {"text": text}
            if type or node_type:
                raw["type"] = type or node_type
            if name is not None:
                raw["name"] = name
            if description is not None:
                raw["description"] = description
            if relationships is not None:
                raw["relationships"] = relationships
            if node_id is not None:
                raw["id"] = node_id

        # Delegate to generic pipeline (domain-neutral, per-project, vocabulary-injected, registry-aware)
        from ai_engine.capture import run_capture
        # Resolve adapter: explicit -> memory's registry -> this instance's registry
        reg = registry or getattr(self, "registry", None)
        # If adapter is a string id, let run_capture resolve via registry
        return run_capture(
            raw=raw,
            project_id=self.project_id,
            data_root=self.data_root,
            vocabulary=self.vocabulary,
            vocabulary_id=self.vocabulary.id if self.vocabulary else None,
            adapter=adapter,
            activity_type=activity_type,
            source=source,
            context_hints=context_hints,
            node_id=node_id,
            registry=reg,
        )

    # ------------------------------------------------------------------
    # recall — federated, deterministic, bounded, provenance-preserving (Phase 16)
    # ------------------------------------------------------------------
    def recall(self, query, limit=20, context=None, candidate_limit=50):
        """Federated recall: Knowledge + Experience + Strategy + Evidence + Context.

        Federates existing deterministic retrieval across intelligence layers,
        bounded and provenance-preserving. Caller remains database-agnostic.

        Result (additive, backward compatible with Phase 2):
            {
                query, query_terms,
                knowledge: [...],        # ranked, sliced to limit
                experience: [...],       # ranked, sliced to min(5, limit)
                strategies: [...],       # learned guidance, sliced to min(5, limit)
                evidence_chain: [...],   # supporting evidence for top results
                context: {...}|None,     # supplied context or None
                ambiguous: bool,
                filtered: [...],         # lifecycle/context filtered out
                total_candidates: int,   # sum across categories
                # Legacy Phase 2 fields for compat:
                evidence_chain_id, count, candidate_count
            }

        Bounded, deterministic, local, synchronous, no network/LLM.
        """
        if not isinstance(query, str) or not query.strip():
            return {"ok": False, "code": "invalid_argument", "error": "query must be non-empty string"}
        if not isinstance(limit, int) or limit <= 0 or limit > 100:
            return {"ok": False, "code": "invalid_argument", "error": "limit must be integer 1..100"}
        if not isinstance(candidate_limit, int) or candidate_limit <= 0 or candidate_limit > 500:
            return {"ok": False, "code": "invalid_argument", "error": "candidate_limit must be integer 1..500"}

        query_terms = [w for w in query.lower().split() if w]
        query_lower = query.lower()

        # -- helper: keyword score for generic dicts --
        def _kw_score(text, terms):
            tl = text.lower()
            return sum(1 for w in terms if w in tl)

        # -- 1. Knowledge (existing Ranker path, bounded, deterministic) --
        knowledge = []
        filtered = []
        candidate_count = 0
        total_candidates = 0
        try:
            from retrieval.repository import KnowledgeRepository
            repo = KnowledgeRepository(self._knowledge_db)
            repo.initialize()
            rankings = repo.search_rankings(query)
            candidate_count = len(rankings)
            total_candidates += candidate_count
            candidate_ids = [nid for _, nid, _ in rankings[:candidate_limit]]
            if candidate_ids:
                candidates = repo.hydrate_nodes(candidate_ids, with_relationships=True)
                # Apply Ranker (deterministic, context-aware, tie-break by id)
                ranked = self.ranker.rank(query_terms, candidates, context)
                # Deduplicate by stable id (already unique via search_rankings)
                seen_k = set()
                deduped_k = []
                for n in ranked:
                    nid = n.get("id")
                    if nid not in seen_k:
                        seen_k.add(nid)
                        deduped_k.append(n)
                knowledge = deduped_k[:limit]
                # Filtered: those with lifecycle filtered out (via ranker quality 0)
                for n in ranked:
                    if n.get("_score", {}).get("lifecycle") == 0.0 or n.get("_score", {}).get("quality") == 0:
                        filtered.append({"id": n.get("id"), "reason": "lifecycle/context filtered"})
            repo.close()
        except Exception as e:
            return {"ok": False, "code": "internal_error", "error": f"recall knowledge failed: {e}"}

        # -- 2. Experience (bounded, deterministic, synthetic protected) --
        experience = []
        try:
            from intelligence.experience.store import ExperienceStore
            from ai_engine.paths import get_experience_db
            estore = ExperienceStore(
                db_path=get_experience_db(self.project_id, self.data_root))
            # Retrieve all, then score
            all_exps = estore.all()
            # Filter synthetic (Phase 13 protection)
            filtered_exps = []
            for exp in all_exps:
                summary = getattr(exp, "summary", None) or (exp.get("summary") if isinstance(exp, dict) else {})
                if isinstance(summary, dict) and summary.get("synthetic"):
                    filtered.append({"id": getattr(exp, "experience_id", None) or exp.get("experience_id"), "reason": "synthetic filtered"})
                    continue
                filtered_exps.append(exp)
            # Score experiences by keyword match on task_type/domain/summary
            scored_exps = []
            for exp in filtered_exps:
                # Build text for scoring
                if isinstance(exp, dict):
                    txt = " ".join(str(v) for v in [exp.get("task_type", ""), exp.get("domain", ""), json.dumps(exp.get("summary", {}), ensure_ascii=False)])
                    exp_id = exp.get("experience_id")
                    ctx = exp.get("context_id", "")
                else:
                    txt = " ".join(str(v) for v in [getattr(exp, "task_type", ""), getattr(exp, "domain", ""), json.dumps(getattr(exp, "summary", {}), ensure_ascii=False)])
                    exp_id = getattr(exp, "experience_id", "")
                    ctx = getattr(exp, "context_id", "")
                score = _kw_score(txt, query_terms)
                if score == 0:
                    continue
                # Context-aware boost: if context provided and matches exp's context, boost
                ctx_match = 1.0
                if isinstance(context, dict) and context.get("context_id"):
                    ctx_match = 1.0 if ctx == context.get("context_id") else 0.5
                final = score * ctx_match
                scored_exps.append((final, exp_id, exp, ctx_match))
            # Deterministic ordering: -final, exp_id ASC
            scored_exps.sort(key=lambda x: (-x[0], x[1]))
            # Slice to min(5, limit)
            exp_limit = min(5, limit)
            for final, exp_id, exp, ctx_m in scored_exps[:exp_limit]:
                # Annotate with score for provenance
                exp_dict = exp.as_dict() if hasattr(exp, "as_dict") else dict(exp)
                exp_dict["_score"] = {"final_score": round(final, 6), "raw_score": final / (ctx_m if ctx_m else 1), "context_match": ctx_m}
                experience.append(exp_dict)
                total_candidates += 1
            estore.close()
        except Exception:
            # Experience retrieval is best-effort; don't fail whole recall
            try:
                estore.close()
            except Exception:
                pass
            experience = []

        # -- 3. Strategies (learned guidance, bounded, provenance) --
        strategies = []
        try:
            from intelligence.strategy.store import StrategyStore
            sstore = StrategyStore(db_path=self._evidence_db)  # strategies live in evidence.db
            # Fallback per-project if needed (check existence, not count which may not exist)
            try:
                has_count = hasattr(sstore, "count")
                needs_fallback = not os.path.exists(sstore.db_path) or (has_count and sstore.count() == 0)
            except Exception:
                needs_fallback = not os.path.exists(sstore.db_path)
            if needs_fallback:
                from ai_engine.paths import get_evidence_db
                try:
                    sstore.close()
                except Exception:
                    pass
                sstore = StrategyStore(db_path=get_evidence_db(self.project_id, self.data_root))
            all_strats = sstore.all() if hasattr(sstore, "all") else []
            scored_strats = []
            for strat in all_strats:
                # Strat may be object or dict
                if isinstance(strat, dict):
                    txt = " ".join(str(v) for v in [strat.get("name", ""), strat.get("description", ""), strat.get("problem_class", "")])
                    sid = strat.get("strategy_id", "")
                else:
                    txt = " ".join(str(v) for v in [getattr(strat, "name", ""), getattr(strat, "description", ""), getattr(strat, "problem_class", "")])
                    sid = getattr(strat, "strategy_id", "")
                score = _kw_score(txt, query_terms)
                if score == 0:
                    continue
                # Synthetic protection: strategies themselves are not synthetic, but check if needed
                scored_strats.append((score, sid, strat))
            scored_strats.sort(key=lambda x: (-x[0], x[1]))
            strat_limit = min(5, limit)
            for score, sid, strat in scored_strats[:strat_limit]:
                # Deduplicate by strategy_id
                if any(s.get("strategy_id") == sid for s in strategies):
                    continue
                strat_dict = strat.to_dict() if hasattr(strat, "to_dict") else dict(strat)
                strat_dict["_score"] = {"final_score": score, "raw_score": score}
                # Provenance: keep supporting experience/evidence if available in strat (may be via learning)
                strategies.append(strat_dict)
                total_candidates += 1
            sstore.close()
        except Exception:
            try:
                sstore.close()
            except Exception:
                pass
            strategies = []

        # -- 4. Evidence chain (supporting evidence for top results) --
        evidence_chain = []
        try:
            from intelligence.evidence.store import EvidenceStore
            estore = EvidenceStore(db_path=self._evidence_db)
            # Collect evidence ids from top knowledge/experience
            ev_ids = set()
            for n in knowledge[:3]:
                # Knowledge nodes don't directly have evidence_ids, but via experience
                pass
            for exp in experience[:3]:
                for eid in exp.get("evidence_ids", [])[:2]:
                    ev_ids.add(eid)
            # Fetch evidence
            for eid in list(ev_ids)[:limit]:
                ev = estore.get(eid)
                if ev:
                    ev_dict = ev.as_dict() if hasattr(ev, "as_dict") else dict(ev)
                    # Synthetic protection: keep marker
                    evidence_chain.append(ev_dict)
            estore.close()
        except Exception:
            try:
                estore.close()
            except Exception:
                pass
            evidence_chain = []

        # -- 5. Context (current) --
        ctx_obj = None
        if isinstance(context, dict) and context.get("context_id"):
            ctx_obj = context
        elif isinstance(context, str):
            ctx_obj = {"context_id": context}

        # -- 6. Ambiguous and conflict surfacing --
        ambiguous = False
        if len(knowledge) >= 2:
            s1 = knowledge[0].get("_score", {}).get("final_score")
            s2 = knowledge[1].get("_score", {}).get("final_score")
            if s1 is not None and s2 is not None and s1 == s2:
                ambiguous = True
        # Conflicting information already surfaced via _has_conflicting_knowledge in planner, but for recall we surface via filtered

        # -- 7. Deduplication already done per category by stable id --

        # -- 8. Provenance already preserved per item (provenance field, evidence_ids, strategy_id) --

        # Evidence chain id for backward compat ( Phase 2 field)
        evidence_chain_id = "evc_" + hashlib.sha256(_canonical({"query_terms": query_terms, "top_ids": [n.get("id") for n in knowledge[:5]]}).encode("utf-8")).hexdigest()[:16] if knowledge else None

        # Bounded total already via per-category limits

        return {
            "ok": True,
            "result": {
                "query": query,
                "query_terms": query_terms,
                "knowledge": knowledge,
                "experience": experience,
                "strategies": strategies,
                "evidence_chain": evidence_chain,
                "context": ctx_obj,
                "ambiguous": ambiguous,
                "filtered": filtered,
                "total_candidates": total_candidates,
                # Legacy Phase 2 fields for backward compat
                "evidence_chain_id": evidence_chain_id,
                "count": len(knowledge),
                "candidate_count": candidate_count,
            },
        }

    # Convenience wrappers for tests
    def get_node(self, node_id):
        from retrieval.repository import KnowledgeRepository
        repo = KnowledgeRepository(self._knowledge_db)
        repo.initialize()
        node = repo.get_node(node_id)
        repo.close()
        return node

    def count_nodes(self):
        from retrieval.repository import KnowledgeRepository
        repo = KnowledgeRepository(self._knowledge_db)
        repo.initialize()
        cnt = repo.count_nodes()
        repo.close()
        return cnt

    def list_nodes(self):
        from retrieval.repository import KnowledgeRepository
        repo = KnowledgeRepository(self._knowledge_db)
        repo.initialize()
        nodes = repo.get_all_nodes()
        repo.close()
        return nodes
