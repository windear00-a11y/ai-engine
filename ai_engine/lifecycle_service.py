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
        -> STRATEGY APPLICATION -> REASONING/DECISION -> PLAN
        -> AUTHORITY / APPROVAL -> ACTION -> OBSERVATION -> VERIFICATION
        -> OUTCOME -> EVIDENCE -> EXPERIENCE (loop)

Phase 29 (safe action, observation & verification loop) is implemented through
this facade: explicit approval grants (``grant_approval``), deterministic
authority evaluation (``evaluate_authority``), the narrow Effect Executor
boundary (``request_action``), observation recording (``record_observation``),
deterministic verification (``verify_action``), verified outcome
(``finalize_action``) and the full chained end-to-end path
(``execute_plan``). Unapproved / unknown actions never reach the effect layer
and caller success claims are never treated as verification evidence.

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

from ai_engine.action import (
    DENIED,
    execute_effect,
    action_status_from_effect,
    derive_action_id,
    derive_default_request_id,
)
from ai_engine.authority import (
    APPROVED,
    INVALID_PLAN,
    derive_authorization_id,
    derive_authority_id,
    evaluate_authority,
)
from ai_engine.observation import (
    OBSERVED,
    observation_status_from_effect,
    derive_observation_id,
)
from ai_engine.verification import (
    CONFLICTING_EVIDENCE,
    INSUFFICIENT_EVIDENCE,
    PARTIAL,
    UNKNOWN,
    VERIFIED_FAILURE,
    VERIFIED_SUCCESS,
    VERIFICATION_RESULTS,
    derive_verification_id,
    verify,
)


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


def _now():
    return time.time()


