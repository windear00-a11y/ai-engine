"""Lifecycle service facade — canonical information/knowledge/experience
lifecycle operations for the Persistent Intelligence engine (Phase 26).

This facade sits ABOVE every store. It has no database access of its own, never
mentions table names, and never imports any low-level repository class: everything
goes through the existing intelligence stores and repositories plus the new
``intelligence.lifecycle`` package. The storage boundary is therefore real:
core intelligence logic depends on roles/origins/states and provenance, not on
database layout.

Canonical lifecycle (single, deterministic)::

    INFORMATION -> SOURCE/ORIGIN -> SOURCE RECORD -> STRUCTURED MEMORY
        -> KNOWLEDGE and/or EXPERIENCE -> LEARNING -> STRATEGY
        -> REASONING/DECISION -> ACTION -> OUTCOME -> EVIDENCE
        -> EXPERIENCE (loop)

Trust boundary (Rule 1): EXTERNAL knowledge is advisory and grounded to its
source; it is recorded as knowledge and never becomes an experience/strategy
directly (enforced by ``check_role_origin``).
"""

import hashlib
import json
import time

from ai_engine.paths import (
    DEFAULT_PROJECT_ID,
    get_context_db,
    get_evidence_db,
    get_experience_db,
    _validate_project_id,
)
from intelligence.context.schema import ContextSnapshot, derive_context_id
from intelligence.context.store import ContextStore
from intelligence.evidence.schema import (
    EvidenceRecord,
    derive_evidence_id,
)
from intelligence.evidence.types import EvidenceType
from intelligence.experience.schema import (
    ExperienceRecord,
    derive_experience_id,
)
from intelligence.experience.store import ExperienceStore
from intelligence.lifecycle.model import (
    LifecycleRecord,
    LifecycleState,
    Origin,
    Provenance,
    RecordRole,
    check_role_origin,
    classify_origin,
    confidence_label,
    derive_learning_record_id,
)
from intelligence.lifecycle.store import LifecycleRecordStore
from intelligence.lifecycle.trace import normalize_entry, trace_provenance
from intelligence.outcome.schema import Outcome, derive_outcome_id
from intelligence.outcome.store import OutcomeStore
from intelligence.outcome.types import OutcomeClassification, classify_outcome
from intelligence.strategy.schema import Strategy
from intelligence.strategy.store import StrategyStore


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


def _now():
    return time.time()


def _prefix_role(record_id):
    """Guess a role from a deterministic record id prefix (best effort)."""
    rid = str(record_id or "")
    if rid.startswith("xp_"):
        return "experience"
    if rid.startswith("oc_"):
        return "outcome"
    if rid.startswith("ev_"):
        return "evidence"
    if rid.startswith("st_"):
        return "strategy"
    if rid.startswith("ctx_") or rid.startswith("context_"):
        return "context"
    if rid.startswith("lrn_"):
        return "learning"
    return None


