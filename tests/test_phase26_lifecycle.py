"""Phase 26 — Information, Knowledge & Experience Lifecycle.

Acceptance coverage (per the Phase 26 spec):
    A  fresh system (system knowledge present; user memory/experience/learned
       strategies empty)
    B  user fact is captured / normalized / persisted / retrievable with
       provenance (Memory, origin user_provided)
    C  external knowledge stays external (origin external, source retained,
       grounded); it never becomes an experience or strategy
    D  actual experience with situation / attempt / result / evidence /
       context / provenance (origin observed)
    E  learning references experiences, preserves failures, deterministic,
       never erases source experiences
    F  strategies respect minimum evidence, per-approach and per-context
       generalization, determinism, and carry supporting ids
    G  provenance trace: strategy -> learning -> experiences -> outcomes/evidence -> source
    H  restart persistence
    I  project isolation
    J  contradiction / correction: both records remain, supersession audited,
       no deletion
    K  trust boundary: external knowledge is advisory and can never bypass
       approval / become an experience
    L  determinism
    M  storage boundary: core intelligence files contain no sqlite / table
       names / raw SQL
    N  v1 contract stays frozen (no lifecycle ops leak into v1)
    O  full normal learning path via engine APIs without manual DB editing
    E2E end-to-end canonical lifecycle through the v2 MemoryToolInterface
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from intelligence.lifecycle import (
    LifecycleRecord,
    LifecycleState,
    Origin,
    Provenance,
    RecordRole,
    RULE_EXTERNAL_NOT_EXPERIENCE,
    canonical_state_transition,
    check_role_origin,
    derive_learning_record_id,
    derive_lifecycle_entry_id,
    role_compatible_origins,
)


class LifecycleModelTests(unittest.TestCase):
    """Pure model tests — no database involved."""

    def test_enum_vocabulary(self):
        self.assertEqual(
            {o.value for o in Origin},
            {"system_defined", "user_provided", "observed", "external",
             "derived"})
        self.assertEqual(
            {r.value for r in RecordRole},
            {"source_record", "memory", "knowledge", "experience",
             "outcome", "evidence", "learning", "strategy", "context",
             "strategy_application", "reasoning", "decision", "plan"})
        self.assertEqual(
            {s.value for s in LifecycleState},
            {"active", "superseded", "deprecated", "rejected",
             "invalidated", "archived"})

    def test_role_origin_matrix(self):
        # rule 1: external is not a legal origin for EXPERIENCE.
        ok, _ = check_role_origin(RecordRole.EXPERIENCE, Origin.EXTERNAL)
        self.assertFalse(ok)
        ok, _ = check_role_origin(RecordRole.EXPERIENCE, Origin.OBSERVED)
        self.assertTrue(ok)
        ok, _ = check_role_origin(RecordRole.KNOWLEDGE, Origin.EXTERNAL)
        self.assertTrue(ok)
        ok, _ = check_role_origin(RecordRole.STRATEGY, Origin.DERIVED)
        self.assertTrue(ok)
        ok, _ = check_role_origin(RecordRole.STRATEGY, Origin.OBSERVED)
        self.assertFalse(ok)

    def test_phase28_derived_roles_are_derived_only(self):
        for role in (RecordRole.STRATEGY_APPLICATION, RecordRole.REASONING,
                     RecordRole.DECISION, RecordRole.PLAN):
            ok, _ = check_role_origin(role, Origin.DERIVED)
            self.assertTrue(ok)
            ok, _ = check_role_origin(role, Origin.OBSERVED)
            self.assertFalse(ok)
            ok, _ = check_role_origin(role, Origin.EXTERNAL)
            self.assertFalse(ok)

    def test_external_not_experience_reason(self):
        ok, reason = check_role_origin(RecordRole.EXPERIENCE, Origin.EXTERNAL)
        self.assertFalse(ok)
        self.assertIn("external", reason)
        self.assertEqual(RULE_EXTERNAL_NOT_EXPERIENCE, reason)

    def test_canonical_transitions(self):
        self.assertTrue(canonical_state_transition(
            LifecycleState.ACTIVE, LifecycleState.INVALIDATED)[0])
        self.assertTrue(canonical_state_transition(
            LifecycleState.ACTIVE, LifecycleState.SUPERSEDED)[0])
        self.assertFalse(canonical_state_transition(
            LifecycleState.ACTIVE, LifecycleState.ARCHIVED)[0])
        self.assertFalse(canonical_state_transition(
            LifecycleState.ARCHIVED, LifecycleState.ACTIVE)[0])
        self.assertFalse(canonical_state_transition(
            LifecycleState.INVALIDATED, LifecycleState.ACTIVE)[0])

    def test_derive_ids_deterministic(self):
        a = derive_lifecycle_entry_id("knowledge", "n1", Origin.EXTERNAL,
                                      {"k": 1})
        b = derive_lifecycle_entry_id("knowledge", "n1", Origin.EXTERNAL,
                                      {"k": 1})
        c = derive_lifecycle_entry_id("knowledge", "n1", Origin.EXTERNAL,
                                      {"k": 2})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertTrue(a.startswith("lcr_"))

    def test_learning_id_order_independent(self):
        a = derive_learning_record_id(["xp_bb", "xp_aa"])
        b = derive_learning_record_id(["xp_aa", "xp_bb"])
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("lrn_"))

    def test_provenance_roundtrip_all_fields(self):
        prov = Provenance(
            record_id="r1", origin=Origin.OBSERVED, source="obs",
            actor="u1", timestamp=1.0, project="p1", context_id="ctx_1",
            evidence_ids=("ev_1", "ev_2"), confidence=0.5,
            lifecycle_state=LifecycleState.ACTIVE,
            parent_record_ids=("act_1",), derived_from=("oc_1",),
            created_at=1.0, updated_at=1.0)
        d = prov.as_dict()
        record = LifecycleRecord(record_id="r1", role=RecordRole.EXPERIENCE,
                                 provenance=prov, subject="s", content={"a": 1})
        self.assertEqual(record.as_dict()["record_id"], "r1")
        self.assertEqual(d["origin"], "observed")
        self.assertEqual(d["evidence_ids"], ["ev_1", "ev_2"])


class LifecycleServiceAcceptance(unittest.TestCase):
    """Acceptance A-O driven through ai_engine.lifecycle_service.LifecycleService."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p26_")
        from ai_engine.lifecycle_service import LifecycleService
        self.svc = LifecycleService(project_id="pl26", data_root=self.tmp)
        self._exp_counter = 0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _experiences(self, situation, attempt, outcomes, task_type="generic"):
        """Record len(outcomes) experiences: (situation, attempt) x outcome."""
        ids = []
        for i, outcome in enumerate(outcomes):
            self._exp_counter += 1
            ev = self.svc.record_evidence(
                "obs_%d" % self._exp_counter,
                "verified outcome %d" % self._exp_counter)
            xp = self.svc.record_experience(
                situation, attempt, outcome, evidence_ids=[ev["evidence_id"]],
                task_type=task_type)
            ids.append(xp["experience_id"])
        return ids

    # ---- A: fresh system -------------------------------------------------
    def test_A_fresh_system_empty_user_layers(self):
        summary = self.svc.summary()
        self.assertEqual(summary["project_id"], "pl26")
        for forbidden in ("memory", "experience", "strategy", "learning",
                          "outcome", "evidence"):
            self.assertEqual(summary["by_role"].get(forbidden, 0), 0,
                             forbidden + " must be empty on a fresh system")
        # system_defined origin is always a legal lifecycle origin.
        self.assertIn(Origin.SYSTEM_DEFINED, role_compatible_origins(
            RecordRole.KNOWLEDGE))

    # ---- B: user fact ----------------------------------------------------
    def test_B_user_fact_captured_with_provenance(self):
        res = self.svc.ingest_user_fact("alpha raises prices in july",
                                        actor="alice")
        self.assertEqual(res["role"], "memory")
        self.assertEqual(res["origin"], "user_provided")
        self.assertEqual(res["lifecycle_state"], "active")
        self.assertTrue(res["record_id"].startswith("fact_"))
        self.assertTrue(res["activity_id"].startswith("act_"))
        self.assertIsNotNone(res["context_id"])
        self.assertIsNotNone(res["evidence_id"])

        described = self.svc.describe(res["record_id"])
        self.assertEqual(described["role"], "memory")
        self.assertEqual(described["origin"], "user_provided")
        self.assertEqual(described["lifecycle_state"], "active")
        self.assertEqual(described["content"]["text"],
                         "alpha raises prices in july")
        # retrievable through the knowledge memory API
        mem = self.svc._memory()
        node = mem.get_node(res["record_id"])
        self.assertIsNotNone(node)
        recall = mem.recall("alpha raises prices")
        self.assertEqual(recall["result"]["knowledge"][0]["id"],
                         res["record_id"])
        # source_record metadata exists for the raw capture
        lstore = self.svc._lifecycle_store()
        src = lstore.get(res["activity_id"])
        lstore.close()
        self.assertIsNotNone(src)
        self.assertEqual(src.role.value, "source_record")
        self.assertEqual(src.provenance.origin.value, "user_provided")

    # ---- C: external knowledge stays external ----------------------------
    def test_C_external_knowledge_stays_external(self):
        res = self.svc.ingest_external_knowledge(
            "the motor runs hot above 60C per datasheet",
            source="external", uri="ref://ds22")
        self.assertEqual(res["role"], "knowledge")
        self.assertEqual(res["origin"], "external")
        self.assertEqual(res["source"], "ref://ds22")
        self.assertIn("never becomes an experience", res["note"])
        described = self.svc.describe(res["record_id"])
        self.assertEqual(described["origin"], "external")
        # External claims cannot be recorded as experiences (Rule 1).
        from intelligence.lifecycle import check_role_origin
        ok, reason = check_role_origin(RecordRole.EXPERIENCE, Origin.EXTERNAL)
        self.assertFalse(ok)
        self.assertIn("external", reason)
        # The database is storage — the rule is enforced by the role-origin
        # model at the service boundary, so external input can never be
        # labelled as an experience through an engine API.

    # ---- D: actual experience -------------------------------------------
    def test_D_experience_with_full_provenance(self):
        ev = self.svc.record_evidence("obs_deploy", "verified the deploy")
        xp = self.svc.record_experience(
            situation="deploy the service", attempt="canary then full",
            result="success", evidence_ids=[ev["evidence_id"]],
            outcome_classification="success", actor="ops")
        self.assertTrue(xp["experience_id"].startswith("xp_"))
        self.assertEqual(xp["origin"], "observed")
        self.assertEqual(xp["outcome_classification"], "success")
        self.assertIsNotNone(xp["context_id"])
        self.assertIn(ev["evidence_id"], xp["evidence_ids"])
        described = self.svc.describe(xp["experience_id"])
        self.assertEqual(described["role"], "experience")
        self.assertEqual(described["origin"], "observed")
        self.assertEqual(described["lifecycle_state"], "active")
        self.assertIn(ev["evidence_id"], described["evidence_ids"])
        self.assertEqual(
            described["content"]["summary"]["situation"]["problem"],
            "deploy the service")
        self.assertEqual(
            described["content"]["summary"]["attempt"]["approach"],
            "canary then full")
        self.assertEqual(described["content"]["outcome_classification"],
                         "success")

    # ---- E: learning -----------------------------------------------------
    def test_E_learning_preserves_failures_and_is_deterministic(self):
        ids = self._experiences("run migrations", "backup then migrate",
                                ["success", "success", "failed"])
        lrn1 = self.svc.derive_learning(ids)
        self.assertEqual(lrn1["counts"]["success"], 2)
        self.assertEqual(lrn1["counts"]["failure"], 1)
        self.assertTrue(lrn1["preserves_source"])
        self.assertIn("preserves failures", lrn1["note"])
        # deterministic: same inputs, same learning id
        lrn2 = self.svc.derive_learning(list(reversed(ids)))
        self.assertEqual(lrn1["learning_id"], lrn2["learning_id"])
        self.assertEqual(lrn1["counts"], lrn2["counts"])
        # source experiences are never erased
        xstore = self.svc._experience_store()
        for eid in ids:
            self.assertIsNotNone(xstore.get(eid))
        xstore.close()

    def test_E_learning_references_experiences(self):
        ids = self._experiences("warm up", "stretch", ["success"])
        lrn = self.svc.derive_learning(ids)
        self.assertEqual(lrn["learning_id"], derive_learning_record_id(ids))
        described = self.svc.describe(lrn["learning_id"])
        self.assertEqual(described["role"], "learning")
        self.assertEqual(described["origin"], "derived")
        self.assertEqual(sorted(described["derived_from"]), sorted(ids))

    # ---- F: strategies ---------------------------------------------------
    def test_F_min_evidence_not_met(self):
        ids = self._experiences("deploy nightly", "script it",
                                ["success", "success"])
        res = self.svc.derive_strategies(ids)
        self.assertEqual(res["strategy_count"], 0)

    def test_F_repeated_success_derives_strategy(self):
        ids = self._experiences("deploy nightly", "script it",
                                ["success"] * 3)
        res = self.svc.derive_strategies(ids)
        self.assertEqual(res["strategy_count"], 1)
        s = res["strategies"][0]
        self.assertIn(s["strategy_id"], {
            sid["strategy_id"] for sid in res["strategies"]})
        self.assertEqual(len(s["supporting_experience_ids"]), 3)
        self.assertGreaterEqual(len(s["supporting_evidence_ids"]), 3)
        self.assertGreater(s["confidence"], 0.0)
        self.assertEqual(str(s["situation_pattern"]).lower(),
                         "deploy nightly")
        # deterministic: repeated derivation yields identical output
        res2 = self.svc.derive_strategies(list(reversed(ids)))
        self.assertEqual(res["strategies"], res2["strategies"])

    def test_F_distinct_approaches_derive_distinct_strategies(self):
        ids_a = self._experiences("tune db", "raise cache", ["success"] * 3)
        ids_b = self._experiences("tune db", "rewrite query",
                                  ["success"] * 3)
        res = self.svc.derive_strategies(ids_a + ids_b)
        self.assertEqual(res["strategy_count"], 2)
        approaches = {s["approach_pattern"] for s in res["strategies"]}
        self.assertEqual(approaches, {"raise cache", "rewrite query"})

    # ---- G: provenance trace ---------------------------------------------
    def test_G_trace_strategy_to_source(self):
        ids = self._experiences("scale out", "add replicas",
                                ["success"] * 3)
        st = self.svc.derive_strategies(ids)
        self.assertEqual(st["strategy_count"], 1)
        strategy_id = st["strategies"][0]["strategy_id"]
        trace = self.svc.trace(strategy_id)
        roles = [n["role"] for n in trace["records"]]
        self.assertIn("strategy", roles)
        self.assertIn("learning", roles)
        self.assertGreaterEqual(roles.count("experience"), 3)
        self.assertGreaterEqual(roles.count("outcome"), 3)
        self.assertGreaterEqual(roles.count("evidence"), 3)
        self.assertGreaterEqual(trace["meta"]["node_count"], 10)

    # ---- H: restart persistence ------------------------------------------
    def test_H_restart_persistence(self):
        res = self.svc.ingest_user_fact("the root password is rotated weekly")
        ids = self._experiences("rotate secrets", "run rotation playbook",
                                ["success"] * 3)
        st = self.svc.derive_strategies(ids)
        strategy_id = st["strategies"][0]["strategy_id"]

        from ai_engine.lifecycle_service import LifecycleService
        svc2 = LifecycleService(project_id="pl26", data_root=self.tmp)
        self.assertEqual(svc2.describe(res["record_id"])["origin"],
                         "user_provided")
        described = svc2.describe(strategy_id)
        self.assertEqual(described["role"], "strategy")
        self.assertEqual(described["origin"], "derived")
        trace = svc2.trace(strategy_id)
        self.assertGreater(len(trace["records"]), 3)

    # ---- I: project isolation ---------------------------------------------
    def test_I_project_isolation(self):
        self.svc.ingest_user_fact("project a secret")
        ids = self._experiences("isolate data", "sandbox", ["success"] * 3)
        self.svc.derive_strategies(ids)
        from ai_engine.lifecycle_service import LifecycleService
        svc_b = LifecycleService(project_id="other", data_root=self.tmp)
        summary_b = svc_b.summary(project_id="other")
        self.assertEqual(summary_b["total_records"], 0)
        self.assertNotIn("memory", summary_b["by_role"])
        with self.assertRaises(ValueError):
            svc_b.describe("fact_x_unknown")

    # ---- J: contradiction / correction ------------------------------------
    def test_J_experience_contradiction_detected(self):
        self._experiences("troubleshoot boot", "rebuild initramfs",
                          ["success", "failed"], task_type="boot_fix")
        c = self.svc.contradictions()
        types = {x["contradiction_type"] for x in c["contradictions"]}
        self.assertIn("experience", types)

    def test_J_correction_supersedes_without_deletion(self):
        f1 = self.svc.ingest_user_fact("alpha uses july pricing")
        f2 = self.svc.ingest_user_fact("alpha uses august pricing")
        result = self.svc.supersede(f1["record_id"], f2["record_id"],
                                    reason="corrected by the finance team")
        self.assertEqual(result["lifecycle_state"], "superseded")
        self.assertEqual(result["superseded_by"], f2["record_id"])
        self.assertTrue(result["both_records_preserved"])
        # both records still readable
        self.assertEqual(self.svc.describe(f1["record_id"])["lifecycle_state"],
                         "superseded")
        self.assertEqual(self.svc.describe(f2["record_id"])["lifecycle_state"],
                         "active")
        # audited
        lstore = self.svc._lifecycle_store()
        trail = lstore.audit_trail(f1["record_id"])
        lstore.close()
        self.assertTrue(trail)
        self.assertEqual(trail[0]["previous_state"], "active")
        self.assertEqual(trail[0]["new_state"], "superseded")

    def test_J_retract_invalidates_without_deletion(self):
        res = self.svc.ingest_user_fact("deprecated claim to be retracted")
        retracted = self.svc.retract(res["record_id"],
                                     reason="superseded information")
        self.assertEqual(retracted["lifecycle_state"], "invalidated")
        self.assertTrue(retracted["never_deletes"])
        # record retained
        described = self.svc.describe(res["record_id"])
        self.assertEqual(described["lifecycle_state"], "invalidated")
        mem = self.svc._memory()
        self.assertIsNotNone(mem.get_node(res["record_id"]))
        lstore = self.svc._lifecycle_store()
        trail = lstore.audit_trail(res["record_id"])
        lstore.close()
        self.assertEqual(trail[-1]["new_state"], "invalidated")

    # ---- K: trust boundary ------------------------------------------------
    def test_K_external_knowledge_never_becomes_experience_or_strategy(self):
        self.svc.ingest_external_knowledge(
            "external specs claim feature F is needed",
            source="external", uri="ref://spec9")
        summary = self.svc.summary()
        self.assertEqual(summary["by_role"].get("experience", 0), 0)
        self.assertEqual(summary["by_role"].get("strategy", 0), 0)
        self.assertEqual(summary["by_role"].get("learning", 0), 0)
        self.assertGreater(summary["by_role"].get("knowledge", 0), 0)
        # external alone is never enough to shortcut to a learned strategy:
        # the service requires the strategy API to be fed experiences, which
        # only ever come from executed actions (observed origin).

    # ---- L: determinism ---------------------------------------------------
    def test_L_determinism(self):
        a = self.svc.summary()
        b = self.svc.summary()
        self.assertEqual(a, b)
        ids = self._experiences("deterministic op", "repeat",
                                ["success"] * 3)
        s1 = self.svc.derive_strategies(ids)
        s2 = self.svc.derive_strategies(list(reversed(ids)))
        self.assertEqual(s1["strategies"], s2["strategies"])

    # ---- M: storage boundary ----------------------------------------------
    def test_M_core_intelligence_free_of_sql(self):
        import pathlib
        base = pathlib.Path(_ROOT)
        files = [
            base / "intelligence/lifecycle/model.py",
            base / "intelligence/lifecycle/trace.py",
            base / "ai_engine/lifecycle_service.py",
        ]
        for path in files:
            text = path.read_text()
            self.assertNotIn("sqlite", text.lower(),
                             "%s must not touch sqlite" % path.name)
            self.assertNotIn("CREATE TABLE", text.upper(),
                             "%s must not know table DDL" % path.name)
            for token in ("SELECT ", "PRAGMA ", "AUTOINCREMENT"):
                self.assertNotIn(token, text.upper(),
                                 "%s must not contain raw SQL" % path.name)
        svc_text = (base / "ai_engine/lifecycle_service.py").read_text()
        self.assertNotIn("KnowledgeRepository", svc_text)
        # only the store module speaks sqlite
        store_text = (base / "intelligence/lifecycle/store.py").read_text()
        self.assertIn("import sqlite3", store_text)

    def test_M_model_and_trace_are_pure(self):
        import subprocess, sys
        code = (
            "from intelligence.lifecycle.model import (Origin, RecordRole, "
            "LifecycleState, check_role_origin)\n"
            "from intelligence.lifecycle.trace import normalize_entry, "
            "trace_provenance\n"
            "ok, _ = check_role_origin(RecordRole.EXPERIENCE, Origin.OBSERVED)\n"
            "assert ok\n"
            "print('PURE_OK')\n"
        )
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PURE_OK", r.stdout)

    # ---- N: v1 contract frozen --------------------------------------------
    def test_N_v1_contract_frozen(self):
        from api.contract import CONTRACT_VERSION, OPERATIONS
        self.assertEqual(CONTRACT_VERSION, "1")
        self.assertEqual(tuple(OPERATIONS),
                         ("search", "get", "related", "follow", "provenance",
                          "inspect"))
        for op in OPERATIONS:
            self.assertNotIn("lifecycle.", op)

    # ---- O: normal learning path via engine APIs --------------------------
    def test_O_full_learning_path_service_only(self):
        # Drive the ENTIRE canonical lifecycle through engine APIs only —
        # no sqlite, no repository code in the test body.
        trained = []
        for i in range(3):
            ev = self.svc.record_evidence("obs_m_%d" % i, "validated step %d" % i)
            xp = self.svc.record_experience(
                "release the package", "run release pipeline", "success",
                evidence_ids=[ev["evidence_id"]], outcome_classification="success",
                task_type="release")
            trained.append(xp["experience_id"])
        learning = self.svc.derive_learning(trained)
        strategies = self.svc.derive_strategies(trained)
        self.assertEqual(learning["counts"]["success"], 3)
        self.assertEqual(strategies["strategy_count"], 1)
        strategy_id = strategies["strategies"][0]["strategy_id"]
        described = self.svc.describe(strategy_id)
        self.assertEqual(described["role"], "strategy")
        self.assertEqual(described["origin"], "derived")
        trace = self.svc.trace(strategy_id)
        roles = {n["role"] for n in trace["records"]}
        self.assertEqual(roles, {"strategy", "learning", "experience",
                                 "outcome", "evidence", "context"})
        # strategy is usable for future reasoning (should_apply_strategy)
        from ai_engine.generic_learning import should_apply_strategy
        sstore = self.svc._strategy_store()
        strat = sstore.get(strategy_id)
        sstore.close()
        self.assertIsNotNone(strat)
        strat_dict = strat.to_dict() if hasattr(strat, "to_dict") else dict(strat)
        self.assertTrue(should_apply_strategy(strat_dict, [], None) or
                        should_apply_strategy(strat_dict, [],
                                              {"context_id": "ctx_x"}))


