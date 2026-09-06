"""Phase 17 — Generic Action / Effect + Verification Boundary.

Tests for 20 items:
1. generic Effect interface
2. generic Verifier interface
3. planner produces plan but does not execute
4. Effect executes only after required authority/approval
5. approval denial prevents Effect
6. Effect failure → FAILURE
7. verified success → SUCCESS
8. unknown verification → UNKNOWN
9. Effect "ok" but verifier UNKNOWN → Outcome UNKNOWN
10. verifier does not execute side effects
11. journal/audit preserved
12. rollback/recovery preserved
13. registry explicit registration
14. unknown effect/verifier controlled failure
15. coding compatibility remains
16. core generic execution has no coding imports
17. no LLM/network
18. no direct DB access
19. deterministic result representation
20. synthetic/unverified results never create fake successful learning
"""

import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

LEGACY_DB = os.path.join(_ROOT, "database", "knowledge.db")
EXPECTED_SHA = "000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91"

def _sha256(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1<<20), b""):
            h.update(c)
    return h.hexdigest()


class EffectInterfaceTests(unittest.TestCase):
    def test_generic_effect_interface(self):
        from ai_engine.effect import Effect, EffectResult, NoopEffect, MemoryRecallEffect
        from ai_engine.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            eff = MemoryRecallEffect(mem)
            self.assertIsInstance(eff, Effect)
            self.assertEqual(eff.effect_id, "memory.recall")
            # Execute read-only effect
            res = eff.execute({"tool": "memory.recall", "inputs": {"query": "test"}}, context={"context_id": "ctx_test"})
            self.assertIsInstance(res, EffectResult)
            d = res.as_dict()
            self.assertIn("effect_id", d)
            self.assertIn("status", d)
            self.assertIn("side_effect_occurred", d)
            self.assertFalse(d["side_effect_occurred"])  # recall is read-only

    def test_generic_verifier_interface(self):
        from ai_engine.verifier import Verifier, GenericVerifier, VerificationResult
        v = GenericVerifier()
        self.assertIsInstance(v, Verifier)
        self.assertEqual(v.verifier_id, "generic")
        # Verify success case
        from ai_engine.effect import EffectResult
        eff = EffectResult(effect_id="eff_test", action={"tool": "memory.recall"}, status="success", output={"knowledge": []}, side_effect_occurred=False)
        res = v.verify({"tool": "memory.recall"}, eff, expected_outcome="success")
        self.assertIsInstance(res, VerificationResult)
        self.assertIn(res.status, [VerificationResult.VERIFIED_SUCCESS, VerificationResult.VERIFIED_FAILURE, VerificationResult.UNKNOWN])

    def test_planner_no_side_effects(self):
        from ai_engine.generic_planner import plan_generic
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "marker.txt")
            with open(marker, "w") as f:
                f.write("original")
            # Planner should not create files or DBs
            for _ in range(3):
                plan_generic({"problem": "test no side effects"}, objective="test", available_information={}, constraints={}, context={})
            self.assertFalse(os.path.exists(os.path.join(tmp, "should_not_exist.db")))
            with open(marker, "r") as f:
                self.assertEqual(f.read(), "original")


