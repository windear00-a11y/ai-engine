"""Phase 30 — final backend hardening & completeness audit guard.

New tests only: this suite hardens and proves the backend-complete definition
without adding production features. Coverage maps to the Phase 30 checkpoint:

  A. closed-loop end-to-end scenario — acquisition -> context -> persist ->
     (recall) -> reasoning -> decision -> plan -> authority/approval ->
     action -> observation -> verification -> outcome -> experience ->
     learning -> strategy -> later applicable strategy application ->
     provenance trace.
  B. negative branch — denied / failed / partial / insufficient-evidence /
     unsupported-effect / unknown authority stay non-successful.
  C. determinism — the same scenario against fresh stores yields identical
     derived IDs and results.
  D. idempotency / retry — duplicate requests do not duplicate records.
  F. project isolation — no cross-project execution or provenance traversal.
  K. persistence boundary — raw sqlite3 stays below the storage boundary.
  L. API / CLI contract inventories — v1 frozen, v2 exact (17 ops), handlers
     1:1, canonical roles exact (18), docs consistent.
  N. dependency guards — no network/LLM/coding-era imports, and production
     never imports the archived/test-only legacy layer.
"""

import os
import pathlib
import re
import shutil
import tempfile
import unittest

_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Production directories scanned for dependency guards (tmp/, benchmarks/,
# tests/, generated dirs are intentionally excluded from the guards).
_PROD_DIRS = [
    "ai_engine", "api", "intelligence", "retrieval",
    "knowledge_client", "http_server", "importing", "ingestion",
    "external_import", "external_http_client", "tools",
]


def _prod_files():
    files = []
    for d in _PROD_DIRS:
        base = _ROOT / d
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [
                x for x in dirnames if x not in ("__pycache__", ".pytest_cache")]
            for name in filenames:
                if name.endswith(".py"):
                    files.append(pathlib.Path(dirpath) / name)
    return files


def _sqlite3_users():
    return [p for p in _prod_files()
            if re.search(r"^\s*(import sqlite3|from sqlite3\b)",
                         p.read_text(encoding="utf-8"), re.M)]


def _import_lines(path):
    text = path.read_text(encoding="utf-8")
    return [line for line in text.splitlines()
            if re.match(r"\s*(import|from)\s+", line)]


def _is_archive_member(rel):
    """Files that ARE the archived/test-only layer (their self-imports and
    legacy-era docstring references are expected and not guarded)."""
    parts = rel.split("/")
    if len(parts) >= 2 and parts[0] == "intelligence" and parts[1] in (
            "loop", "learning", "decision", "api"):
        return True
    if rel in ("retrieval/ranked_knowledge.py", "retrieval/ranked_experience.py"):
        return True
    if rel in ("intelligence/experience/__init__.py",
               "intelligence/outcome/__init__.py"):
        return True
    if rel.startswith("ai_engine/") and parts[-1] in (
            "lifecycle.py", "runtime.py", "registry.py", "effect.py",
            "verifier.py", "generic_planner.py"):
        return True
    if rel in ("intelligence/experience/extractor.py",
               "intelligence/outcome/extractor.py"):
        return True
    return False


class Phase30ClosedLoopTests(unittest.TestCase):
    """A. Deterministic closed-loop end to end."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p30_")
        from ai_engine.lifecycle_service import LifecycleService
        self.svc = LifecycleService(project_id="p30a", data_root=self.tmp)
        self.svc_other = LifecycleService(project_id="p30b", data_root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _experiences(self, situation, attempt, outcome="success", n=3):
        ids = []
        for i in range(n):
            ev = self.svc.record_evidence("obs_seed_%d" % i, "seed %d" % i)
            xp = self.svc.record_experience(
                situation, attempt, outcome,
                evidence_ids=[ev["evidence_id"]])
            ids.append(xp["experience_id"])
        return ids

    def _execute(self, plan, executors):
        step_id = plan["ordered_steps"][0]["step_id"]
        au = self.svc.evaluate_authority(plan["plan_id"], [step_id], "alice")
        if au["decision"] == "requires_approval":
            self.svc.grant_approval(plan["plan_id"], [step_id], "alice")
        return self.svc.request_action(
            plan["plan_id"], step_id, "alice", request_id="req-CL",
            executors=executors)

    def test_full_closed_loop_traces_and_reapplies_strategy(self):
        # Acquisition + experience (persist, with context + evidence links).
        ids = self._experiences("deploy the service", "deploy the release")
        e2e = self.svc.plan_from_experiences("deploy the service", ids)
        # Recall -> reasoning -> decision -> plan in the canonical chain.
        self.assertEqual(
            e2e["chain"],
            ["experience", "learning", "strategy", "strategy_application",
             "reasoning", "decision", "plan"])
        self.assertEqual(e2e["plan_status"], "drafted")
        plan = e2e["plan"]
        self.assertEqual(plan["ordered_steps"][0]["action"],
                         "deploy the release")

        # Authority/approval -> action (with observation) -> verification.
        action = self._execute(plan, {
            "deploy the release": lambda e, i: {
                "status": "success",
                "observed_state": {"deployed": True, "replicas": 3}}})
        self.assertEqual(action["authority_decision"], "approved")
        self.assertEqual(action["execution_status"], "succeeded")
        self.assertTrue(action["observation_ids"])
        verification = self.svc.verify_action(
            action["action_id"],
            expectations={"deployed": True, "replicas": 3})
        self.assertEqual(verification["result"], "verified_success")

        # Outcome + experience reflect verified reality.
        result = self.svc.finalize_action(action["action_id"])
        self.assertEqual(result["classification"], "success")
        xp_id = result["experience"]["experience_id"]

        # Learning -> strategy from the executed experience.
        learning = self.svc.derive_learning([xp_id])
        self.assertTrue(learning["learning_id"].startswith("lrn_"))
        strategies = self.svc.derive_strategies([xp_id] + ids)
        self.assertGreaterEqual(len(strategies["strategies"]), 1)
        strategy_ids = [s["strategy_id"] for s in strategies["strategies"]]
        self.assertIn(xp_id,
                      strategies["strategies"][0]["supporting_experience_ids"])

        # Later applicable strategy retrieval/application under the plan's own
        # context (strategies carry context restrictions, so a missing current
        # context is surfaced as insufficient_evidence rather than assumed).
        app = self.svc.apply_strategy(
            "deploy the service", strategy_ids=strategy_ids,
            context_id=e2e["strategy_application"]["context_id"])
        self.assertEqual(app["status"], "applicable")
        self.assertTrue(app["strategy_id"])

        # Provenance trace covers the whole loop.
        trace = self.svc.trace(xp_id)
        roles = {n["role"] for n in trace["records"]}
        required = {"context", "experience", "learning", "strategy",
                    "strategy_application", "reasoning", "decision", "plan",
                    "authority", "action", "observation",
                    "verification", "outcome", "evidence"}
        self.assertTrue(required <= roles, roles - required)

    def test_experience_records_carry_context_and_evidence(self):
        ev = self.svc.record_evidence("obs_ctx", "claim")
        xp = self.svc.record_experience(
            "situation one", "attempt one", "success",
            evidence_ids=[ev["evidence_id"]])
        described = self.svc.describe(xp["experience_id"])
        self.assertEqual(described["role"], "experience")
        self.assertTrue(described["content"]["evidence_ids"])
        self.assertTrue(described["context_id"])


class Phase30NegativeBranchTests(unittest.TestCase):
    """B. Failure/denial/unknown paths stay non-successful."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p30_")
        from ai_engine.lifecycle_service import LifecycleService
        self.svc = LifecycleService(project_id="p30a", data_root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _plan(self, situation="deploy the service",
              attempt="deploy the release"):
        ids = []
        for i in range(3):
            ev = self.svc.record_evidence("obs_seed_%d" % i, "seed %d" % i)
            xp = self.svc.record_experience(
                situation, attempt, "success",
                evidence_ids=[ev["evidence_id"]])
            ids.append(xp["experience_id"])
        return self.svc.plan_from_experiences(situation, ids)["plan"]

    def _finalize(self, executors, expectations=None, grant=True):
        plan = self._plan()
        step_id = plan["ordered_steps"][0]["step_id"]
        if grant:
            self.svc.grant_approval(plan["plan_id"], [step_id], "alice")
        action = self.svc.request_action(
            plan["plan_id"], step_id, "alice", request_id="req-N",
            executors=executors)
        if expectations is not None:
            self.svc.verify_action(action["action_id"],
                                   expectations=expectations)
        return self.svc.finalize_action(action["action_id"])

    def test_denied_action_is_blocked(self):
        result = self._finalize(executors={}, grant=False)
        self.assertEqual(result["classification"], "blocked")
        self.assertNotEqual(result["classification"], "success")

    def test_failed_outcome_is_never_success(self):
        result = self._finalize(
            executors={"deploy the release": lambda e, i: {
                "status": "failure",
                "observed_state": {"deployed": False}}},
            expectations={"deployed": True})
        self.assertEqual(result["classification"], "failure")

    def test_partial_outcome_is_never_success(self):
        result = self._finalize(
            executors={"deploy the release": lambda e, i: {
                "status": "partial",
                "observed_state": {"deployed": True, "replicas": 1}}},
            expectations={"deployed": True, "replicas": 3})
        self.assertEqual(result["classification"], "partial")

    def test_unsupported_effect_is_explicitly_unknown(self):
        result = self._finalize(executors={}, grant=True)
        self.assertEqual(result["classification"], "unknown")

    def test_unverified_outcome_is_unknown_or_blocked_not_success(self):
        result = self._finalize(
            executors={"deploy the release": lambda e, i: {
                "status": "success",
                "observed_state": {"deployed": True}}},
            expectations=None)
        self.assertNotEqual(result["classification"], "success")

    def test_unknown_authority_cannot_execute(self):
        plan = self._plan(situation="run noop", attempt="noop")
        step_id = plan["ordered_steps"][0]["step_id"]
        action = self.svc.request_action(
            plan["plan_id"], step_id, "alice",
            policy={"trust_level": "remote"})
        self.assertEqual(action["authority_decision"], "unknown")
        self.assertEqual(action["execution_status"], "denied")
        self.assertIs(action["executed"], False)


class Phase30DeterminismTests(unittest.TestCase):
    """C. Identical inputs against fresh stores -> identical results."""

    def test_full_loop_identical_over_fresh_stores(self):
        def run_one(tmp):
            from ai_engine.lifecycle_service import LifecycleService
            svc = LifecycleService(project_id="p30d", data_root=tmp)
            ids = []
            for i in range(3):
                ev = svc.record_evidence("obs_seed_%d" % i, "seed %d" % i)
                xp = svc.record_experience(
                    "deploy the service", "deploy the release", "success",
                    evidence_ids=[ev["evidence_id"]])
                ids.append(xp["experience_id"])
            e2e = svc.plan_from_experiences("deploy the service", ids)
            plan = e2e["plan"]
            step_id = plan["ordered_steps"][0]["step_id"]
            svc.grant_approval(plan["plan_id"], [step_id], "alice")
            action = svc.request_action(
                plan["plan_id"], step_id, "alice", request_id="req-D",
                executors={"deploy the release": lambda e, i: {
                    "status": "success",
                    "observed_state": {"deployed": True, "replicas": 3}}})
            svc.verify_action(action["action_id"],
                              expectations={"deployed": True, "replicas": 3})
            result = svc.finalize_action(action["action_id"])
            return (tuple(sorted(ids)), e2e["learning_id"],
                    tuple(sorted(str(s) for s in e2e["strategy_ids"])),
                    e2e["strategy_application_id"], e2e["reasoning_id"],
                    e2e["decision_id"], e2e["plan_id"], e2e["plan"]["plan_id"],
                    step_id, action["action_id"],
                    result["verification_id"], result["outcome"]["outcome_id"],
                    result["experience"]["experience_id"])

        with tempfile.TemporaryDirectory(prefix="p30_d_") as a, \
                tempfile.TemporaryDirectory(prefix="p30_d_") as b:
            first = run_one(a)
            second = run_one(b)
        self.assertEqual(first, second)


class Phase30IdempotencyTests(unittest.TestCase):
    """D. Duplicate/retried requests do not duplicate records."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p30_")
        from ai_engine.lifecycle_service import LifecycleService
        self.svc = LifecycleService(project_id="p30a", data_root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_same_request_id_returns_same_action(self):
        ids = []
        for i in range(3):
            ev = self.svc.record_evidence("obs_seed_%d" % i, "seed %d" % i)
            xp = self.svc.record_experience(
                "deploy the service", "deploy the release", "success",
                evidence_ids=[ev["evidence_id"]])
            ids.append(xp["experience_id"])
        plan = self.svc.plan_from_experiences(
            "deploy the service", ids)["plan"]
        step_id = plan["ordered_steps"][0]["step_id"]
        self.svc.grant_approval(plan["plan_id"], [step_id], "alice")
        executors = {"deploy the release": lambda e, i: {
            "status": "success",
            "observed_state": {"deployed": True, "replicas": 3}}}
        a1 = self.svc.request_action(plan["plan_id"], step_id, "alice",
                                     request_id="req-I", executors=executors)
        a2 = self.svc.request_action(plan["plan_id"], step_id, "alice",
                                     request_id="req-I", executors=executors)
        self.assertEqual(a1["action_id"], a2["action_id"])
        self.assertEqual(a1["execution_status"], "succeeded")

        a3 = self.svc.request_action(plan["plan_id"], step_id, "alice",
                                     request_id="req-I2", executors=executors)
        self.assertNotEqual(a1["action_id"], a3["action_id"])


class Phase30IsolationTests(unittest.TestCase):
    """F. Project isolation across the loop."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p30_")
        from ai_engine.lifecycle_service import LifecycleService
        self.svc = LifecycleService(project_id="p30a", data_root=self.tmp)
        self.svc_other = LifecycleService(project_id="p30b", data_root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cross_project_plan_and_trace_rejected(self):
        ids = []
        for i in range(3):
            ev = self.svc.record_evidence("obs_seed_%d" % i, "seed %d" % i)
            xp = self.svc.record_experience(
                "deploy the service", "deploy the release", "success",
                evidence_ids=[ev["evidence_id"]])
            ids.append(xp["experience_id"])
        plan = self.svc.plan_from_experiences(
            "deploy the service", ids)["plan"]
        step_id = plan["ordered_steps"][0]["step_id"]
        self.svc.grant_approval(plan["plan_id"], [step_id], "alice")
        action = self.svc.request_action(plan["plan_id"], step_id, "alice")
        self.svc.verify_action(action["action_id"],
                               expectations={"completed": True})
        result = self.svc.finalize_action(action["action_id"])

        au = self.svc_other.evaluate_authority(
            plan["plan_id"], [step_id], "bob")
        self.assertEqual(au["decision"], "invalid_plan")
        with self.assertRaises(ValueError):
            self.svc_other.trace(result["outcome"]["outcome_id"])


class Phase30PersistenceBoundaryTests(unittest.TestCase):
    """K. Raw storage stays below the storage boundary."""

    # Declared storage-boundary files allowed to touch raw sqlite3:
    # declared stores (store.py), read-only legacy extractors, repository /
    # graph report, persistence/backup tooling, importers, journal.
    def _allowed(self, path):
        rel = path.relative_to(_ROOT).as_posix()
        if path.name in ("store.py", "extractor.py"):
            return True
        if path.name == "journal.py" and rel.startswith("tools/permissions/"):
            return True
        if path.name in ("repository.py", "graph_report.py"):
            return rel.startswith("retrieval/")
        if path.name == "events.py" and rel.startswith("intelligence/"):
            return True
        if rel in ("ai_engine/activity.py", "ai_engine/migration.py",
                   "ai_engine/persistence.py"):
            return True
        if rel.startswith("external_import/") and path.name in (
                "apply.py", "dry_run.py", "preview.py", "staging.py"):
            return True
        if rel.startswith("importing/") and path.name in (
                "importer.py", "dry_run.py"):
            return True
        # Blocked-zone policy DATA (literal db paths used as write-guards,
        # not runtime DB access).
        if rel in ("tools/permissions/policy.py",
                   "tools/permissions/pathpolicy.py"):
            return True
        return False

    def test_sqlite3_confined_to_storage_boundary(self):
        users = _sqlite3_users()
        leaks = [str(p.relative_to(_ROOT)) for p in users
                 if not self._allowed(p)]
        self.assertEqual(leaks, [])

    def test_sensitive_layers_never_import_sqlite3(self):
        forbiddden = ("api/", "http_server/", "knowledge_client/",
                      "ingestion/", "external_http_client/")
        offenders = []
        for p in _prod_files():
            rel = p.relative_to(_ROOT).as_posix()
            if any(rel.startswith(d) for d in forbiddden) and \
                    re.search(r"^\s*(import sqlite3|from sqlite3\b)",
                              p.read_text(encoding="utf-8"), re.M):
                offenders.append(rel)
        self.assertEqual(offenders, [])

    def test_no_hardcoded_db_paths_outside_boundaries(self):
        offenders = []
        for p in _prod_files():
            rel = p.relative_to(_ROOT).as_posix()
            if rel == "ai_engine/paths.py":
                continue
            if p.name == "journal.py" and rel.startswith("tools/permissions/"):
                continue
            # Blocked-zone policy DATA (literal db paths used only as
            # write-guard definitions, never runtime DB access).
            if rel in ("tools/permissions/policy.py",
                       "tools/permissions/pathpolicy.py"):
                continue
            if re.search(r"[\"']database/(knowledge|context|evidence|"
                         r"experience|engine_state)\.db[\"']",
                         p.read_text(encoding="utf-8")):
                offenders.append(rel)
        self.assertEqual(offenders, [])


class Phase30DependencyGuardTests(unittest.TestCase):
    """N. No network/LLM/coding-era or archived imports from production."""

    _FIRST_PARTY_FORBIDDEN = (
        r"intelligence\.loop\b",
        r"intelligence\.learning\b",
        r"intelligence\.decision\b",
        r"ai_engine\.(lifecycle\b(?!_service)|runtime|registry|effect|"
        r"verifier|generic_planner)",
        r"\bbenchmarks\b",
        r"intelligence\.(experience|outcome)\.extractor",
    )

    def _archived_importers(self):
        offenders = []
        for p in _prod_files():
            rel = p.relative_to(_ROOT).as_posix()
            if _is_archive_member(rel):
                continue
            for line in _import_lines(p):
                for pat in self._FIRST_PARTY_FORBIDDEN:
                    if re.search(pat, line):
                        offenders.append("%s: %s" % (rel, line.strip()))
                        break
        return offenders

    def test_no_production_import_of_archived_layer(self):
        self.assertEqual(self._archived_importers(), [])

    def test_no_network_or_llm_imports(self):
        third_party = re.compile(
            r"(requests|openai|anthropic|langchain|httpx|aiohttp|"
            r"torch|transformers|flask|fastapi)\b")
        offenders = []
        for p in _prod_files():
            for line in _import_lines(p):
                if third_party.search(line):
                    offenders.append(
                        "%s: %s" % (p.relative_to(_ROOT), line.strip()))
        self.assertEqual(offenders, [])

    def test_no_coding_era_imports_or_paths(self):
        forbidden = (
            (r"(from|import)\s+engine(?:\b|\.|import)", "engine/"),
            (r"(from|import)\s+tools\.coding\b", "tools/coding"),
            (r"(from|import)\s+knowledge_compiler\b", "knowledge_compiler"),
            (r"(from|import)\s+plugins\.code\b", "plugins/code"),
        )
        offenders = []
        for pattern, path_tok in forbidden:
            if path_tok.startswith(("tools/", "engine/", "plugins/")):
                if (_ROOT / path_tok).exists():
                    offenders.append("legacy path present: %s" % path_tok)
            compiled = re.compile(pattern)
            for p in _prod_files():
                rel = p.relative_to(_ROOT).as_posix()
                if _is_archive_member(rel) or rel.startswith("tmp/") or \
                        rel.startswith("benchmarks/"):
                    continue
                for line in _import_lines(p):
                    if compiled.search(line):
                        offenders.append(
                            "%s: %s" % (rel, line.strip()))
        self.assertEqual(offenders, [])

    def test_no_reverse_domain_import_of_api_or_facade(self):
        offenders = []
        intelligence = _ROOT / "intelligence"
        for p in _prod_files():
            rel = p.relative_to(_ROOT).as_posix()
            if not rel.startswith("intelligence/"):
                continue
            if any(rel.startswith("intelligence/" + x + "/")
                   for x in ("loop", "learning", "decision", "reasoning",
                             "api")):
                continue
            for line in _import_lines(p):
                if re.search(r"(from api[. ]|import api\b|from ai_engine\.(?!paths)\w*|import ai_engine\.(?!paths)\w*)", line):
                    offenders.append("%s: %s" % (rel, line.strip()))
        self.assertEqual(offenders, [])


class Phase30InventoryGuardTests(unittest.TestCase):
    """L. Contract inventories stay exact."""

    def test_v1_contract_frozen(self):
        from api.contract import OPERATIONS, CONTRACT_VERSION, \
            DEFAULT_OPERATION_ORDER
        self.assertEqual(CONTRACT_VERSION, "1")
        expected = ["search", "get", "related", "follow", "provenance",
                    "inspect"]
        self.assertEqual(list(OPERATIONS.keys()), expected)
        self.assertEqual(list(DEFAULT_OPERATION_ORDER), expected)

    def test_v2_contract_exact_17_ops_and_handlers(self):
        import api.contract_v2 as v2
        import api.memory_tools as tools
        expected = ["remember", "recall", "get", "provenance", "inspect",
                    "context.get", "lifecycle.ingest", "lifecycle.experience",
                    "lifecycle.learning", "lifecycle.strategy",
                    "lifecycle.trace", "lifecycle.describe",
                    "lifecycle.summary", "lifecycle.plan", "lifecycle.grant",
                    "lifecycle.authorize", "lifecycle.execute"]
        self.assertEqual(list(v2.OPERATIONS.keys()), expected)
        self.assertEqual(list(v2.DEFAULT_OPERATION_ORDER), expected)
        tool = tools.MemoryToolInterface
        for op in expected:
            method = "_op_" + op.replace(".", "_")
            self.assertTrue(hasattr(tool, method), "missing handler %s" % method)

    def test_v2_api_service_methods_present(self):
        import api.memory_api as api
        expected = ["lifecycle_ingest", "lifecycle_experience",
                    "lifecycle_learning", "lifecycle_strategy",
                    "lifecycle_trace", "lifecycle_describe",
                    "lifecycle_summary", "lifecycle_plan", "lifecycle_grant",
                    "lifecycle_authorize", "lifecycle_execute"]
        for m in expected:
            self.assertTrue(hasattr(api.MemoryAPI, m), "missing %s" % m)

    def test_canonical_roles_exact_18(self):
        from intelligence.lifecycle.model import RecordRole
        expected = ["source_record", "memory", "knowledge", "experience",
                    "outcome", "evidence", "learning", "strategy", "context",
                    "strategy_application", "reasoning", "decision", "plan",
                    "authority", "authorization", "action", "observation",
                    "verification"]
        self.assertEqual(sorted(r.value for r in RecordRole),
                         sorted(expected))
        self.assertEqual(len(list(RecordRole)), 18)

    def test_docs_claim_17_ops_matching_code(self):
        import api.contract_v2 as v2
        ops = list(v2.OPERATIONS.keys())
        doc = (_ROOT / "docs" / "public-contract.md").read_text(encoding="utf-8")
        self.assertIn("exactly seventeen", doc)
        for op in ops:
            self.assertIn(op, doc)

    def test_no_coding_vocabulary_ship(self):
        pkg_vocab = _ROOT / "ai_engine" / "vocabularies"
        self.assertTrue((pkg_vocab / "diary_v1.json").exists())
        self.assertFalse((pkg_vocab / "code_v1.json").exists())


if __name__ == "__main__":
    unittest.main()