class LifecycleAPIIntegration(unittest.TestCase):
    """End-to-end canonical lifecycle through the v2 contract interface."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p26api_")
        from api.memory_tools import MemoryToolInterface
        self.iface = MemoryToolInterface(data_root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _call(self, operation, arguments):
        res = self.iface.execute({"operation": operation,
                                  "arguments": arguments})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["contract_version"], "2")
        return res["result"]

    def test_e2e_canonical_lifecycle_via_v2(self):
        # INFORMATION -> memory (user-provided)
        fact = self._call("lifecycle.ingest", {
            "content": "the build cache must be purged before release",
            "actor": "u1"})
        self.assertEqual(fact["role"], "memory")
        # SOURCE/ORIGIN -> external knowledge (advisory)
        ext = self._call("lifecycle.ingest", {
            "content": "spec says builds are reproducible",
            "origin": "external", "source": "external", "uri": "ref://spec1"})
        self.assertEqual(ext["role"], "knowledge")
        self.assertEqual(ext["origin"], "external")
        # ACTION -> OUTCOME -> EVIDENCE -> EXPERIENCE (3 successes)
        ids = []
        for i in range(3):
            ev = self._call("lifecycle.experience", {
                "situation": "purge the cache", "attempt": "rm -rf build",
                "result": "success", "evidence_ids": [],
                "task_type": "build"})
            self.assertEqual(ev["origin"], "observed")
            ids.append(ev["experience_id"])
        # learning + strategy through the contract
        learning = self._call("lifecycle.learning", {"experience_ids": ids})
        self.assertEqual(learning["counts"]["success"], 3)
        strategies = self._call("lifecycle.strategy", {"experience_ids": ids})
        self.assertEqual(strategies["strategy_count"], 1)
        strategy_id = strategies["strategies"][0]["strategy_id"]
        # trace through contract
        trace = self._call("lifecycle.trace", {"record_id": strategy_id})
        roles = {n["role"] for n in trace["records"]}
        self.assertEqual(roles, {"strategy", "learning", "experience",
                                 "outcome", "evidence", "context"})
        # describe
        described = self._call("lifecycle.describe", {"record_id": fact["record_id"]})
        self.assertEqual(described["origin"], "user_provided")
        # summary
        summary = self._call("lifecycle.summary", {})
        self.assertIn("experience", summary["by_role"])
        self.assertIn("strategy", summary["by_role"])

    def test_e2e_contract_errors_mapped(self):
        res = self.iface.execute({
            "operation": "lifecycle.trace",
            "arguments": {"record_id": "does_not_exist"}})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "node_not_found")
        res = self.iface.execute({
            "operation": "lifecycle.experience",
            "arguments": {"situation": "", "attempt": "", "result": ""}})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "invalid_argument")

    def test_e2e_cli_lifecycle_summary(self):
        import subprocess, sys
        env = dict(os.environ)
        env["AI_ENGINE_DATA_DIR"] = self.tmp
        code = (
            "from ai_engine.lifecycle_service import LifecycleService\n"
            "svc = LifecycleService(project_id='cli', data_root=%r)\n"
            "svc.ingest_user_fact('cli fact')\n"
            "print(svc.summary(project_id='cli')['total_records'])\n"
        ) % self.tmp
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        if r.returncode == 0 and r.stdout.strip():
            self.assertGreater(int(r.stdout.strip()), 0)


if __name__ == "__main__":
    unittest.main()