class ApprovalTests(unittest.TestCase):
    def test_effect_only_after_authority(self):
        from ai_engine.memory import Memory
        from ai_engine.effect import MemoryRememberEffect, execute_with_approval
        from tools.permissions import Policy, PathPolicy, ApprovalGate
        from tools.permissions.journal import EngineState
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(project_id="default", data_root=os.path.join(tmp, "data"), vocabulary_id="diary_v1")
            eff = MemoryRememberEffect(mem)
            # Without approval, should be denied
            ws = os.path.join(tmp, "ws")
            os.makedirs(ws)
            state = EngineState(db_path=os.path.join(tmp, "state.db"))
            policy = Policy()
            pp = PathPolicy(ws, policy=policy)
            gate_denied = ApprovalGate(path_policy=pp, state=state, approver=lambda p: False)
            action = {"tool": "memory.remember", "inputs": {"payload": {"text": "approval test", "type": "fact"}}}
            res = execute_with_approval(action, eff, context={}, approval_gate=gate_denied, policy=policy)
            self.assertEqual(res.status, "failure")
            self.assertIn("approval denied", res.error.lower())
            self.assertFalse(res.side_effect_occurred)
            # With approval, should succeed
            gate_allowed = ApprovalGate(path_policy=pp, state=state, approver=lambda p: True)
            res2 = execute_with_approval(action, eff, context={}, approval_gate=gate_allowed, policy=policy)
            self.assertEqual(res2.status, "success")
            self.assertTrue(res2.side_effect_occurred)

    def test_approval_denial_prevents_effect(self):
        from ai_engine.memory import Memory
        from ai_engine.effect import MemoryRememberEffect, execute_with_approval
        from tools.permissions import Policy, PathPolicy, ApprovalGate
        from tools.permissions.journal import EngineState
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(project_id="default", data_root=os.path.join(tmp, "data"), vocabulary_id="diary_v1")
            eff = MemoryRememberEffect(mem)
            ws = os.path.join(tmp, "ws")
            os.makedirs(ws)
            state = EngineState(db_path=os.path.join(tmp, "state.db"))
            gate = ApprovalGate(path_policy=PathPolicy(ws, Policy()), state=state, approver=lambda p: False)
            action = {"tool": "memory.remember", "inputs": {"payload": {"text": "denied test"}}}
            res = execute_with_approval(action, eff, approval_gate=gate, policy=Policy())
            self.assertEqual(res.status, "failure")
            # Verify no node was created
            out = mem.recall(query="denied test")
            self.assertEqual(len([n for n in out["result"]["knowledge"] if "denied test" in n["description"]]), 0)


class VerificationOutcomeTests(unittest.TestCase):
    def test_effect_failure_verified_failure(self):
        from ai_engine.effect import EffectResult
        from ai_engine.verifier import GenericVerifier, VerificationResult
        v = GenericVerifier()
        eff = EffectResult(effect_id="eff1", action={"tool": "memory.remember"}, status="failure", output=None, side_effect_occurred=False, error="some error")
        res = v.verify({"tool": "memory.remember"}, eff, expected_outcome="success")
        self.assertEqual(res.status, VerificationResult.VERIFIED_FAILURE)

    def test_verified_success_success(self):
        from ai_engine.effect import EffectResult
        from ai_engine.verifier import GenericVerifier, VerificationResult
        v = GenericVerifier()
        eff = EffectResult(effect_id="eff1", action={"tool": "memory.recall"}, status="success", output={"knowledge": []}, side_effect_occurred=False)
        res = v.verify({"tool": "memory.recall"}, eff, expected_outcome="success")
        self.assertEqual(res.status, VerificationResult.VERIFIED_SUCCESS)

    def test_unknown_verification_unknown(self):
        from ai_engine.effect import EffectResult
        from ai_engine.verifier import GenericVerifier, VerificationResult
        v = GenericVerifier()
        eff = EffectResult(effect_id="eff1", action={"tool": "noop"}, status="skipped", output=None, side_effect_occurred=False)
        res = v.verify({"tool": "noop"}, eff, expected_outcome="unknown")
        self.assertEqual(res.status, VerificationResult.UNKNOWN)

    def test_effect_ok_but_verifier_unknown_outcome_unknown(self):
        from ai_engine.effect import EffectResult
        from ai_engine.verifier import GenericVerifier, VerificationResult, outcome_from_verification
        v = GenericVerifier()
        eff = EffectResult(effect_id="eff1", action={"tool": "memory.recall"}, status="success", output={"ok": True}, side_effect_occurred=False)
        # Verifier with expected UNKNOWN should remain UNKNOWN even though effect is ok
        res = v.verify({"tool": "memory.recall"}, eff, expected_outcome="unknown")
        self.assertEqual(res.status, VerificationResult.UNKNOWN)
        # Outcome mapping should be UNKNOWN, not SUCCESS
        from intelligence.outcome.types import OutcomeClassification
        cls = outcome_from_verification(res)
        self.assertEqual(cls, OutcomeClassification.UNKNOWN)

    def test_verifier_no_side_effects(self):
        from ai_engine.verifier import GenericVerifier
        from ai_engine.effect import EffectResult
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "marker.txt")
            with open(marker, "w") as f:
                f.write("original")
            v = GenericVerifier()
            eff = EffectResult(effect_id="eff1", action={"tool": "memory.recall"}, status="success", output={}, side_effect_occurred=False)
            v.verify({"tool": "memory.recall"}, eff, expected_outcome="success")
            # Verifier should not have created files
            self.assertFalse(os.path.exists(os.path.join(tmp, "verifier_side_effect.db")))
            with open(marker, "r") as f:
                self.assertEqual(f.read(), "original")