def _as_tuple(value):
    """Normalize a record id (or sequence) into a tuple for provenance."""
    if value is None:
        return ()
    if isinstance(value, (tuple, list, set)):
        return tuple(value)
    return (value,)


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
    if rid.startswith("sa_"):
        return "strategy_application"
    if rid.startswith("rs_"):
        return "reasoning"
    if rid.startswith("dc_"):
        return "decision"
    if rid.startswith("pl_") or rid.startswith("pls_"):
        return "plan"
    if rid.startswith("au_"):
        return "authority"
    if rid.startswith("az_"):
        return "authorization"
    if rid.startswith("ac_"):
        return "action"
    if rid.startswith("ob_"):
        return "observation"
    if rid.startswith("vf_"):
        return "verification"
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
                       context_id=None, project_id=None,
                       verification_id=None):
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
        parents = [plan_id]
        content = {"classification": classification.value,
                   "plan_id": plan_id,
                   "verification_evidence_ids": sorted(evidence_ids)}
        if verification_id:
            parents.append(str(verification_id))
            content["verification_id"] = str(verification_id)
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
                parent_record_ids=tuple(parents),
                created_at=_now(),
                updated_at=_now(),
            ),
            subject="outcome %s" % classification.value,
            content=content,
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
    # STRATEGY APPLICATION -> REASONING -> DECISION -> PLAN (Phase 28)
    # ------------------------------------------------------------------
    # Canonical chain: a strategy applies deterministically to a situation;
    # reasoning determines what the information supports; decision selects an
    # intended course under constraints; the plan describes the intended steps
    # WITHOUT execution. All derived records persist with the same lifecycle
    # provenance model (roles STRATEGY_APPLICATION / REASONING / DECISION /
    # PLAN, origin DERIVED) so the Phase 26 trace walks the full chain.

    def apply_strategy(self, situation, strategy_ids=None, context_id=None,
                       max_candidates=20, project_id=None):
        """Deterministic canonical Strategy Application.

        Evaluates strategy candidates against ``situation`` and (optionally)
        ``context_id`` and persists one STRATEGY_APPLICATION record. Existing
        Phase 25 fit semantics (deprecated/superseded rejected, context
        restrictions honored) are preserved; a missing current context under
        restrictions is surfaced as insufficient_evidence rather than assumed.
        """
        if not isinstance(situation, str) or not situation.strip():
            raise ValueError("situation must be a non-empty string")
        pid = project_id or self.project_id
        sstore = self._strategy_store()
        try:
            if strategy_ids is None:
                strategies = sstore.all()
            else:
                ids = sorted({str(x) for x in strategy_ids})
                strategies = []
                for strategy_id in ids:
                    strategy = sstore.get(strategy_id)
                    if strategy is not None:
                        strategies.append(strategy)
        finally:
            sstore.close()

        from ai_engine.strategy_application import apply_strategies
        result = apply_strategies(
            situation, [s.to_dict() for s in strategies],
            context_id=context_id, max_candidates=max_candidates)
        strategy_id = result.get("strategy_id")
        supporting_evidence = result["provenance"]["supporting_evidence_ids"]
        supporting_experience = result["provenance"][
            "supporting_experience_ids"]

        lstore = self._lifecycle_store()
        lstore.save(LifecycleRecord(
            record_id=result["application_id"],
            role=RecordRole.STRATEGY_APPLICATION,
            provenance=Provenance(
                record_id=result["application_id"],
                origin=Origin.DERIVED,
                timestamp=_now(),
                project=pid,
                context_id=context_id,
                evidence_ids=supporting_evidence,
                confidence=result["confidence"] or None,
                lifecycle_state=LifecycleState.ACTIVE,
                parent_record_ids=(strategy_id,) if strategy_id else (),
                derived_from=(strategy_id,) if strategy_id else (),
                created_at=_now(),
                updated_at=_now(),
            ),
            subject="strategy application: situation %r (%s)"
                    % (result["situation_pattern"], result["status"]),
            content={
                "situation": result["situation"],
                "situation_pattern": result["situation_pattern"],
                "context_id": context_id,
                "application_status": result["status"],
                "strategy_id": strategy_id,
                "candidate_ids": [c["strategy_id"]
                                  for c in result["candidates"]],
                "candidate_statuses": [
                    {"strategy_id": c["strategy_id"],
                     "strategy_status": c["strategy_status"],
                     "recommended_approach": c["recommended_approach"]}
                    for c in result["candidates"]],
                "applicable_count": result["applicable_count"],
                "supporting_experience_ids": supporting_experience,
                "supporting_evidence_ids": supporting_evidence,
                "evidence_ids": supporting_evidence,
                "uncertainties": result["uncertainties"],
                "truncated": result["truncated"],
            },
        ))
        lstore.close()
        return {
            "application_id": result["application_id"],
            "role": RecordRole.STRATEGY_APPLICATION.value,
            "origin": Origin.DERIVED.value,
            "status": result["status"],
            "situation": result["situation"],
            "situation_pattern": result["situation_pattern"],
            "context_id": context_id,
            "strategy_id": strategy_id,
            "application": result["application"],
            "candidates": result["candidates"],
            "applicable_count": result["applicable_count"],
            "candidate_count": result["candidate_count"],
            "truncated": result["truncated"],
            "rationale": result["rationale"],
            "confidence": result["confidence"],
            "uncertainties": result["uncertainties"],
            "provenance": result["provenance"],
            "lifecycle_state": LifecycleState.ACTIVE.value,
        }

    def _load_application(self, strategy_application_id, context_id=None):
        lstore = self._lifecycle_store()
        try:
            meta = lstore.get(strategy_application_id)
        finally:
            lstore.close()
        if meta is None:
            raise ValueError("no such strategy_application_id: %r"
                             % (strategy_application_id,))
        content = meta.content or {}
        if context_id is None:
            context_id = content.get("context_id")
        candidates = []
        for c in content.get("candidate_statuses") or ():
            candidates.append({
                "strategy_id": c.get("strategy_id"),
                "strategy_status": c.get("strategy_status"),
                "recommended_approach": c.get("recommended_approach"),
            })
        if not candidates:
            for strategy_id in content.get("candidate_ids") or ():
                status = "applicable" if strategy_id \
                    == content.get("strategy_id") else "not_applicable"
                candidates.append({"strategy_id": strategy_id,
                                   "strategy_status": status,
                                   "recommended_approach": None})
        return {
            "application_id": strategy_application_id,
            "status": content.get("application_status"),
            "strategy_id": content.get("strategy_id"),
            "candidates": candidates,
            "context_id": context_id or content.get("context_id"),
            "situation": content.get("situation"),
            "supporting_experience_ids":
                content.get("supporting_experience_ids", []),
        }

    def _strategy_dicts(self, strategy_ids):
        ids = sorted({str(x) for x in strategy_ids or ()})
        if not ids:
            return []
        sstore = self._strategy_store()
        try:
            result = []
            for strategy_id in ids:
                strategy = sstore.get(strategy_id)
                if strategy is not None:
                    result.append(strategy.to_dict())
        finally:
            sstore.close()
        result.sort(key=lambda s: (s.get("deprecated", False),
                                   bool(s.get("superseded_by")),
                                   -float(s.get("confidence") or 0.0),
                                   str(s.get("strategy_id"))))
        return result

    def reason(self, situation, strategy_application_id=None,
               strategy_ids=None, context_id=None, evidence_ids=None,
               experience_ids=None, knowledge_ids=None, constraints=None,
               bounds=None, project_id=None):
        """Deterministic canonical Reasoning over structured inputs.

        Deterministic, bounded and uncertainty-preserving: missing evidence is
        never invented, conflicting inputs are surfaced, and the result keeps
        every provenance reference. Persists one REASONING record.
        """
        if not isinstance(situation, str) or not situation.strip():
            raise ValueError("situation must be a non-empty string")
        pid = project_id or self.project_id

        application = None
        if strategy_application_id:
            application = self._load_application(strategy_application_id,
                                                 context_id)
            context_id = application.get("context_id") or context_id
        applicable_ids = [
            c["strategy_id"] for c in (application or {}).get("candidates")
            if c.get("strategy_status") == "applicable"] \
            if application else None
        strategy_ids = strategy_ids or applicable_ids
        strategies = self._strategy_dicts(strategy_ids)

        evidences = []
        for evidence_id in sorted({str(x) for x in evidence_ids or ()}):
            estore = self._evidence_store()
            try:
                record = estore.get(evidence_id)
            finally:
                estore.close()
            if record is not None:
                evidences.append(record.as_dict())

        experiences = []
        for experience_id in sorted({str(x) for x in experience_ids or ()}):
            xstore = self._experience_store()
            try:
                record = xstore.get(experience_id)
            finally:
                xstore.close()
            if record is not None:
                experiences.append(record.as_dict())

        knowledge = []
        for node_id in sorted({str(x) for x in knowledge_ids or ()}):
            mem = self._memory()
            try:
                node = mem.get_node(node_id)
            finally:
                pass
            if node is not None:
                knowledge.append(dict(node))

        from ai_engine.reasoning import reason as reason_engine
        result = reason_engine(
            situation, application=application, strategies=strategies,
            knowledge=knowledge, experiences=experiences, evidence=evidences,
            context_id=context_id, constraints=constraints, bounds=bounds)
        rs_id = result["reasoning_id"]
        derived_from = (strategy_application_id,) \
            if strategy_application_id else (
                tuple(result["applicable_strategy_ids"])
                if result["applicable_strategy_ids"] else ())
        parent_ids = tuple(result["applicable_strategy_ids"])

        lstore = self._lifecycle_store()
        lstore.save(LifecycleRecord(
            record_id=rs_id,
            role=RecordRole.REASONING,
            provenance=Provenance(
                record_id=rs_id,
                origin=Origin.DERIVED,
                timestamp=_now(),
                project=pid,
                context_id=context_id,
                evidence_ids=tuple(result["inputs"]["evidence_ids"]),
                confidence=result["confidence"],
                lifecycle_state=LifecycleState.ACTIVE,
                parent_record_ids=parent_ids,
                derived_from=derived_from,
                created_at=_now(),
                updated_at=_now(),
            ),
            subject="reasoning for situation %r (%s)"
                    % (result["situation_pattern"], result["status"]),
            content={
                "reasoning_status": result["status"],
                "situation": result["situation"],
                "situation_pattern": result["situation_pattern"],
                "context_id": context_id,
                "strategy_application_id": strategy_application_id,
                "applicable_strategy_ids": result["applicable_strategy_ids"],
                "knowledge_ids": result["inputs"]["knowledge_ids"],
                "experience_ids": result["inputs"]["experience_ids"],
                "evidence_ids": result["inputs"]["evidence_ids"],
                "constraints": constraints or {},
                "conflicts": result["conflicts"],
                "uncertainties": result["uncertainties"],
                "recommendation": result["recommendation"],
                "recommended_approach": result["recommended_approach"],
                "truncated": result["truncated"],
            },
        ))
        lstore.close()
        return {
            "reasoning_id": rs_id,
            "role": RecordRole.REASONING.value,
            "origin": Origin.DERIVED.value,
            "status": result["status"],
            "situation": result["situation"],
            "situation_pattern": result["situation_pattern"],
            "context_id": context_id,
            "strategy_application_id": strategy_application_id,
            "applicable_strategy_id": result["applicable_strategy_id"],
            "applicable_strategy_ids": result["applicable_strategy_ids"],
            "recommendation": result["recommendation"],
            "recommended_approach": result["recommended_approach"],
            "confidence": result["confidence"],
            "conflicts": result["conflicts"],
            "uncertainties": result["uncertainties"],
            "inputs": result["inputs"],
            "bounds": result["bounds"],
            "truncated": result["truncated"],
            "provenance": result["provenance"],
            "lifecycle_state": LifecycleState.ACTIVE.value,
        }

    def decide(self, reasoning_id=None, reasoning=None, constraints=None,
               alternatives=None, project_id=None):
        """Deterministic canonical Decision under explicit constraints.

        Distinguishes reasoning (what information supports) from decision
        (which course to select) and never executes anything. May return
        UNKNOWN / INSUFFICIENT_EVIDENCE rather than inventing certainty.
        """
        pid = project_id or self.project_id
        if reasoning is None:
            if reasoning_id is None:
                raise ValueError("reasoning_id or reasoning is required")
            lstore = self._lifecycle_store()
            try:
                meta = lstore.get(reasoning_id)
            finally:
                lstore.close()
            if meta is None:
                raise ValueError("no such reasoning_id: %r" % (reasoning_id,))
            content = meta.content or {}
            reasoning = {
                "reasoning_id": reasoning_id,
                "status": content.get("reasoning_status"),
                "recommendation": content.get("recommendation"),
                "recommended_approach": content.get("recommended_approach"),
                "applicable_strategy_id":
                    (content.get("applicable_strategy_ids") or [None])[0],
                "strategy_application_id":
                    content.get("strategy_application_id"),
                "context_id": content.get("context_id"),
                "situation": content.get("situation"),
            }
        reasoning = dict(reasoning)

        from ai_engine.decision import make_decision
        decision = make_decision(reasoning, constraints=constraints,
                                 alternatives=alternatives)
        dc_id = decision["decision_id"]

        lstore = self._lifecycle_store()
        lstore.save(LifecycleRecord(
            record_id=dc_id,
            role=RecordRole.DECISION,
            provenance=Provenance(
                record_id=dc_id,
                origin=Origin.DERIVED,
                timestamp=_now(),
                project=pid,
                context_id=reasoning.get("context_id"),
                confidence=decision["confidence"],
                lifecycle_state=LifecycleState.ACTIVE,
                parent_record_ids=(decision["strategy_application_id"],)
                if decision["strategy_application_id"] else (),
                derived_from=_as_tuple(reasoning.get("reasoning_id")),
                created_at=_now(),
                updated_at=_now(),
            ),
            subject="decision for situation %r (%s)"
                    % (reasoning.get("situation") or "",
                       decision["status"]),
            content={
                "decision_status": decision["status"],
                "situation": reasoning.get("situation"),
                "context_id": reasoning.get("context_id"),
                "selected_course": decision["selected_course"],
                "reasoning_id": reasoning.get("reasoning_id"),
                "strategy_application_id":
                    decision["strategy_application_id"],
                "applicable_strategy_id": decision["applicable_strategy_id"],
                "constraints": decision["constraints"],
                "alternatives": decision["alternatives"],
                "explicit_unknown": decision["explicit_unknown"],
            },
        ))
        lstore.close()
        return {
            "decision_id": dc_id,
            "role": RecordRole.DECISION.value,
            "origin": Origin.DERIVED.value,
            "status": decision["status"],
            "situation": reasoning.get("situation"),
            "context_id": reasoning.get("context_id"),
            "selected_course": decision["selected_course"],
            "selected_option_id": decision["selected_option_id"],
            "rationale": decision["rationale"],
            "alternatives": decision["alternatives"],
            "reasoning_id": reasoning.get("reasoning_id"),
            "strategy_application_id": decision["strategy_application_id"],
            "applicable_strategy_id": decision["applicable_strategy_id"],
            "constraints": decision["constraints"],
            "confidence": decision["confidence"],
            "explicit_unknown": decision["explicit_unknown"],
            "provenance": decision["provenance"],
            "lifecycle_state": LifecycleState.ACTIVE.value,
        }

    def plan(self, decision_id=None, decision=None, constraints=None,
             max_steps=8, project_id=None):
        """Deterministic canonical Plan over a decided course.

        A plan is an intended sequence of actions, never execution. It marks
        the PLAN -> ACTION handoff (Phase 29) explicitly and stays not-ready.
        """
        pid = project_id or self.project_id
        if decision is None:
            if decision_id is None:
                raise ValueError("decision_id or decision is required")
            lstore = self._lifecycle_store()
            try:
                meta = lstore.get(decision_id)
            finally:
                lstore.close()
            if meta is None:
                raise ValueError("no such decision_id: %r" % (decision_id,))
            content = meta.content or {}
            decision = {
                "decision_id": decision_id,
                "status": content.get("decision_status"),
                "selected_course": content.get("selected_course"),
                "reasoning_id": content.get("reasoning_id"),
                "strategy_application_id":
                    content.get("strategy_application_id"),
                "applicable_strategy_id": content.get("applicable_strategy_id"),
                "constraints": content.get("constraints"),
                "context_id": content.get("context_id"),
                "situation": content.get("situation"),
            }
        decision = dict(decision)

        from ai_engine.plan import build_plan
        plan_result = build_plan(
            decision, situation=decision.get("situation"),
            constraints=constraints, max_steps=max_steps)
        pl_id = plan_result["plan_id"]
        decision_id = decision.get("decision_id")

        lstore = self._lifecycle_store()
        lstore.save(LifecycleRecord(
            record_id=pl_id,
            role=RecordRole.PLAN,
            provenance=Provenance(
                record_id=pl_id,
                origin=Origin.DERIVED,
                timestamp=_now(),
                project=pid,
                context_id=decision.get("context_id"),
                lifecycle_state=LifecycleState.ACTIVE,
                parent_record_ids=(decision_id,),
                derived_from=(decision_id,),
                created_at=_now(),
                updated_at=_now(),
            ),
            subject="intended plan for decision %s (%s)"
                    % (decision_id, plan_result["status"]),
            content={
                "plan_status": plan_result["status"],
                "situation": plan_result["situation"],
                "context_id": decision.get("context_id"),
                "decision_id": decision_id,
                "ordered_steps": plan_result["ordered_steps"],
                "preconditions": plan_result["preconditions"],
                "expected_observations": plan_result["expected_observations"],
                "verification_requirements":
                    plan_result["verification_requirements"],
                "constraints": plan_result["constraints"],
                "action_handoff": plan_result["action_handoff"],
                "executed": False,
            },
        ))
        lstore.close()
        return {
            "plan_id": pl_id,
            "role": RecordRole.PLAN.value,
            "origin": Origin.DERIVED.value,
            "status": plan_result["status"],
            "situation": plan_result["situation"],
            "context_id": decision.get("context_id"),
            "decision_id": decision_id,
            "ordered_steps": plan_result["ordered_steps"],
            "preconditions": plan_result["preconditions"],
            "expected_observations": plan_result["expected_observations"],
            "verification_requirements":
                plan_result["verification_requirements"],
            "constraints": plan_result["constraints"],
            "action_handoff": plan_result["action_handoff"],
            "provenance": plan_result["provenance"],
            "lifecycle_state": LifecycleState.ACTIVE.value,
        }

    def plan_from_experiences(self, situation, experience_ids,
                              context_id=None, constraints=None, max_steps=8,
                              min_samples=None, project_id=None):
        """Deterministic end-to-end canonical path (Phase 28).

        Runs the full chain
        experience -> learning -> strategy -> strategy application ->
        reasoning -> decision -> plan and returns every derived id.
        Nothing is executed and no context is written.
        """
        if not isinstance(situation, str) or not situation.strip():
            raise ValueError("situation must be a non-empty string")
        if not isinstance(experience_ids, (list, tuple)) or not experience_ids:
            raise ValueError("experience_ids must be be a non-empty list")
        pid = project_id or self.project_id
        strategies = self.derive_strategies(
            experience_ids, project_id=pid, min_samples=min_samples)
        strategy_ids = [s["strategy_id"] for s in strategies["strategies"]]

        if context_id is None:
            xstore = self._experience_store()
            context_ids = []
            try:
                for sid in sorted({str(x) for x in experience_ids}):
                    record = xstore.get(sid)
                    if record is not None and record.context_id:
                        context_ids.append(record.context_id)
            finally:
                xstore.close()
            context_id = context_ids[0] if context_ids else None

        application = self.apply_strategy(
            situation, strategy_ids=strategy_ids, context_id=context_id,
            project_id=pid)
        reasoning = self.reason(
            situation, strategy_application_id=application["application_id"],
            context_id=context_id,
            experience_ids=application["provenance"]["supporting_experience_ids"],
            evidence_ids=application["provenance"]["supporting_evidence_ids"],
            constraints=constraints, project_id=pid)
        decision = self.decide(reasoning=reasoning, constraints=constraints,
                               project_id=pid)
        plan = self.plan(decision=decision, constraints=constraints,
                         max_steps=max_steps, project_id=pid)
        return {
            "situation": situation,
            "learning_id": strategies["learning_id"],
            "strategy_ids": strategy_ids,
            "strategy_application_id": application["application_id"],
            "strategy_application_status": application["status"],
            "reasoning_id": reasoning["reasoning_id"],
            "reasoning_status": reasoning["status"],
            "decision_id": decision["decision_id"],
            "decision_status": decision["status"],
            "plan_id": plan["plan_id"],
            "plan_status": plan["status"],
            "chain": ["experience", "learning", "strategy",
                      "strategy_application", "reasoning", "decision",
                      "plan"],
            "strategy_application": application,
            "reasoning": reasoning,
            "decision": decision,
            "plan": plan,
            "note": "the intelligence loop ends at the plan boundary; no "
                    "action was executed and no context was written",
        }

    # ------------------------------------------------------------------
    # Phase 29 — safe action, observation & verification loop
    #
    # Deterministic, project-isolated, backend-only. Unapproved / unknown
    # actions never reach the effect layer; caller success claims are never
    # verification evidence; an outcome is never marked verified success
    # without verification evidence.
    # ------------------------------------------------------------------

    def _load_plan_meta(self, plan_id, project_id=None):
        """Load a plan's lifecycle metadata in the caller's project."""
        pid = project_id or self.project_id
        plan_id = str(plan_id)
        lstore = self._lifecycle_store()
        try:
            meta = lstore.get(plan_id)
        finally:
            lstore.close()
        if meta is None:
            return None
        if (meta.provenance.project or pid) != pid:
            raise ValueError("plan %s does not belong to project %s"
                             % (plan_id, pid))
        if not meta.role or meta.role.value != RecordRole.PLAN.value:
            raise ValueError("%r is not a plan record" % (plan_id,))
        if not meta.provenance.lifecycle_state == LifecycleState.ACTIVE:
            raise ValueError("plan %s is not active" % (plan_id,))
        return meta

    @staticmethod
    def _plan_steps(meta):
        return list(((meta.content or {}).get("ordered_steps")) or [])

    def _find_plan_step(self, meta, plan_step_id):
        for step in self._plan_steps(meta):
            if str(step.get("step_id")) == str(plan_step_id):
                return dict(step)
        return None

    def _load_action_record(self, action_id, project_id=None):
        pid = project_id or self.project_id
        action_id = str(action_id)
        lstore = self._lifecycle_store()
        try:
            meta = lstore.get(action_id)
        finally:
            lstore.close()
        if meta is None:
            return None
        if (meta.provenance.project or pid) != pid:
            raise ValueError("action %s does not belong to project %s"
                             % (action_id, pid))
        if not meta.role or meta.role.value != RecordRole.ACTION.value:
            raise ValueError("%r is not an action record" % (action_id,))
        return meta

    def _observations_for(self, action_id, project_id=None):
        pid = project_id or self.project_id
        lstore = self._lifecycle_store()
        try:
            rows = lstore.for_role(RecordRole.OBSERVATION)
        finally:
            lstore.close()
        return sorted(
            [r for r in rows
             if (r.provenance.project or pid) == pid
             and ((r.content or {}).get("action_id") == str(action_id))],
            key=lambda r: r.record_id)

    def _verifications_for(self, action_id, project_id=None):
        pid = project_id or self.project_id
        lstore = self._lifecycle_store()
        try:
            rows = lstore.for_role(RecordRole.VERIFICATION)
        finally:
            lstore.close()
        return sorted(
            [r for r in rows
             if (r.provenance.project or pid) == pid
             and ((r.content or {}).get("action_id") == str(action_id))],
            key=lambda r: r.record_id)

    @staticmethod
    def _denial_evidence_id(action_id, plan_step_id, decision):
        return "ev_" + hashlib.sha256(_canonical({
            "source": action_id,
            "claim": "action denied by authority",
            "decision": decision,
            "plan_step_id": plan_step_id,
        }).encode("utf-8")).hexdigest()[:32]

    def _grant_record(self, meta, plan_id, plan_step_ids, actor, mechanism,
                      evidence_ids):
        pid = self.project_id
        content = {
            "plan_id": plan_id,
            "plan_step_ids": sorted(plan_step_ids),
            "actor": actor,
            "mechanism": mechanism,
            "state": "approved",
            "evidence_ids": sorted(str(x) for x in evidence_ids),
        }
        lstore = self._lifecycle_store()
        lstore.save(LifecycleRecord(
            record_id=derive_authorization_id(
                plan_id, plan_step_ids, actor, mechanism),
            role=RecordRole.AUTHORIZATION,
            provenance=Provenance(
                record_id=derive_authorization_id(
                    plan_id, plan_step_ids, actor, mechanism),
                origin=Origin.USER_PROVIDED,
                source=mechanism,
                actor=actor,
                timestamp=_now(),
                project=pid,
                context_id=(meta.provenance.context_id
                            if meta is not None else None),
                lifecycle_state=LifecycleState.ACTIVE,
                parent_record_ids=(plan_id,),
                created_at=_now(),
                updated_at=_now(),
            ),
            subject="explicit approval grant for plan %s by %s"
                    % (plan_id, actor),
            content=content,
        ))
        lstore.close()
        return content

    def grant_approval(self, plan_id, plan_step_ids, actor,
                       mechanism="explicit_user_approval", evidence_ids=(),
                       project_id=None):
        """Explicit approval grant (recorded, audited, idempotent)."""
        pid = project_id or self.project_id
        plan_id = str(plan_id)
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor must be a non-empty string")
        plan_step_ids = [str(x) for x in (plan_step_ids or ())]
        if not plan_step_ids:
            raise ValueError("plan_step_ids must be a non-empty list")
        meta = self._load_plan_meta(plan_id, pid)
        if meta is None:
            raise ValueError("no such plan_id: %r" % (plan_id,))
        known = {str(s.get("step_id")) for s in self._plan_steps(meta)}
        unknown = sorted(set(plan_step_ids) - known)
        if unknown:
            raise ValueError("plan %s has no such steps: %s"
                             % (plan_id, unknown))
        grant_id = derive_authorization_id(plan_id, plan_step_ids, actor,
                                           mechanism)
        lstore = self._lifecycle_store()
        try:
            existing = lstore.get(grant_id)
        finally:
            lstore.close()
        if existing is not None:
            content = existing.content or {}
            content["grant_id"] = grant_id
            content["duplicate"] = True
            return content
        self._grant_record(meta, plan_id, plan_step_ids, actor, mechanism,
                           evidence_ids)
        return {
            "grant_id": grant_id,
            "plan_id": plan_id,
            "plan_step_ids": sorted(plan_step_ids),
            "actor": actor,
            "mechanism": mechanism,
            "state": "approved",
            "project_id": pid,
            "role": RecordRole.AUTHORIZATION.value,
            "origin": Origin.USER_PROVIDED.value,
        }

    def _approvals_for(self, plan_id, project_id=None):
        pid = project_id or self.project_id
        lstore = self._lifecycle_store()
        try:
            rows = lstore.for_role(RecordRole.AUTHORIZATION)
        finally:
            lstore.close()
        approvals = []
        for row in rows:
            if (row.provenance.project or pid) != pid:
                continue
            content = row.content or {}
            if str(content.get("plan_id")) != str(plan_id):
                continue
            for step_id in (content.get("plan_step_ids") or []):
                approvals.append({
                    "plan_id": str(plan_id),
                    "step_id": str(step_id),
                    "actor": content.get("actor"),
                    "state": content.get("state") or "approved",
                })
        return approvals

    def evaluate_authority(self, plan_id, plan_step_ids, actor, policy=None,
                           project_id=None, request_ref=None):
        """Deterministic authority/approval evaluation for plan steps.

        Persists an AUTHORITY record (origin derived) and returns the decision.
        ``UNKNOWN`` is never approval and never permits execution.
        """
        pid = project_id or self.project_id
        plan_id = str(plan_id)
        plan_step_ids = [str(x) for x in (plan_step_ids or ())]
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor must be a non-empty string")
        meta = self._load_plan_meta(plan_id, pid)
        if meta is None:
            decision = INVALID_PLAN
            per_step = {}
            rationale = "no such plan in project %s: %r" % (pid, plan_id)
            authority_id = derive_authority_id(
                plan_id, plan_step_ids, actor, pid, "canonical", request_ref)
            result = {
                "authority_id": authority_id,
                "plan_id": plan_id,
                "plan_step_ids": sorted(plan_step_ids),
                "actor": actor,
                "project_id": pid,
                "policy_ref": "canonical",
                "decision": decision,
                "per_step": per_step,
                "rationale": rationale,
            }
        else:
            steps = self._plan_steps(meta)
            result = evaluate_authority(
                plan_id, plan_step_ids, actor, pid, policy=policy,
                steps=steps, approvals=self._approvals_for(plan_id, pid),
                policy_ref="canonical", request_ref=request_ref)
        self._record_authority(meta, plan_id, result, pid)
        return result

    def _record_authority(self, meta, plan_id, result, project_id):
        lstore = self._lifecycle_store()
        lstore.save(LifecycleRecord(
            record_id=result["authority_id"],
            role=RecordRole.AUTHORITY,
            provenance=Provenance(
                record_id=result["authority_id"],
                origin=Origin.DERIVED,
                source="authority.evaluate",
                actor=result.get("actor"),
                timestamp=_now(),
                project=project_id,
                context_id=(meta.provenance.context_id
                            if meta is not None else None),
                lifecycle_state=LifecycleState.ACTIVE,
                parent_record_ids=(plan_id,),
                derived_from=(plan_id,),
                created_at=_now(),
                updated_at=_now(),
            ),
            subject="authority %s for plan %s" % (result.get("decision"),
                                                  plan_id),
            content={
                "plan_id": plan_id,
                "plan_step_ids": sorted(result.get("plan_step_ids") or []),
                "actor": result.get("actor"),
                "project_id": project_id,
                "decision": result.get("decision"),
                "per_step": result.get("per_step") or {},
                "policy_ref": result.get("policy_ref"),
                "rationale": result.get("rationale"),
            },
        ))
        lstore.close()

    def _save_action(self, status, plan_id, plan_step_id, actor, request_id,
                     effect, authority_id, authority_decision, project_id,
                     context_id, observation_ids=None, detail=None):
        action_id = derive_action_id(plan_id, plan_step_id, actor, request_id,
                                     project_id)
        content = {
            "plan_id": plan_id,
            "plan_step_id": plan_step_id,
            "actor": actor,
            "project_id": project_id,
            "request_id": request_id,
            "execution_status": status,
            "registered_effect": effect,
            "authority_id": authority_id,
            "authority_decision": authority_decision,
            "observation_references": sorted(observation_ids or ()),
        }
        if detail is not None:
            content["detail"] = detail
        lstore = self._lifecycle_store()
        lstore.save(LifecycleRecord(
            record_id=action_id,
            role=RecordRole.ACTION,
            provenance=Provenance(
                record_id=action_id,
                origin=Origin.OBSERVED,
                source="action.request",
                actor=actor,
                timestamp=_now(),
                project=project_id,
                context_id=context_id,
                lifecycle_state=LifecycleState.ACTIVE,
                parent_record_ids=(plan_id, authority_id),
                created_at=_now(),
                updated_at=_now(),
            ),
            subject="action %s for plan step %s (%s)"
                    % (action_id, plan_step_id, status),
            content=content,
        ))
        lstore.close()
        return action_id

    def _record_observation(self, action_id, observed_state, status, source,
                            project_id, context_id=None, evidence_ids=(),
                            sequence=0):
        obs_id = derive_observation_id(action_id, source,
                                       observed_state or {})
        pid = project_id or self.project_id
        lstore = self._lifecycle_store()
        try:
            existing = lstore.get(obs_id)
        finally:
            lstore.close()
        if existing is not None:
            return (obs_id, sorted(existing.content.get("evidence_ids")
                                   or existing.provenance.evidence_ids or ()))
        doc_evidence = self._record_observation_evidence(
            obs_id, observed_state or {}, pid, context_id)
        doc_evidence = sorted(set(doc_evidence) | set(
            str(x) for x in evidence_ids))
        lstore = self._lifecycle_store()
        lstore.save(LifecycleRecord(
            record_id=obs_id,
            role=RecordRole.OBSERVATION,
            provenance=Provenance(
                record_id=obs_id,
                origin=Origin.OBSERVED,
                source=source,
                timestamp=_now(),
                project=pid,
                context_id=context_id,
                evidence_ids=tuple(doc_evidence),
                lifecycle_state=LifecycleState.ACTIVE,
                parent_record_ids=(action_id,),
                created_at=_now(),
                updated_at=_now(),
            ),
            subject="observation %s for action %s (%s)" % (obs_id, action_id,
                                                           status),
            content={
                "action_id": action_id,
                "status": status,
                "source": source,
                "observed_state": dict(observed_state or {}),
                "sequence": int(sequence),
                "evidence_ids": doc_evidence,
            },
        ))
        lstore.close()
        return (obs_id, doc_evidence)

    def _record_observation_evidence(self, observation_id, observed_state,
                                     project_id, context_id=None):
        evidence_ids = []
        for key in sorted((observed_state or {}).keys()):
            value = observed_state[key]
            claim = "%s=%s" % (key, _canonical(value))
            rec = self.record_evidence(
                source_observation_id=observation_id, claim=claim,
                evidence_type="fact", project_id=project_id,
                context_id=context_id)
            evidence_ids.append(rec["evidence_id"])
        return evidence_ids

    def request_action(self, plan_id, plan_step_id, actor, request_id=None,
                       policy=None, executors=None, project_id=None):
        """Canonical safe ACTION attempt via the narrow executor boundary.

        Authority is evaluated first; anything other than APPROVED produces a
        DENIED action that never reaches the effect layer. Approved actions
        execute through an Effect Executor, and the executor's reported
        reality becomes the first OBSERVATION of the action.
        """
        pid = project_id or self.project_id
        plan_id = str(plan_id)
        plan_step_id = str(plan_step_id)
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor must be a non-empty string")
        meta = self._load_plan_meta(plan_id, pid)
        if meta is None:
            raise ValueError("no such plan_id: %r" % (plan_id,))
        step = self._find_plan_step(meta, plan_step_id)
        if step is None:
            raise ValueError("plan %s has no such step: %r"
                             % (plan_id, plan_step_id))
        context_id = meta.provenance.context_id
        request_id = request_id or derive_default_request_id(
            plan_id, plan_step_id, actor, pid)
        action_id = derive_action_id(plan_id, plan_step_id, actor, request_id,
                                     pid)
        existing = self._load_action_record(action_id, pid)
        if existing is not None:
            status = (existing.content or {}).get("execution_status")
            return {
                "action_id": action_id,
                "plan_id": plan_id,
                "plan_step_id": plan_step_id,
                "actor": actor,
                "request_id": request_id,
                "project_id": pid,
                "execution_status": status,
                "authority_decision":
                    (existing.content or {}).get("authority_decision"),
                "observation_ids":
                    sorted((existing.content or {}).get(
                        "observation_references") or ()),
                "duplicate": True,
                "retry_policy": (
                    "pass a distinct request_id to execute a new attempt of "
                    "this plan step"),
            }

        authority = self.evaluate_authority(
            plan_id, [plan_step_id], actor, policy=policy, project_id=pid,
            request_ref=request_id)
        decision = authority["decision"]
        effect = str(step.get("action"))
        if decision != APPROVED:
            action_id = self._save_action(
                DENIED, plan_id, plan_step_id, actor, request_id, effect,
                authority["authority_id"], decision, pid, context_id)
            return {
                "action_id": action_id,
                "plan_id": plan_id,
                "plan_step_id": plan_step_id,
                "actor": actor,
                "request_id": request_id,
                "project_id": pid,
                "execution_status": DENIED,
                "authority_decision": decision,
                "authority_id": authority["authority_id"],
                "observation_ids": [],
                "effect": effect,
                "executed": False,
                "duplicate": False,
                "note": "unapproved/denied actions never reach the effect "
                        "layer",
            }

        effect_result = execute_effect(effect, step.get("inputs") or {},
                                       executor=executors)
        status = action_status_from_effect(effect_result["status"])
        observation_ids = []
        evidence_ids = []
        obs_id, doc_evidence = self._record_observation(
            action_id, effect_result["observed_state"],
            observation_status_from_effect(effect_result["status"]),
            source="effect_executor", project_id=pid, context_id=context_id)
        observation_ids.append(obs_id)
        evidence_ids.extend(doc_evidence)
        detail = effect_result.get("detail")
        if isinstance(detail, dict):
            detail = dict(detail)
        else:
            detail = str(detail or "")
        if effect_result["unsupported"]:
            detail = {"unsupported": True, "detail": str(
                effect_result.get("detail") or "unsupported effect")}
        action_id = self._save_action(
            status, plan_id, plan_step_id, actor, request_id, effect,
            authority["authority_id"], decision, pid, context_id,
            observation_ids=observation_ids, detail=detail)
        return {
            "action_id": action_id,
            "plan_id": plan_id,
            "plan_step_id": plan_step_id,
            "actor": actor,
            "request_id": request_id,
            "project_id": pid,
            "execution_status": status,
            "authority_decision": decision,
            "authority_id": authority["authority_id"],
            "observation_ids": observation_ids,
            "evidence_ids": sorted(evidence_ids),
            "effect": effect,
            "executed": True,
            "duplicate": False,
        }

    def record_observation(self, action_id, observed_state, source=None,
                           project_id=None):
        """Record an additional OBSERVATION for an existing action."""
        pid = project_id or self.project_id
        action = self._load_action_record(action_id, pid)
        if action is None:
            raise ValueError("no such action_id: %r" % (action_id,))
        if not isinstance(observed_state, dict) or not observed_state:
            raise ValueError("observed_state must be a non-empty dict")
        observations = self._observations_for(action_id, pid)
        sequence = len(observations)
        obs_id, doc_evidence = self._record_observation(
            action_id, dict(observed_state), OBSERVED,
            source or "manual_observation", pid,
            context_id=action.provenance.context_id,
            sequence=sequence)
        return {
            "observation_id": obs_id,
            "action_id": action_id,
            "project_id": pid,
            "status": OBSERVED,
            "source": source or "manual_observation",
            "observed_state": dict(observed_state),
            "sequence": sequence,
            "evidence_ids": sorted(doc_evidence),
        }

    def verify_action(self, action_id, expectations=None, project_id=None):
        """Deterministic VERIFICATION of an action against expectations."""
        pid = project_id or self.project_id
        action = self._load_action_record(action_id, pid)
        if action is None:
            raise ValueError("no such action_id: %r" % (action_id,))
        action_content = action.content or {}
        expectations = dict(expectations or {})
        observations = self._observations_for(action_id, pid)
        claims = []
        documented = []
        for obs in observations:
            observed_state = (obs.content or {}).get("observed_state") or {}
            for key in sorted(observed_state.keys()):
                claims.append({
                    "claim_key": key,
                    "value": observed_state[key],
                    "source": obs.record_id,
                })
            documented.extend(obs.provenance.evidence_ids
                              or (obs.content or {}).get("evidence_ids") or ())
        result = verify(expected_conditions=expectations,
                        observed_claims=claims,
                        documented_evidence=documented)
        verification_id = derive_verification_id(action_id, expectations)
        lstore = self._lifecycle_store()
        lstore.save(LifecycleRecord(
            record_id=verification_id,
            role=RecordRole.VERIFICATION,
            provenance=Provenance(
                record_id=verification_id,
                origin=Origin.DERIVED,
                source="verification.verify",
                actor=action.provenance.actor,
                timestamp=_now(),
                project=pid,
                context_id=action.provenance.context_id,
                evidence_ids=result["documented_evidence"],
                lifecycle_state=LifecycleState.ACTIVE,
                parent_record_ids=(action_id,) + tuple(
                    obs.record_id for obs in observations),
                derived_from=(action_id,),
                created_at=_now(),
                updated_at=_now(),
            ),
            subject="verification %s for action %s" % (result["result"],
                                                       action_id),
            content={
                "action_id": action_id,
                "expectations": dict(expectations),
                "observed_claims": claims,
                "observation_ids": [o.record_id for o in observations],
                "result": result["result"],
                "rationale": result["rationale"],
                "evidence_ids": result["documented_evidence"],
                "plan_step_id": action_content.get("plan_step_id"),
            },
        ))
        lstore.close()
        return {
            "verification_id": verification_id,
            "action_id": action_id,
            "project_id": pid,
            "result": result["result"],
            "expectations": dict(expectations),
            "observation_ids": [o.record_id for o in observations],
            "rationale": result["rationale"],
            "evidence_ids": result["documented_evidence"],
        }

    @staticmethod
    def _classification_from_verification(result):
        mapping = {
            VERIFIED_SUCCESS: OutcomeClassification.SUCCESS,
            VERIFIED_FAILURE: OutcomeClassification.FAILURE,
            PARTIAL: OutcomeClassification.PARTIAL,
            INSUFFICIENT_EVIDENCE: OutcomeClassification.UNKNOWN,
            CONFLICTING_EVIDENCE: OutcomeClassification.UNKNOWN,
            UNKNOWN: OutcomeClassification.UNKNOWN,
        }
        return mapping.get(result, OutcomeClassification.UNKNOWN)

    def _deny_evidence(self, action_id, plan_step_id, decision, project_id,
                       context_id=None):
        evidence_id = self._denial_evidence_id(action_id, plan_step_id,
                                               decision)
        estore = self._evidence_store()
        try:
            existing = estore.get(evidence_id)
        finally:
            estore.close()
        if existing is not None:
            return evidence_id
        return self.record_evidence(
            source_observation_id=action_id,
            claim="action denied by authority: %s" % decision,
            evidence_type="fact", supporting_data={
                "decision": decision, "plan_step_id": plan_step_id},
            project_id=project_id, context_id=context_id)["evidence_id"]

    def finalize_action(self, action_id, task_type=None, domain=None,
                        strategy_id=None, project_id=None):
        """Convert an action's verified reality into OUTCOME + EXPERIENCE.

        A VERIFIED_SUCCESS outcome is only produced when VERIFICATION has a
        documented verification evidence id. Missing/insufficient/conflicting
        verification and denied actions are preserved as unknown/blocked —
        never silently converted into success.
        """
        pid = project_id or self.project_id
        action = self._load_action_record(action_id, pid)
        if action is None:
            raise ValueError("no such action_id: %r" % (action_id,))
        action_content = action.content or {}
        plan_id = action_content.get("plan_id")
        plan_step_id = action_content.get("plan_step_id")
        plan = self._load_plan_meta(plan_id, pid)
        context_id = plan.provenance.context_id if plan else None
        situation = (plan.content or {}).get("situation") if plan else "n/a"
        attempt = action_content.get("registered_effect") or "action"
        verification_id = None
        verifications = self._verifications_for(action_id, pid)

        status = action_content.get("execution_status")
        verification_result = None
        if status == DENIED:
            decision = action_content.get("authority_decision") or DENIED
            evidence_ids = [self._deny_evidence(
                action_id, plan_step_id, decision, pid, context_id)]
            classification = OutcomeClassification.BLOCKED
            verification_result = DENIED
        else:
            verifications = [v for v in verifications if
                             (v.content or {}).get("result") in
                             VERIFICATION_RESULTS]
            if verifications:
                latest = verifications[-1]
                verification_id = latest.record_id
                verification_result = (latest.content or {}).get("result")
                evidence_ids = sorted(latest.provenance.evidence_ids
                                      or (latest.content or {}).get(
                                          "evidence_ids") or ())
                if not evidence_ids:
                    # A non-success verification may carry no documented
                    # evidence; the outcome then documents its absence.
                    evidence_ids = self._unverified_evidence(
                        action_id, pid, context_id)
            else:
                verification_result = INSUFFICIENT_EVIDENCE
                evidence_ids = self._unverified_evidence(
                    action_id, pid, context_id)
            classification = self._classification_from_verification(
                verification_result)
        outcome = self.record_outcome(
            classification, evidence_ids=evidence_ids, plan_id=plan_id,
            context_id=context_id, project_id=pid,
            verification_id=verification_id)
        result_text = str(classification.value)
        experience = self.record_experience(
            situation=situation, attempt=attempt, result=result_text,
            context_id=context_id, evidence_ids=evidence_ids,
            outcome_id=outcome["outcome_id"], task_type=task_type,
            domain=domain, strategy_id=strategy_id, actor=action.provenance.actor,
            source="observed", project_id=pid)
        return {
            "action_id": action_id,
            "verification_id": verification_id,
            "verification_result": verification_result,
            "plan_id": plan_id,
            "plan_step_id": plan_step_id,
            "classification": result_text,
            "evidence_ids": sorted(evidence_ids),
            "outcome": outcome,
            "experience": experience,
        }

    def _unverified_evidence(self, action_id, project_id, context_id=None):
        evidence_id = "ev_" + hashlib.sha256(_canonical({
            "action_id": action_id,
            "claim": "verification evidence absent",
        }).encode("utf-8")).hexdigest()[:32]
        estore = self._evidence_store()
        try:
            existing = estore.get(evidence_id)
        finally:
            estore.close()
        if existing is not None:
            return [evidence_id]
        rec = self.record_evidence(
            source_observation_id=action_id,
            claim="verification evidence absent for action %s" % action_id,
            evidence_type="fact",
            supporting_data={"action_id": action_id},
            project_id=project_id, context_id=context_id)
        return [rec["evidence_id"]]

    def execute_plan(self, plan_id, plan_step_ids, actor, policy=None,
                     request_id=None, verification=None, task_type=None,
                     domain=None, strategy_id=None, project_id=None):
        """Canonical end-to-end safe execution of plan steps.

        Per step: AUTHORITY/APPROVAL -> ACTION -> OBSERVATION ->
        VERIFICATION -> OUTCOME -> EXPERIENCE. Unapproved or unknown
        authority blocks execution; verified success requires evidence;
        failures/partials/unknowns are retained.
        """
        pid = project_id or self.project_id
        plan_id = str(plan_id)
        meta = self._load_plan_meta(plan_id, pid)
        if meta is None:
            raise ValueError("no such plan_id: %r" % (plan_id,))
        plan_step_ids = [str(x) for x in (plan_step_ids or ())]
        known = {str(s.get("step_id")) for s in self._plan_steps(meta)}
        unknown = sorted(set(plan_step_ids) - known)
        if unknown:
            raise ValueError("plan %s has no such steps: %s"
                             % (plan_id, unknown))
        ordered = [sid for sid in self._plan_steps_list(meta)
                   if sid in plan_step_ids]
        steps = ordered or self._plan_steps_list(meta)

        if strategy_id is None:
            decision_id = (meta.content or {}).get("decision_id")
            if decision_id:
                lstore = self._lifecycle_store()
                try:
                    decision_meta = lstore.get(decision_id)
                finally:
                    lstore.close()
                if decision_meta is not None:
                    strategy_id = ((decision_meta.content or {}).get(
                        "applicable_strategy_id")
                        or (decision_meta.content or {}).get(
                        "strategy_id"))
        results = []
        for step_id in steps:
            step_action = self.request_action(
                plan_id, step_id, actor, policy=policy, request_id=request_id,
                project_id=pid)
            verification_result = None
            if step_action.get("execution_status") != DENIED:
                verification_result = self.verify_action(
                    step_action["action_id"], expectations=verification,
                    project_id=pid)
            finalized = self.finalize_action(
                step_action["action_id"], task_type=task_type, domain=domain,
                strategy_id=strategy_id, project_id=pid)
            results.append({
                "plan_step_id": step_id,
                "action": step_action,
                "authority": {
                    "authority_id": step_action.get("authority_id"),
                    "decision": step_action.get("authority_decision"),
                },
                "verification": verification_result,
                "outcome": finalized["outcome"],
                "experience": finalized["experience"],
                "classification": finalized["classification"],
            })
        return {
            "plan_id": plan_id,
            "actor": actor,
            "project_id": pid,
            "steps": results,
            "chain": ["authority", "action", "observation", "verification",
                      "outcome", "experience"],
        }

    def _plan_steps_list(self, meta):
        return [str(s.get("step_id")) for s in self._plan_steps(meta)]

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
            "context_id": entry.get("context_id"),
            "derived_from": list(entry.get("derived_from") or ()) or (
                entry.get("derived_from") or []),
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
                sstore.set_deprecated(record_id, True, _now(), reason)
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
                sstore.set_superseded_by(old_id, new_id, _now(), reason)
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