class LifecycleService:
    """Deterministic lifecycle operations over the intelligence stores.

    Usage:
        svc = LifecycleService(project_id="default", data_root="/tmp/data")
        svc.ingest_user_fact(text="...")
        svc.derive_strategies(experience_ids=[...])
    """

    def __init__(self, project_id=DEFAULT_PROJECT_ID, data_root=None,
                 vocabulary_id="diary_v1"):
        _validate_project_id(project_id)
        self.project_id = project_id
        self.data_root = data_root
        self.vocabulary_id = vocabulary_id
        self._ensure_project()

    def _ensure_project(self):
        """Create the per-project data directory so stores can open."""
        from ai_engine.paths import (
            create_project,
            ensure_default_project,
            get_project_entry,
        )
        ensure_default_project(self.data_root)
        if self.project_id != DEFAULT_PROJECT_ID:
            if get_project_entry(self.project_id, self.data_root) is None:
                try:
                    create_project(
                        self.project_id, data_root=self.data_root,
                        vocabulary_id=self.vocabulary_id or "diary_v1")
                except ValueError:
                    pass  # already exists (race)

    # ------------------------------------------------------------------
    # Store wiring (per-op, following the existing per-project convention)
    # ------------------------------------------------------------------

    def _memory(self):
        from ai_engine.memory import Memory
        return Memory(project_id=self.project_id, data_root=self.data_root,
                      vocabulary_id=self.vocabulary_id)

    def _lifecycle_store(self):
        return LifecycleRecordStore(
            db_path=get_evidence_db(self.project_id, self.data_root))

    def _experience_store(self):
        return ExperienceStore(
            db_path=get_experience_db(self.project_id, self.data_root))

    def _outcome_store(self):
        return OutcomeStore(db_path=get_evidence_db(self.project_id,
                                                    self.data_root))

    def _strategy_store(self):
        return StrategyStore(db_path=get_evidence_db(self.project_id,
                                                     self.data_root))

    def _context_store(self):
        return ContextStore(db_path=get_context_db(self.project_id,
                                                   self.data_root))

    def _evidence_store(self):
        from intelligence.evidence.store import EvidenceStore
        return EvidenceStore(db_path=get_evidence_db(self.project_id,
                                                     self.data_root))

    # ------------------------------------------------------------------
    # Information -> SOURCE/ORIGIN -> SOURCE RECORD -> structured memory -> KNOWLEDGE
    # ------------------------------------------------------------------

    def ingest_user_fact(self, text, source=None, actor=None,
                         context_hints=None):
        """Capture a user-provided fact as structured memory (User->Memory).

        Full pipeline in one call: activity -> context -> knowledge node ->
        evidence, exactly like ``remember``, plus lifecycle provenance where
        the fact is recorded as role=MEMORY origin=USER_PROVIDED and its raw
        source capture is recorded as role=SOURCE_RECORD.
        """
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        mem = self._memory()
        res = mem.remember(
            text=text, source=source or "manual", activity_type="manual",
            context_hints=context_hints or {},
        )
        if not res.get("ok"):
            raise ValueError(res.get("error") or "user fact capture failed")
        node_id = (res.get("node_ids") or [None])[0]
        activity_id = res.get("activity_id")
        context_id = res.get("context_id")
        evidence_id = res.get("evidence_id")
        if not node_id:
            raise ValueError("user fact capture produced no node")

        origin = classify_origin(source=source or "manual",
                                 declared=Origin.USER_PROVIDED)
        actor_value = actor or _actor_from_hints(context_hints)
        prov = Provenance(
            record_id=node_id,
            origin=origin,
            source=source or "manual",
            actor=actor_value,
            timestamp=_now(),
            project=self.project_id,
            context_id=context_id,
            evidence_ids=(evidence_id,) if evidence_id else (),
            lifecycle_state=LifecycleState.ACTIVE,
            parent_record_ids=(activity_id,) if activity_id else (),
            created_at=_now(),
            updated_at=_now(),
        )
        record = LifecycleRecord(
            record_id=node_id,
            role=RecordRole.MEMORY,
            provenance=prov,
            subject=text,
            content={"text": text},
        )
        lstore = self._lifecycle_store()
        lstore.save(record)
        # SOURCE RECORD metadata for the raw capture (info -> source record).
        if activity_id:
            source_prov = Provenance(
                record_id=activity_id,
                origin=Origin.USER_PROVIDED,
                source=source or "manual",
                actor=actor_value,
                timestamp=_now(),
                project=self.project_id,
                context_id=context_id,
                evidence_ids=(evidence_id,) if evidence_id else (),
                lifecycle_state=LifecycleState.ACTIVE,
                parent_record_ids=(context_id,) if context_id else (),
                created_at=_now(),
                updated_at=_now(),
            )
            lstore.save(LifecycleRecord(
                record_id=activity_id,
                role=RecordRole.SOURCE_RECORD,
                provenance=source_prov,
                subject=text,
                content={"kind": "raw_capture", "node_id": node_id},
            ))
        lstore.close()
        return {
            "record_id": node_id,
            "lifecycle_record_id": node_id,
            "role": RecordRole.MEMORY.value,
            "origin": origin.value,
            "lifecycle_state": LifecycleState.ACTIVE.value,
            "activity_id": activity_id,
            "context_id": context_id,
            "evidence_id": evidence_id,
            "provenance": prov.as_dict(),
        }

    def ingest_external_knowledge(self, content, source="external", uri=None,
                                  actor=None, scope=None, context_hints=None):
        """Ingest external information as advisory KNOWLEDGE (import).

        External information is grounded to its source and recorded only as
        knowledge (origin=EXTERNAL). It never becomes an experience or a
        strategy here; deriving one from external claims requires going
        through an executed, observed action (or an explicit user step).
        """
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be a non-empty string")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source must be a non-empty string")
        mem = self._memory()
        hints = dict(context_hints or {})
        if uri:
            hints.setdefault("uri", uri)
        res = mem.remember(
            text=content, source=source, activity_type="import",
            context_hints=hints,
        )
        if not res.get("ok"):
            raise ValueError(res.get("error") or "external knowledge import failed")
        node_id = (res.get("node_ids") or [None])[0]
        activity_id = res.get("activity_id")
        context_id = res.get("context_id")
        evidence_id = res.get("evidence_id")
        if not node_id:
            raise ValueError("external knowledge import produced no node")

        origin = classify_origin(source=source, declared=Origin.EXTERNAL)
        # Rule 1 check is applied explicitly so it is impossible to route
        # external knowledge into an experience/strategy through this gate.
        ok, reason = check_role_origin(RecordRole.KNOWLEDGE, origin)
        if not ok:
            raise ValueError(reason)
        prov = Provenance(
            record_id=node_id,
            origin=Origin.EXTERNAL,
            source=uri or source,
            actor=actor,
            timestamp=_now(),
            project=self.project_id,
            context_id=context_id,
            evidence_ids=(evidence_id,) if evidence_id else (),
            lifecycle_state=LifecycleState.ACTIVE,
            parent_record_ids=(activity_id,) if activity_id else (),
            created_at=_now(),
            updated_at=_now(),
        )
        record = LifecycleRecord(
            record_id=node_id,
            role=RecordRole.KNOWLEDGE,
            provenance=prov,
            subject=content,
            content={
                "text": content,
                "external_source": source,
                "uri": uri,
                "scope": scope,
                "advisory": True,
                "grounded_to_source": uri or source,
            },
        )
        lstore = self._lifecycle_store()
        lstore.save(record)
        if activity_id:
            lstore.save(LifecycleRecord(
                record_id=activity_id,
                role=RecordRole.SOURCE_RECORD,
                provenance=Provenance(
                    record_id=activity_id,
                    origin=Origin.EXTERNAL,
                    source=uri or source,
                    actor=actor,
                    timestamp=_now(),
                    project=self.project_id,
                    context_id=context_id,
                    evidence_ids=(evidence_id,) if evidence_id else (),
                    parent_record_ids=(context_id,) if context_id else (),
                    created_at=_now(),
                    updated_at=_now(),
                ),
                subject=content,
                content={"kind": "external_import",
                         "external_source": source, "uri": uri},
            ))
        lstore.close()
        return {
            "record_id": node_id,
            "role": RecordRole.KNOWLEDGE.value,
            "origin": Origin.EXTERNAL.value,
            "lifecycle_state": LifecycleState.ACTIVE.value,
            "activity_id": activity_id,
            "context_id": context_id,
            "evidence_id": evidence_id,
            "source": uri or source,
            "note": (
                "external knowledge is advisory and grounded to its source; "
                "it is stored as knowledge and never becomes an experience or "
                "strategy directly"
            ),
            "provenance": prov.as_dict(),
        }

    # ------------------------------------------------------------------
    # ACTION -> OUTCOME -> EVIDENCE  (evidence + outcome records)
    # ------------------------------------------------------------------

    def record_evidence(self, source_observation_id, claim,
                        evidence_type="fact", supporting_data=None,
                        context_id=None, project_id=None):
        """Record one immutable verified observation (origin observed)."""
        if not source_observation_id or not claim:
            raise ValueError(
                "source_observation_id and claim are required for evidence")
        try:
            etype = EvidenceType(evidence_type)
        except ValueError:
            raise ValueError("invalid evidence_type %r" % (evidence_type,))
        evidence_id = derive_evidence_id(
            source_observation_id, claim, context_id, etype,
            supporting_data or {},
        )
        record = EvidenceRecord(
            evidence_id=evidence_id,
            source_observation_id=source_observation_id,
            claim=claim,
            context_id=context_id,
            evidence_type=etype,
            supporting_data=dict(supporting_data or {}),
            created_at_epoch=_now(),
        )
        estore = self._evidence_store()
        estore.save(record)
        estore.close()

        pid = project_id or self.project_id
        lstore = self._lifecycle_store()
        lstore.save(LifecycleRecord(
            record_id=evidence_id,
            role=RecordRole.EVIDENCE,
            provenance=Provenance(
                record_id=evidence_id,
                origin=Origin.OBSERVED,
                source=source_observation_id,
                timestamp=_now(),
                project=pid,
                context_id=context_id,
                lifecycle_state=LifecycleState.ACTIVE,
                parent_record_ids=(source_observation_id,) 
                if source_observation_id else (),
                created_at=_now(),
                updated_at=_now(),
            ),
            subject=claim,
            content={"source_observation_id": source_observation_id,
                     "evidence_type": etype.value,
                     "supporting_data": dict(supporting_data or {})},
        ))
        lstore.close()
        return {
            "evidence_id": evidence_id,
            "role": RecordRole.EVIDENCE.value,
            "origin": Origin.OBSERVED.value,
            "source_observation_id": source_observation_id,
            "lifecycle_state": LifecycleState.ACTIVE.value,
        }

    def record_outcome(self, classification, evidence_ids=(), plan_id=None,
                       context_id=None, project_id=None):
        """Record a verified execution outcome (origin observed)."""
        if isinstance(classification, str):
            classification = classify_outcome(classification)
        if classification is None:
            classification = OutcomeClassification.UNKNOWN
        if not evidence_ids:
            raise ValueError(
                "an outcome requires at least one verification evidence id")
        pid = project_id or self.project_id
        plan_id = plan_id or ("plan_lifecycle_" + hashlib.sha256(
            _canonical({"classification": classification.value,
                        "context_id": context_id}).encode("utf-8")
        ).hexdigest()[:16])
        outcome_id = derive_outcome_id(
            plan_id, context_id, classification,
            sorted(evidence_ids), {"source": "lifecycle_service"})
        ostore = self._outcome_store()
        ostore.save(Outcome(
            outcome_id=outcome_id,
            plan_id=plan_id,
            context_id=context_id,
            classification=classification,
            verification_evidence_ids=tuple(sorted(evidence_ids)),
            metadata={"source": "lifecycle_service"},
            created_at_epoch=_now(),
        ))
        ostore.close()
        lstore = self._lifecycle_store()
        lstore.save(LifecycleRecord(
            record_id=outcome_id,
            role=RecordRole.OUTCOME,
            provenance=Provenance(
                record_id=outcome_id,
                origin=Origin.OBSERVED,
                timestamp=_now(),
                project=pid,
                context_id=context_id,
                evidence_ids=tuple(sorted(evidence_ids)),
                lifecycle_state=LifecycleState.ACTIVE,
                parent_record_ids=(plan_id,),
                created_at=_now(),
                updated_at=_now(),
            ),
            subject="outcome %s" % classification.value,
            content={"classification": classification.value,
                     "plan_id": plan_id,
                     "verification_evidence_ids": sorted(evidence_ids)},
        ))
        lstore.close()
        return {
            "outcome_id": outcome_id,
            "role": RecordRole.OUTCOME.value,
            "origin": Origin.OBSERVED.value,
            "classification": classification.value,
            "evidence_ids": sorted(evidence_ids),
            "lifecycle_state": LifecycleState.ACTIVE.value,
        }

    # ------------------------------------------------------------------
    # EXPERIENCE (interpreted record of an executed action)
    # ------------------------------------------------------------------

    def record_experience(self, situation, attempt, result,
                          context_id=None, evidence_ids=None, task_id=None,
                          outcome_classification=None, outcome_id=None,
                          task_type=None, domain=None, strategy_id=None,
                          actor=None, source="observed", project_id=None):
        """Record a deterministic EXPERIENCE from an executed attempt.

        Origin is OBSERVED (an action happened). External origin is rejected
        here by the canonical role-origin rule.
        """
        if not situation or not attempt:
            raise ValueError("situation and attempt are required")
        pid = project_id or self.project_id
        evidence_ids = tuple(sorted(evidence_ids or ()))

        # Deterministic context: reuse the caller's context or derive a stable
        # lifecycle context from the project + source dimensions.
        if context_id is None:
            context_id = derive_context_id(
                environment={"system": "lifecycle"},
                project={"project_id": pid},
                source={"adapter": source or "observed"},
                actor={"user": actor} if actor else {},
            )
            cstore = self._context_store()
            snapshot = ContextSnapshot.build(
                environment={"system": "lifecycle"},
                project={"project_id": pid},
                source={"adapter": source or "observed"},
                actor={"user": actor} if actor else {},
                captured_at_epoch=_now(),
            )
            cstore.save(snapshot)
            cstore.close()

        # Resolve / create the outcome.
        if outcome_id is None:
            if outcome_classification is None:
                classification = classify_outcome(result)
            else:
                classification = classify_outcome(outcome_classification)
            if not evidence_ids:
                ev_src = "lifecycle_obs_" + hashlib.sha256(
                    _canonical({"situation": situation, "attempt": attempt,
                                "context_id": context_id}).encode("utf-8")
                ).hexdigest()[:24]
                ev_id = derive_evidence_id(
                    ev_src,
                    "outcome of attempt on %s" % situation,
                    context_id, EvidenceType.FACT,
                    {"classification": classification.value},
                )
                estore = self._evidence_store()
                estore.save(EvidenceRecord(
                    evidence_id=ev_id, source_observation_id=ev_src,
                    claim="outcome of attempt on %s" % situation,
                    context_id=context_id, evidence_type=EvidenceType.FACT,
                    supporting_data={"classification": classification.value},
                    created_at_epoch=_now(),
                ))
                estore.close()
                evidence_ids = (ev_id,)
            plan_id = task_id or ("plan_" + hashlib.sha256(
                _canonical({"situation": situation, "source": source,
                            "context_id": context_id}).encode("utf-8")
            ).hexdigest()[:16])
            outcome_id = derive_outcome_id(
                plan_id, context_id, classification,
                evidence_ids, {"source": "lifecycle_service"})
            ostore = self._outcome_store()
            ostore.save(Outcome(
                outcome_id=outcome_id, plan_id=plan_id,
                context_id=context_id, classification=classification,
                verification_evidence_ids=evidence_ids,
                metadata={"source": "lifecycle_service"},
                created_at_epoch=_now(),
            ))
            ostore.close()
        else:
            ostore = self._outcome_store()
            outcome = ostore.get(outcome_id)
            ostore.close()
            if outcome is None:
                raise ValueError("unknown outcome_id %r" % (outcome_id,))
            classification = outcome.classification
            if not evidence_ids:
                evidence_ids = tuple(sorted(
                    getattr(outcome, "verification_evidence_ids") or ()))

        task_id = task_id or ("task_" + hashlib.sha256(
            _canonical({"situation": situation, "context_id": context_id,
                        "source": source}).encode("utf-8")).hexdigest()[:16])
        experience_id = derive_experience_id(
            task_id, context_id, outcome_id, evidence_ids, strategy_id)
        summary = {
            "situation": {
                "problem": situation,
                "problem_pattern": _canonical_pattern(situation),
            },
            "attempt": {"approach": attempt,
                        "approach_pattern": _canonical_pattern(attempt)},
            "approach": attempt,
            "result": result,
            "outcome": classification.value,
            "source": source or "observed",
        }
        record = ExperienceRecord(
            experience_id=experience_id,
            task_id=task_id,
            task_type=task_type or "generic",
            domain=domain or "generic",
            context_id=context_id,
            outcome_id=outcome_id,
            evidence_ids=evidence_ids,
            strategy_id=strategy_id,
            summary=summary,
            synthesized_at_epoch=_now(),
        )
        xstore = self._experience_store()
        xstore.save(record)
        xstore.close()

        origin = classify_origin(source=source, declared=Origin.OBSERVED)
        ok, reason = check_role_origin(RecordRole.EXPERIENCE, origin)
        if not ok:
            raise ValueError(reason)
        lstore = self._lifecycle_store()
        lstore.save(LifecycleRecord(
            record_id=experience_id,
            role=RecordRole.EXPERIENCE,
            provenance=Provenance(
                record_id=experience_id,
                origin=origin,
                source=source or "observed",
                actor=actor,
                timestamp=_now(),
                project=pid,
                context_id=context_id,
                evidence_ids=evidence_ids,
                lifecycle_state=LifecycleState.ACTIVE,
                parent_record_ids=(outcome_id, task_id),
                derived_from=(outcome_id,),
                created_at=_now(),
                updated_at=_now(),
            ),
            subject="experience: %s (%s)" % (situation, classification.value),
            content={"outcome_id": outcome_id,
                     "outcome_classification": classification.value,
                     "evidence_ids": list(evidence_ids),
                     "context_id": context_id,
                     "task_id": task_id,
                     "summary": summary},
        ))
        lstore.close()
        return {
            "experience_id": experience_id,
            "role": RecordRole.EXPERIENCE.value,
            "origin": origin.value,
            "outcome_id": outcome_id,
            "context_id": context_id,
            "evidence_ids": list(evidence_ids),
            "outcome_classification": classification.value,
            "lifecycle_state": LifecycleState.ACTIVE.value,
        }

    # ------------------------------------------------------------------
    # LEARNING (derived from experiences, never erases its sources)
    # ------------------------------------------------------------------

    def derive_learning(self, experience_ids, project_id=None):
        """Derive a LEARNING record from source experiences (deterministic).

        The learning record REFERS to the experiences it was derived from and
        preserves failures. Source experiences are never erased or modified.
        """
        if not isinstance(experience_ids, (list, tuple)) or not experience_ids:
            raise ValueError("experience_ids must be a non-empty list")
        ids = sorted(str(x) for x in experience_ids)
        pid = project_id or self.project_id
        xstore = self._experience_store()
        experiences = [xstore.get(i) for i in ids]
        xstore.close()
        experiences = [e for e in experiences if e is not None]
        if not experiences:
            raise ValueError("no matching experiences found for %r" % (ids,))

        counts = {"success": 0, "failure": 0, "partial": 0, "blocked": 0,
                  "unknown": 0}
        for e in experiences:
            outcome = e.summary.get("outcome") if e.summary else None
            cls = "unknown"
            if isinstance(outcome, str):
                oc = classify_outcome(outcome)
                if oc.value == "unknown":
                    low = outcome.strip().lower()
                    if low in ("failure",):
                        cls = "failure"
                    elif low == "partial":
                        cls = "partial"
                    elif low == "blocked":
                        cls = "blocked"
                    else:
                        cls = oc.value
                else:
                    cls = oc.value
            counts[cls] = counts.get(cls, 0) + 1
        learning_id = derive_learning_record_id([e.experience_id
                                                 for e in experiences])
        content = {
            "experience_ids": [e.experience_id for e in experiences],
            "counts": counts,
            "preserves_source": True,
        }
        lstore = self._lifecycle_store()
        existing = lstore.get(learning_id)
        lstore.save(LifecycleRecord(
            record_id=learning_id,
            role=RecordRole.LEARNING,
            provenance=Provenance(
                record_id=learning_id,
                origin=Origin.DERIVED,
                timestamp=existing.provenance.timestamp if existing else _now(),
                project=pid,
                lifecycle_state=LifecycleState.ACTIVE,
                parent_record_ids=[e.experience_id for e in experiences],
                derived_from=[e.experience_id for e in experiences],
                created_at=existing.provenance.created_at if existing
                else _now(),
                updated_at=_now(),
            ),
            subject="learning from %d experience(s)" % len(experiences),
            content=content,
        ))
        lstore.close()
        return {
            "learning_id": learning_id,
            "role": RecordRole.LEARNING.value,
            "origin": Origin.DERIVED.value,
            "experience_ids": [e.experience_id for e in experiences],
            "counts": counts,
            "lifecycle_state": LifecycleState.ACTIVE.value,
            "preserves_source": True,
            "note": "learning references its sources and preserves failures; "
                    "source experiences are never erased",
        }

    def derive_strategies(self, experience_ids, project_id=None,
                          outcomes=None, context_snapshots=None,
                          min_samples=None):
        """Derive generalized strategies from experiences (Phase 25 engine).

        Deterministic generalization: identical experience sets yield
        identical strategy candidates. Candidates are persisted to the
        StrategyStore and recorded as role=STRATEGY origin=DERIVED with
        derived_from=learning and supporting experience/evidence ids.
        """
        if not isinstance(experience_ids, (list, tuple)) or not experience_ids:
            raise ValueError("experience_ids must be a non-empty list")
        ids = sorted(str(x) for x in experience_ids)
        pid = project_id or self.project_id
        xstore = self._experience_store()
        experiences = [xstore.get(i) for i in ids]
        xstore.close()
        experiences = [e for e in experiences if e is not None]
        if not experiences:
            raise ValueError("no matching experiences found for %r" % (ids,))

        from ai_engine.generic_learning import (
            generalize_strategies_from_experiences,
        )
        # The LEARNING record precedes the strategies: strategies are derived
        # FROM learning, keeping the full strategy -> learning -> experience
        # chain traceable.
        self.derive_learning([e.experience_id for e in experiences],
                             project_id=project_id)
        candidates = generalize_strategies_from_experiences(
            experiences, outcomes=outcomes,
            context_snapshots=context_snapshots, min_samples=min_samples)
        learning_id = derive_learning_record_id(
            [e.experience_id for e in experiences])

        lstore = self._lifecycle_store()
        sstore = self._strategy_store()
        for cand in candidates:
            strategy_id = cand["strategy_id"]
            existing = sstore.get(strategy_id)
            if existing is None:
                strat = Strategy(
                    strategy_id=strategy_id,
                    name=str(cand.get("approach", "generic"))[:50],
                    description=str(cand.get("recommendation", ""))[:200],
                    problem_class=str(cand.get("situation", "generic")),
                    tool_sequence=[cand.get("approach", "memory.recall")]
                    if isinstance(cand.get("approach"), str)
                    else cand.get("approach", []),
                    constraints={
                        "supporting_experience_ids":
                            cand.get("supporting_experience_ids", []),
                        "supporting_evidence_ids":
                            cand.get("supporting_evidence_ids", []),
                        "context_pattern": cand.get("context_pattern"),
                        "applicable_contexts":
                            cand.get("applicable_contexts", []),
                        "success_count": cand.get("success_count", 0),
                        "failure_count": cand.get("failure_count", 0),
                    },
                    confidence=float(cand.get("confidence", 0.5)),
                    context_restrictions={
                        "allowed_contexts": cand.get("applicable_contexts", []),
                    },
                    strategy_type="experience_derived",
                    created_at_epoch=_now(),
                    updated_at_epoch=_now(),
                )
                sstore.save(strat)
            lstore.save(LifecycleRecord(
                record_id=strategy_id,
                role=RecordRole.STRATEGY,
                provenance=Provenance(
                    record_id=strategy_id,
                    origin=Origin.DERIVED,
                    timestamp=_now(),
                    project=pid,
                    context_id=cand.get("context_pattern"),
                    evidence_ids=cand.get("supporting_evidence_ids", []),
                    confidence=float(cand.get("confidence", 0.5)),
                    lifecycle_state=LifecycleState.ACTIVE,
                    parent_record_ids=cand.get("supporting_experience_ids", []),
                    derived_from=(learning_id,),
                    created_at=_now(),
                    updated_at=_now(),
                ),
                subject=cand.get("recommendation", ""),
                content={
                    "situation_pattern": cand.get("situation_pattern"),
                    "approach_pattern": cand.get("approach_pattern"),
                    "supporting_experience_ids":
                        cand.get("supporting_experience_ids", []),
                    "supporting_evidence_ids":
                        cand.get("supporting_evidence_ids", []),
                    "applicable_contexts": cand.get("applicable_contexts", []),
                    "sample_count": cand.get("sample_count", 0),
                    "success_count": cand.get("success_count", 0),
                    "failure_count": cand.get("failure_count", 0),
                    "success_rate": cand.get("success_rate", 0.0),
                    "confidence": cand.get("confidence", 0.0),
                    "derived_from": (learning_id,),
                },
            ))
        sstore.close()

        # Deterministic de-dup of the saved lists.
        result = []
        seen = set()
        for cand in candidates:
            strategy_id = cand["strategy_id"]
            if strategy_id in seen:
                continue
            seen.add(strategy_id)
            result.append({
                "strategy_id": strategy_id,
                "situation_pattern": cand.get("situation_pattern"),
                "approach_pattern": cand.get("approach_pattern"),
                "confidence": cand.get("confidence"),
                "sample_count": cand.get("sample_count"),
                "success_count": cand.get("success_count"),
                "failure_count": cand.get("failure_count"),
                "applicable_contexts": cand.get("applicable_contexts", []),
                "supporting_experience_ids":
                    cand.get("supporting_experience_ids", []),
                "supporting_evidence_ids":
                    cand.get("supporting_evidence_ids", []),
                "derived_from": (learning_id,),
                "lifecycle_state": LifecycleState.ACTIVE.value,
            })
        lstore.close()
        return {
            "learning_id": learning_id,
            "role": RecordRole.STRATEGY.value,
            "origin": Origin.DERIVED.value,
            "strategy_count": len(result),
            "strategies": result,
            "min_samples": min_samples,
            "note": "strategies are derived from experiences only; "
                    "never from external knowledge directly",
        }

    # ------------------------------------------------------------------
    # Trace / describe / summary — provenance & lifecycle inspection
    # ------------------------------------------------------------------

    def _resolve(self, record_id, role=None):
        """Resolve any record id to a normalized accessor entry.

        Looks up lifecycle metadata first (authoritative for origin/state),
        then the native store for the given/guessed role.
        """
        record_id = str(record_id)
        lstore = self._lifecycle_store()
        meta = lstore.get(record_id)
        lstore.close()
        entry = None
        if meta is not None:
            entry = normalize_entry(meta.as_dict())
        role = role or _prefix_role(record_id) or (
            entry.get("role") if entry else None)

        native = None
        if role == "experience":
            xstore = self._experience_store()
            native = xstore.get(record_id)
            xstore.close()
            if native is not None:
                native = native.as_dict()
        elif role == "outcome":
            ostore = self._outcome_store()
            r = ostore.get(record_id)
            ostore.close()
            if r is not None:
                native = r.as_dict()
        elif role == "evidence":
            estore = self._evidence_store()
            r = estore.get(record_id)
            estore.close()
            if r is not None:
                native = r.as_dict()
        elif role == "strategy":
            sstore = self._strategy_store()
            r = sstore.get(record_id)
            sstore.close()
            if r is not None:
                native = r.to_dict() if hasattr(r, "to_dict") else None
        elif role == "context":
            cstore = self._context_store()
            r = cstore.get(record_id)
            cstore.close()
            if r is not None:
                native = r.as_dict()
        elif role in ("memory", "knowledge", "source_record"):
            mem = self._memory()
            node = mem.get_node(record_id)
            if node is not None:
                native = dict(node)
                native["record_id"] = record_id
                native["role"] = role

        if entry is None and native is None:
            return None
        return self._merge_entry(entry, native, role)

    @staticmethod
    def _merge_entry(entry, native, role):
        """Merge native store content into a normalized trace entry."""
        entry = dict(entry or {})
        native = native or {}
        if not entry.get("record_id"):
            rid = (native.get("record_id") or native.get("id")
                   or native.get("experience_id") or native.get("outcome_id")
                   or native.get("evidence_id") or native.get("strategy_id")
                   or native.get("context_id"))
            if rid:
                entry["record_id"] = rid
        entry.setdefault("role", role)
        content = dict(entry.get("content") or {})
        # Carry native fields that trace edges depend on.
        if native.get("outcome_id"):
            content.setdefault("outcome_id", native["outcome_id"])
        if native.get("context_id"):
            content.setdefault("context_id", native["context_id"])
        if native.get("evidence_ids"):
            content.setdefault("evidence_ids", list(native["evidence_ids"]))
        if native.get("classification"):
            content.setdefault("classification", native["classification"])
        if native.get("source_observation_id"):
            content.setdefault("source_observation_id",
                               native["source_observation_id"])
        if native.get("summary"):
            content.setdefault("summary", native["summary"])
        if native.get("id") and native.get("type"):
            content.setdefault("node", {"id": native["id"],
                                        "type": native["type"],
                                        "name": native.get("name"),
                                        "description": native.get("description")})
        entry["content"] = content
        prov = entry.get("provenance") or {}
        prov.setdefault("record_id", entry.get("record_id"))
        entry["provenance"] = prov
        entry["subject"] = entry.get("subject") or native.get("subject") \
            or native.get("description") or native.get("name")
        return entry

    def describe(self, record_id, role=None):
        """Describe one record with full lifecycle provenance."""
        entry = self._resolve(record_id, role)
        if entry is None:
            raise ValueError("no such lifecycle record: %r" % (record_id,))
        return {
            "record_id": entry.get("record_id"),
            "role": entry.get("role"),
            "subject": entry.get("subject"),
            "origin": entry.get("origin"),
            "lifecycle_state": entry.get("lifecycle_state"),
            "confidence": entry.get("provenance", {}).get("confidence")
            if entry.get("provenance") else None,
            "confidence_label": confidence_label(
                entry.get("provenance", {}).get("confidence")
                if entry.get("provenance") else None),
            "context_id": entry.get("provenance", {}).get("context_id"),
            "derived_from": list(entry.get("provenance", {}).get(
                "derived_from") or ()) or (entry.get("derived_from") or []),
            "parent_record_ids": list(entry.get("provenance", {}).get(
                "parent_record_ids") or ()) or
                (entry.get("parent_record_ids") or []),
            "evidence_ids": list(entry.get("provenance", {}).get(
                "evidence_ids") or ()) or (entry.get("evidence_ids") or []),
            "content": entry.get("content", {}),
        }

    def trace(self, record_id, role=None, max_depth=16):
        """Deterministic provenance trace from a record."""
        accessor = self._resolve
        try:
            return trace_provenance(record_id, role, accessor,
                                    max_depth=max_depth)
        except KeyError:
            raise ValueError("no such lifecycle record: %r" % (record_id,))

    def summary(self, project_id=None):
        """Aggregate lifecycle counts for a project."""
        pid = project_id or self.project_id
        lstore = self._lifecycle_store()
        result = {
            "project_id": pid,
            "total_records": lstore.count(),
            "by_role": lstore.count_by_role(),
            "by_origin": lstore.count_by_origin(),
            "by_state": lstore.count_by_state(),
        }
        lstore.close()
        return result

    # ------------------------------------------------------------------
    # Contradiction / correction — both records stay; supersession is audited
    # ------------------------------------------------------------------

    def contradictions(self, project_id=None):
        """Detect contradictions across knowledge and experiences."""
        pid = project_id or self.project_id
        mem = self._memory()
        nodes = mem.list_nodes()
        xstore = self._experience_store()
        experiences = xstore.all()
        xstore.close()
        from intelligence.reasoning.contradiction import (
            detect_all_contradictions,
        )
        results = detect_all_contradictions(nodes, experiences)
        return {
            "project_id": pid,
            "count": len(results),
            "contradictions": [
                {
                    "contradiction_type": c.contradiction_type,
                    "node_a": c.node_a,
                    "node_b": c.node_b,
                    "severity": c.severity,
                    "reason": c.description,
                } for c in results
            ],
        }

    def retract(self, record_id, reason=None, evidence_ids=None,
                project_id=None):
        """Retract a record loudly: state -> invalidated (audited).

        Never deletes. Knowledge retraction records a pending lifecycle
        invalidation event; strategy retraction marks the strategy deprecated
        (mutable-with-audit, native); everything else transitions to
        INVALIDATED in the lifecycle store.
        """
        pid = project_id or self.project_id
        record_id = str(record_id)
        entry = self._resolve(record_id)
        if entry is None:
            raise ValueError("no such lifecycle record: %r" % (record_id,))
        role = entry.get("role")

        changed = []
        if role == "strategy":
            sstore = self._strategy_store()
            try:
                sstore.set_deprecated(record_id, True, _now(),
                                      evidence_ids or (), reason)
            except KeyError:
                pass
            sstore.close()
            changed.append("strategies.deprecated")
        lstore = self._lifecycle_store()
        existing = lstore.get(record_id)
        if existing is None:
            prov = Provenance(
                record_id=record_id, origin=Origin.USER_PROVIDED,
                project=pid, created_at=_now(), updated_at=_now(),
            )
            lstore.save(LifecycleRecord(
                record_id=record_id,
                role=role if isinstance(role, RecordRole)
                else _role_enum(role) or RecordRole.MEMORY,
                provenance=prov,
                subject=entry.get("subject"),
                content=dict(entry.get("content") or {}),
            ))
            existing = lstore.get(record_id)
        current = existing.provenance.lifecycle_state
        try:
            updated = lstore.set_state(record_id, LifecycleState.INVALIDATED,
                                       note=reason or "retracted",
                                       created_at_epoch=_now())
        except ValueError as exc:
            lstore.close()
            raise ValueError("%s (uncaught contradiction/correction does "
                             "not delete records)" % (exc,))
        lstore.close()
        audit = {"previous_state": current.value,
                 "new_state": LifecycleState.INVALIDATED.value,
                 "record_id": record_id, "note": reason or "retracted"}
        return {
            "record_id": record_id,
            "role": role,
            "origin": entry.get("origin"),
            "previous_lifecycle_state": current.value,
            "lifecycle_state": LifecycleState.INVALIDATED.value,
            "note": reason or "retracted",
            "never_deletes": True,
            "changed": changed,
            "audit": audit,
        }

    def supersede(self, old_id, new_id, reason=None, evidence_ids=None,
                  project_id=None):
        """Supersede one record with another (old -> superseded, audited).

        Both records remain; the old one is marked superseded referencing the
        new one. Strategies use the native superseded_by audit path.
        """
        pid = project_id or self.project_id
        old_id = str(old_id)
        new_id = str(new_id)
        entry = self._resolve(old_id)
        if entry is None:
            raise ValueError("no such lifecycle record: %r" % (old_id,))
        role = entry.get("role")
        if old_id == new_id:
            raise ValueError("cannot supersede a record with itself")

        changed = []
        if role == "strategy":
            sstore = self._strategy_store()
            try:
                sstore.set_superseded_by(old_id, new_id, _now(),
                                         evidence_ids or (), reason)
                changed.append("strategies.superseded_by")
            except KeyError:
                pass
            sstore.close()
        lstore = self._lifecycle_store()
        existing = lstore.get(old_id)
        if existing is None:
            prov = Provenance(
                record_id=old_id, origin=Origin.USER_PROVIDED,
                project=pid, created_at=_now(), updated_at=_now(),
            )
            lstore.save(LifecycleRecord(
                record_id=old_id,
                role=role if isinstance(role, RecordRole)
                else _role_enum(role) or RecordRole.MEMORY,
                provenance=prov,
                subject=entry.get("subject"),
                content=dict(entry.get("content") or {}),
            ))
            existing = lstore.get(old_id)
        current = existing.provenance.lifecycle_state
        updated = lstore.set_state(old_id, LifecycleState.SUPERSEDED,
                                   note=(reason or "superseded by %s"
                                         % new_id),
                                   created_at_epoch=_now())
        lstore.close()
        return {
            "record_id": old_id,
            "role": role,
            "superseded_by": new_id,
            "previous_lifecycle_state": current.value,
            "lifecycle_state": LifecycleState.SUPERSEDED.value,
            "note": reason or "superseded by %s" % new_id,
            "both_records_preserved": True,
            "changed": changed,
        }


def _actor_from_hints(context_hints):
    try:
        actor = (context_hints or {}).get("actor")
        if isinstance(actor, dict):
            return actor.get("user_id") or actor.get("user") or "local"
        if isinstance(actor, str):
            return actor
    except Exception:
        pass
    return None


def _canonical_pattern(value):
    if not isinstance(value, str):
        value = str(value)
    return " ".join(value.strip().lower().split())


def _role_enum(role):
    from intelligence.lifecycle.model import coerce_role
    return coerce_role(role)