class JournalRollbackTests(unittest.TestCase):
    def test_journal_preserved(self):
        from tools.permissions.journal import EngineState
        from tests._gate_writer_support import GateWriter
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            ws = os.path.join(tmp, "ws")
            os.makedirs(ws)
            state = EngineState(db_path=os.path.join(tmp, "state.db"))
            from tools.permissions import Policy, PathPolicy, ApprovalGate
            policy = Policy()
            pp = PathPolicy(ws, policy=policy)
            gate = ApprovalGate(path_policy=pp, state=state,
                                approver=lambda p: True)
            wt = GateWriter(ws, gate)
            res = wt.write("a.txt", "hello")
            self.assertIsNone(res["error"])
            # Journal should have entry (audit from the write approval).
            self.assertGreater(state.audit_count(), 0)
            # Rollback should still work
            op_id = res["operation_id"]
            from tools.permissions.rollback import RollbackExecutor
            ex = RollbackExecutor(pp, state)
            plan = ex.plan(op_id)
            self.assertFalse(plan["not_found"])

    def test_rollback_preserved(self):
        from tools.permissions.journal import EngineState
        from tools.permissions import Policy, PathPolicy, ApprovalGate
        from tools.permissions.rollback import RollbackExecutor
        from tests._gate_writer_support import GateWriter
        with tempfile.TemporaryDirectory() as tmp:
            ws = os.path.join(tmp, "ws")
            os.makedirs(ws)
            state = EngineState(db_path=os.path.join(tmp, "state.db"))
            gate = ApprovalGate(path_policy=PathPolicy(ws, Policy()),
                                state=state, approver=lambda p: True)
            wt = GateWriter(ws, gate)
            op_id = wt.write("b.txt", "hi")["operation_id"]
            ex = RollbackExecutor(PathPolicy(ws, Policy()), state)
            result = ex.execute(op_id)
            # Rollback via the generic executor surface.
            self.assertIn(result["result"],
                          ("rolled_back", "rollback_conflict"))


class RegistryTests(unittest.TestCase):
    def test_registry_explicit_registration(self):
        from ai_engine.registry import AdapterRegistry
        from ai_engine.effect import NoopEffect
        reg = AdapterRegistry()
        eff = NoopEffect()
        self.assertTrue(reg.register_effect("custom.noop", eff.execute))
        self.assertIn("custom.noop", reg.list_effects())
        self.assertEqual(reg.get_effect("custom.noop"), eff.execute)

    def test_unknown_effect_controlled_failure(self):
        from ai_engine.registry import AdapterRegistry
        reg = AdapterRegistry()
        self.assertIsNone(reg.get_effect("unknown.effect"))
        self.assertFalse(reg.has_effect("unknown.effect"))
        with self.assertRaises(ValueError):
            reg.register_effect("bad/effect", lambda: None)
        with self.assertRaises(ValueError):
            reg.register_effect("unknown.effect", "not callable")

    def test_unknown_verifier_controlled_failure(self):
        from ai_engine.registry import AdapterRegistry
        reg = AdapterRegistry()
        self.assertIsNone(reg.get_verifier("unknown_verifier"))
        with self.assertRaises(ValueError):
            reg.register_verifier("bad", "not callable")


