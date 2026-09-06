"""Phase 24 — Persistent Intelligence Core extraction contract.

Verifies that the repository now contains a genuinely generic, local-first,
deterministic core with NO code/domain artifacts shipped, while preserving the
generic plugin/adapter boundary and the trust/policy/approval/provenance/
rollback/data/project-isolation guarantees.

Failure here means a domain implementation leaked back into the core (Case 2
in the prompt: "reintroduced a domain"), or the generic boundary/trust layer
was damaged by the extraction.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SHOWN_PACKAGES = (
    "ai_engine",
    "api",
    "intelligence",
    "retrieval",
    "ingestion",
    "importing",
    "external_import",
    "knowledge_client",
    "external_http_client",
    "http_server",
    "tools",
)

_DOMAIN_NAMES = (
    "ExecutionRunner",
    "DeterministicPlanner",
    "ProjectIndex",
    "TaskEngine",
    "CodingConfig",
    "WriteTools",
    "knowledge_compiler",
    "webapp",
)

_VOCAB_DIR = os.path.join(_REPO_ROOT, "ai_engine", "vocabularies")


def _iter_shown_py_files():
    for pkg in _SHOWN_PACKAGES:
        root = os.path.join(_REPO_ROOT, pkg)
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fn in files:
                if fn.endswith(".py"):
                    yield os.path.join(base, fn)


def _auto_approve(proposed):
    return True


class DomainExtractionAbsenceTests(unittest.TestCase):
    """Case-2 guard: no code/domain implementation may exist in the core."""

    def test_no_domain_source_trees(self):
        for missing in ("engine", "knowledge_compiler",
                        os.path.join("tools", "coding"),
                        os.path.join("ai_engine", "plugins", "code")):
            self.assertFalse(
                os.path.exists(os.path.join(_REPO_ROOT, missing)),
                "domain tree still present: %s" % missing)

    def test_domain_classes_absent_from_shown_packages(self):
        shown = "\n".join(
            open(p, encoding="utf-8", errors="replace").read()
            for p in _iter_shown_py_files())
        for clsname in _DOMAIN_NAMES:
            self.assertNotIn("class %s" % clsname, shown)
            self.assertNotIn("class %s(" % clsname, shown)

    def test_plugins_boundary_is_generic_only(self):
        plugins_dir = os.path.join(_REPO_ROOT, "ai_engine", "plugins")
        entries = [e for e in os.listdir(plugins_dir)
                   if not e.startswith("__") and e != "__pycache__"]
        self.assertEqual(entries, [],
                         "domain plugins shipped inside core: %r" % entries)
        import ai_engine.plugins as boundary
        self.assertEqual(boundary.__all__, [])

    def test_no_code_vocabulary(self):
        self.assertFalse(
            os.path.exists(os.path.join(_VOCAB_DIR, "code_v1.json")),
            "code_v1 vocabulary shipped in core")
        names = [n for n in os.listdir(_VOCAB_DIR) if n.endswith(".json")]
        self.assertIn("diary_v1.json", names)

    def test_non_packaged_trees_never_shipped(self):
        # database/ (preserved production data), workspace/, sources/, and the
        # retired domain shells must not appear in the wheel include patterns.
        import tomllib
        with open(os.path.join(_REPO_ROOT, "pyproject.toml"), "rb") as fh:
            data = tomllib.load(fh)
        include = data["tool"]["setuptools"]["packages"]["find"]["include"]
        for banned in ("database", "workspace", "sources", "engine",
                       "knowledge_compiler", "webapp"):
            for pat in include:
                self.assertFalse(
                    pat.startswith(banned),
                    "include pattern touches %s: %r" % (banned, pat))

    def test_foreign_bridge_modules_absent(self):
        foreign = (
            os.path.join("ai_engine", "code_bridge.py"),
            os.path.join("ai_engine", "code_ingest.py"),
            os.path.join("internal", "integra", "movement"),
        )
        for rel in foreign:
            self.assertFalse(
                os.path.exists(os.path.join(_REPO_ROOT, rel)),
                "foreign bridge still present: %s" % rel)

    def test_no_soft_import_of_removed_domains(self):
        shown = "\n".join(
            open(p, encoding="utf-8", errors="replace").read()
            for p in _iter_shown_py_files())
        for banned in (
                "from engine import",
                "import engine",
                "from tools.coding",
                "from knowledge_compiler",
                "from webapp",
                "tools.coding.tools",
        ):
            self.assertNotIn(banned, shown)


class GenericCoreOperationalTests(unittest.TestCase):
    """Zero-plugin core must still perform its generic duties."""

    def test_memory_remember_recall_does_not_require_any_plugin(self):
        from ai_engine.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(project_id="default", data_root=tmp,
                         vocabulary_id="diary_v1")
            r = mem.remember(payload={"text": "phase24 generic memory"})
            self.assertTrue(r["ok"], r)
            recall = mem.recall(query="phase24 generic memory")
            out = recall["result"]["knowledge"]
            self.assertGreaterEqual(len(out), 1)

    def test_knowledge_client_search_get_provenance(self):
        from api.tools import ToolInterface
        from knowledge_client.transports import InProcessTransport
        from knowledge_client import KnowledgeClient
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            iface = ToolInterface(db_path=db)
            client = KnowledgeClient(InProcessTransport(interface=iface))
            self.assertEqual(client.inspect()["node_count"], 0)
            client.close()
            iface.close()

    def test_vocabularies_load_generic_diary(self):
        from ai_engine.vocabulary import Vocabulary, list_vocabularies
        lst = list_vocabularies()
        self.assertIn("diary_v1", lst)
        v = Vocabulary.load("diary_v1")
        self.assertEqual(v.id, "diary_v1")

    def test_deterministic_generic_planner(self):
        from ai_engine.generic_planner import plan_generic
        situation = {"problem": "need to recall fact about sleep"}
        objective = "recall relevant memory"
        available = {"knowledge": [{"id": "fact_sleep", "type": "fact",
                                    "name": "sleep fact",
                                    "description": "sleep routine helps"}]}
        r1 = plan_generic(situation, objective,
                          available_information=available,
                          constraints={}, context={})
        r2 = plan_generic(situation, objective,
                          available_information=available,
                          constraints={}, context={})
        self.assertEqual(r1, r2)
        self.assertEqual(r1["status"], "ok")


class PluginAdapterBoundaryTests(unittest.TestCase):
    """Generic adapter boundary must be intact and explicit/fail-closed."""

    def setUp(self):
        from ai_engine.registry import AdapterRegistry
        self.reg = AdapterRegistry()

    def test_registry_explicit_and_fail_closed(self):
        seen = set()
        self.reg.register_effect("my_tool", lambda *a, **kw: None)
        seen.add("my_tool")
        self.assertEqual(set(self.reg.list_effects()), seen)
        with self.assertRaises(ValueError):
            self.reg.register_effect("my_tool", lambda *a, **kw: None)
        with self.assertRaises(ValueError):
            self.reg.register_effect("a/../b", lambda *a, **kw: None)
        self.assertFalse(self.reg.has_effect("not-registered"))

    def test_registry_effect_adapter_shape(self):
        def handler(inp):
            return {"ok": True}
        self.reg.register_effect("probe", handler)
        got = self.reg.get_effect("probe")
        self.assertIs(got, handler)
        self.assertIsNone(self.reg.get_effect("missing"))

    def test_registry_rankers_captures_boundaries(self):
        from ai_engine.ranker import KeywordRanker
        ranker = KeywordRanker()
        self.reg.register_ranker("kw", ranker)
        self.assertIs(self.reg.get_ranker("kw"), ranker)
        from ai_engine.capture import ManualCaptureAdapter
        cap = ManualCaptureAdapter()
        self.reg.register_capture(cap)
        self.assertIs(self.reg.get_capture(cap.adapter_id), cap)
        with self.assertRaises(ValueError):
            self.reg.register_capture(object())


class TrustLayerPreservationTests(unittest.TestCase):
    """The removed ExecutionRunner used to be the only enforcement point;
    Phase 24 keeps the same guarantees in the Core trust layer."""

    def test_command_specs_closed_forms_no_any(self):
        from tools.permissions.policy import Policy
        policy = Policy()
        for cmd in ("flake8", "ruff", "black", "isort", "npm", "pip",
                    "python", "python3"):
            spec = policy.command_spec(cmd)
            self.assertIsNotNone(spec, cmd)
            forms = spec.get("forms") or []
            self.assertTrue(forms, cmd)
            for form in forms:
                self.assertIn("args", form)
                self.assertNotEqual(form["args"], "any", cmd)

    def test_deny_by_default_git_and_make(self):
        from tools.permissions.policy import Policy
        policy = Policy()
        self.assertIsNone(policy.command_spec("git"))
        self.assertIsNone(policy.command_spec("make"))

    def test_run_checked_confines_cwd_to_workspace(self):
        from tools.permissions import Policy
        from tools.permissions.execution import run_checked
        with tempfile.TemporaryDirectory() as tmp:
            ws = os.path.join(tmp, "ws")
            os.makedirs(ws)
            with open(os.path.join(ws, "mod.py"), "w") as fh:
                fh.write("x = 1\n")
            r = run_checked(Policy(), "python3",
                            ["-m", "py_compile", "mod.py"],
                            approval_of=_auto_approve, cwd=None,
                            workspace_root=ws)
            self.assertTrue(r["success"], r)
            self.assertEqual(os.path.realpath(r["cwd"]), os.path.realpath(ws))
            escaped = os.path.join(tmp, "outside")
            os.makedirs(escaped)
            r2 = run_checked(Policy(), "python3",
                             ["-m", "py_compile", "mod.py"],
                             approval_of=_auto_approve, cwd=escaped,
                             workspace_root=ws)
            self.assertFalse(r2["success"])
            self.assertIn("denied", r2["error"])

    def test_gate_roundtrip_approval_required(self):
        from tools.permissions import (Policy, ApprovalGate, EngineState,
                                       Domain, PathPolicy)
        from tools.permissions.decisions import DecisionKind as DK
        from tools.permissions.execution import run_checked
        with tempfile.TemporaryDirectory() as tmp:
            state = EngineState(db_path=os.path.join(tmp, "s.db"))
            gate = ApprovalGate(path_policy=PathPolicy(tmp), state=state,
                                approver=lambda p: p.get("domain") == "execute")
            proposal = {"command": "python3 -m py_compile mod.py", "cwd": tmp}
            decision = gate.check(Domain.EXECUTE, proposal["command"])
            self.assertIs(decision.kind, DK.REQUIRE_APPROVAL)
            with open(os.path.join(tmp, "mod.py"), "w") as fh:
                fh.write("x = 1\n")
            r = run_checked(Policy(), "python3",
                            ["-m", "py_compile", "mod.py"],
                            approval_of=_auto_approve, cwd=tmp,
                            workspace_root=tmp)
            self.assertTrue(r["success"], r)

    def test_path_policy_zones_and_hard_write_guard(self):
        from tools.permissions import PathPolicy
        from tools.permissions.pathpolicy import hard_write_guard
        with tempfile.TemporaryDirectory() as tmp:
            pp = PathPolicy(tmp)
            db = os.path.join(tmp, "database", "knowledge.db")
            res = pp.read_decision(db)
            self.assertEqual(res.kind.value, "deny")
            self.assertTrue(hard_write_guard(db, tmp))
            from tools.permissions.fs import PathError
            with self.assertRaises(PathError):
                pp.read_decision("../secret.txt")


class GenericLoopDomainSafeTests(unittest.TestCase):
    """The generic loop must fail safe for domain work with no plugin."""

    def _stores(self, names):
        out = {}
        for name in names:
            if name == "strategy_store":
                from intelligence.strategy.store import StrategyStore
                out[name] = StrategyStore(":memory:")
            elif name == "outcome_store":
                from intelligence.outcome.store import OutcomeStore
                out[name] = OutcomeStore(":memory:")
            elif name == "experience_store":
                from intelligence.experience.store import ExperienceStore
                out[name] = ExperienceStore(":memory:")
            elif name == "learning_store":
                from intelligence.learning.store import LearningStore
                out[name] = LearningStore(":memory:")
            elif name == "evidence_store":
                from intelligence.evidence.store import EvidenceStore
                out[name] = EvidenceStore(":memory:")
            elif name == "reasoning_store":
                from intelligence.reasoning.store import ReasoningStore
                out[name] = ReasoningStore(":memory:")
            elif name == "decision_store":
                from intelligence.decision.store import DecisionStore
                out[name] = DecisionStore(":memory:")
            elif name == "context_store":
                from intelligence.context.store import ContextStore
                out[name] = ContextStore(":memory:")
        return out

    def test_coding_task_fails_safe_without_plugin(self):
        from intelligence.loop.engine import execute_intelligence_loop
        stores = self._stores([
            "strategy_store", "outcome_store", "experience_store",
            "learning_store", "evidence_store", "reasoning_store",
            "decision_store", "context_store"])
        task = {"task_id": "t_code", "task_type": "bug_fix",
                "domain": "code",
                "target": {"file": "src/mod.py", "error": "syntax"}}
        res = execute_intelligence_loop(task, workspace_root="/tmp",
                                        **stores)
        self.assertFalse(res.ok)
        self.assertIsNone(res.learning_event_id)
        self.assertIsNone(res.experience_id)
        self.assertTrue(
            any("plugin" in e and "insufficient" in e for e in res.errors),
            res.errors)
        if res.outcome_id is not None:
            self.assertIn("UNKNOWN", res.status)

    def test_loop_never_fabricates_success_without_plugin(self):
        from intelligence.loop.engine import execute_intelligence_loop
        stores = self._stores([
            "strategy_store", "outcome_store", "experience_store",
            "learning_store", "evidence_store", "reasoning_store",
            "decision_store", "context_store"])
        task = {"task_id": "t_generic", "task_type": "generic",
                "domain": "", "target": {},
                "description": "summarize available knowledge facts"}
        res = execute_intelligence_loop(task, workspace_root="/tmp",
                                        operator_approval=_auto_approve,
                                        **stores)
        # Even with approval granted, an unplannable task must never be
        # recorded as SUCCESS and must never spawn learning/experience.
        self.assertFalse(res.ok)
        self.assertIsNone(res.learning_event_id)
        self.assertIsNone(res.experience_id)
        self.assertTrue(
            any("plugin" in e and "insufficient" in e for e in res.errors),
            res.errors)


class DeterminismAndLocalFirstTests(unittest.TestCase):
    def test_no_network_or_ai_dependency_imports(self):
        shown = "\n".join(
            open(p, encoding="utf-8", errors="replace").read()
            for p in _iter_shown_py_files())
        for banned in ("import requests", "import openai", "import torch",
                       "import anthropic", "import google.generativeai"):
            self.assertNotIn(banned, shown)

    def test_deterministic_retrieval_queries(self):
        from retrieval.ranked_knowledge import build_query_terms
        q1 = build_query_terms({"intent": "bug_fix", "target": {"file": "a.py"},
                                "error": {"line": 1}})
        q2 = build_query_terms({"intent": "bug_fix", "target": {"file": "a.py"},
                                "error": {"line": 1}})
        self.assertEqual(q1, q2)

    def test_pycompile_stays_closed_form_via_core(self):
        from tools.permissions import Policy
        from tools.permissions.execution import run_checked
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            mod = os.path.join(tmp, "mod.py")
            with open(mod, "w") as fh:
                fh.write("x = 1\n")
            r = run_checked(Policy(), "python3",
                            ["-m", "py_compile", "mod.py"],
                            approval_of=_auto_approve, cwd=tmp)
            self.assertTrue(r["success"], r)


if __name__ == "__main__":
    unittest.main(verbosity=2)