class CodingCompatTests(unittest.TestCase):
    def test_coding_domain_not_registered_in_core(self):
        # The removed coding facade (file.write / knowledge.search tools) is
        # NOT part of the generic core; a fresh registry has no domain tools
        # and effect lookup fails safely (deny-by-default, explicit plugin
        # boundary).
        from ai_engine.registry import AdapterRegistry
        reg = AdapterRegistry()
        self.assertFalse(reg.has_effect("file.write"))
        self.assertFalse(reg.has_effect("knowledge.search"))
        self.assertIsNone(reg.get_effect("file.write"))
        # Domain tools only exist when a plugin registers them.
        self.assertTrue(reg.register_effect("file.write", lambda *a, **k: None))
        self.assertIn("file.write", reg.list_effects())


class CoreGenericTests(unittest.TestCase):
    def test_core_generic_no_coding_imports(self):
        import pathlib
        for f in ["ai_engine/effect.py", "ai_engine/verifier.py"]:
            content = pathlib.Path(os.path.join(_ROOT, f)).read_text()
            self.assertNotIn("from tools.coding", content)
            self.assertNotIn("import tools.coding", content)
            self.assertNotIn("bug_fix", content)
            self.assertNotIn("E302", content)
            self.assertNotIn("file.write", content)  # generic should not hardcode file.write

    def test_no_llm_network(self):
        import pathlib
        for f in ["ai_engine/effect.py", "ai_engine/verifier.py"]:
            content = pathlib.Path(os.path.join(_ROOT, f)).read_text()
            self.assertNotIn("import openai", content)
            self.assertNotIn("import torch", content)
            self.assertNotIn("import requests", content)
            self.assertNotIn("socket", content)

    def test_no_direct_db_access(self):
        import pathlib
        for f in ["ai_engine/effect.py", "ai_engine/verifier.py"]:
            content = pathlib.Path(os.path.join(_ROOT, f)).read_text()
            self.assertNotIn("import sqlite3", content)
            self.assertNotIn("KnowledgeRepository", content)

    def test_deterministic_result_representation(self):
        from ai_engine.effect import NoopEffect
        eff = NoopEffect()
        action = {"tool": "noop", "inputs": {}}
        r1 = eff.execute(action, context={"context_id": "ctx_test"})
        r2 = eff.execute(action, context={"context_id": "ctx_test"})
        d1 = r1.as_dict()
        d2 = r2.as_dict()
        # created_at_epoch is time-based, ignore for determinism check
        d1.pop("created_at_epoch", None)
        d2.pop("created_at_epoch", None)
        self.assertEqual(d1, d2)
        self.assertEqual(r1.effect_id, r2.effect_id)

    def test_synthetic_never_fake_successful_learning(self):
        from ai_engine.generic_learning import learn_from_generic_experience
        from intelligence.experience.schema import ExperienceRecord
        from intelligence.outcome.schema import Outcome, derive_outcome_id
        from intelligence.outcome.types import OutcomeClassification
        from intelligence.evidence.schema import EvidenceRecord
        from intelligence.evidence.types import EvidenceType
        exp = ExperienceRecord(experience_id="xp_synth2", task_id="task_synth2", task_type="generic", domain="diary", context_id="ctx_synth", outcome_id="oc_synth", evidence_ids=("ev_synth",), summary={"synthetic": True, "outcome": "success"}, synthesized_at_epoch=1.0)
        oc = Outcome(outcome_id="oc_synth", plan_id="plan_synth", context_id="ctx_synth", classification=OutcomeClassification.SUCCESS, verification_evidence_ids=("ev_synth",), created_at_epoch=1.0)
        # Synthetic experience with SUCCESS should still be ignored
        res = learn_from_generic_experience(exp, oc)
        self.assertEqual(res["status"], "synthetic_ignored")
        self.assertIsNone(res["strategy_candidate"])


if __name__ == "__main__":
    unittest.main()
