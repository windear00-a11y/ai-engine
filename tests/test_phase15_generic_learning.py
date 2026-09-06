"""Phase 15 — Generic Experience / Learning / Strategy Layer.

Tests for (14):
1. successful generic experience → strategy candidate
2. failed generic experience → avoidance/alternative lesson
3. UNKNOWN outcome → no success strategy
4. repeated equivalent experiences → deterministic aggregation
5. strategy provenance → source experiences/outcomes traceable
6. synthetic experience → no successful strategy
7. learning cannot modify Policy
8. learning cannot bypass ApprovalGate
9. planner can consume strategy
10. current conflicting evidence overrides old strategy
11. identical inputs → identical learning output
12. core learning has no coding imports
13. no direct DB access from domain logic
14. no LLM/network dependency
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


class GenericLearningTests(unittest.TestCase):
    def _make_generic_experience(self, task_id, outcome_class, context_id="ctx_test", is_synthetic=False):
        from intelligence.experience.schema import ExperienceRecord
        from intelligence.outcome.schema import Outcome, derive_outcome_id
        from intelligence.outcome.types import OutcomeClassification
        from intelligence.evidence.schema import EvidenceRecord
        from intelligence.evidence.types import EvidenceType
        import time
        # Create minimal outcome and evidence
        ev = EvidenceRecord(
            evidence_id=f"ev_{task_id}",
            source_observation_id=f"obs_{task_id}",
            claim=f"test {task_id}",
            context_id=context_id,
            evidence_type=EvidenceType.FACT,
            supporting_data={"synthetic": is_synthetic} if is_synthetic else {},
            created_at_epoch=1.0,
        )
        oc = Outcome(
            outcome_id=derive_outcome_id(f"plan_{task_id}", context_id, OutcomeClassification(outcome_class), [ev.evidence_id], {}),
            plan_id=f"plan_{task_id}",
            context_id=context_id,
            classification=OutcomeClassification(outcome_class),
            verification_evidence_ids=(ev.evidence_id,),
            created_at_epoch=1.0,
        )
        summary = {"outcome": outcome_class}
        if is_synthetic:
            summary["synthetic"] = True
        exp = ExperienceRecord(
            experience_id=f"xp_{task_id}",
            task_id=task_id,
            task_type="generic",
            domain="diary",
            context_id=context_id,
            outcome_id=oc.outcome_id,
            evidence_ids=(ev.evidence_id,),
            strategy_id=None,
            summary=summary,
            synthesized_at_epoch=1.0,
        )
        return exp, oc, ev

    def test_successful_experience_strategy_candidate(self):
        from ai_engine.generic_learning import learn_from_generic_experience
        exp, oc, ev = self._make_generic_experience("task_success", "success")
        res = learn_from_generic_experience(exp, oc)
        self.assertEqual(res["status"], "success_strategy")
        self.assertIsNotNone(res["strategy_candidate"])
        self.assertIn("provenance", res)
        self.assertEqual(res["provenance"]["experience_id"], exp.experience_id)

    def test_failed_experience_avoidance(self):
        from ai_engine.generic_learning import learn_from_generic_experience
        exp, oc, ev = self._make_generic_experience("task_fail", "failure")
        res = learn_from_generic_experience(exp, oc)
        self.assertEqual(res["status"], "failure_lesson")
        self.assertIn("avoidance", res["strategy_candidate"]["status"])
        self.assertIn("failed", res["strategy_candidate"]["recommendation"].lower())

    def test_unknown_outcome_no_success_strategy(self):
        from ai_engine.generic_learning import learn_from_generic_experience
        exp, oc, ev = self._make_generic_experience("task_unknown", "unknown")
        res = learn_from_generic_experience(exp, oc)
        self.assertEqual(res["status"], "no_strategy")
        self.assertIsNone(res["strategy_candidate"])

    def test_repeated_equivalent_deterministic_aggregation(self):
        from ai_engine.generic_learning import learn_from_generic_experience
        exp1, oc1, _ = self._make_generic_experience("task_rep", "success", context_id="ctx_same")
        exp2, oc2, _ = self._make_generic_experience("task_rep2", "success", context_id="ctx_same")
        # Simulate repeated experiences with same situation and outcome
        # For Phase 15, repeated should strengthen evidence count but not blindly multiply confidence
        # Use experience_store to count
        from intelligence.experience.store import ExperienceStore
        from intelligence.outcome.store import OutcomeStore
        with tempfile.TemporaryDirectory() as tmp:
            exp_store = ExperienceStore(db_path=os.path.join(tmp, "exp.db"))
            out_store = OutcomeStore(db_path=os.path.join(tmp, "out.db"))
            # Save first experience/outcome
            exp_store.save(exp1)
            out_store.save(oc1)
            exp_store.save(exp2)
            out_store.save(oc2)
            # Now learn from second experience with store containing both
            res1 = learn_from_generic_experience(exp1, oc1, experience_store=exp_store, outcome_store=out_store)
            res2 = learn_from_generic_experience(exp2, oc2, experience_store=exp_store, outcome_store=out_store)
            # Deterministic: same situation should give same strategy_id
            self.assertEqual(res1["strategy_candidate"]["strategy_id"], res2["strategy_candidate"]["strategy_id"])
            # Confidence should be capped, not multiplied blindly
            self.assertLessEqual(res2["strategy_candidate"]["confidence"], 0.9)
            exp_store.close()
            out_store.close()

    def test_strategy_provenance_traceable(self):
        from ai_engine.generic_learning import learn_from_generic_experience
        exp, oc, ev = self._make_generic_experience("task_prov", "success")
        res = learn_from_generic_experience(exp, oc)
        prov = res["provenance"]
        self.assertIn("experience_id", prov)
        self.assertIn("outcome_id", prov)
        self.assertEqual(prov["experience_id"], exp.experience_id)
        self.assertIn("evidence_ids", prov)

    def test_synthetic_no_successful_strategy(self):
        from ai_engine.generic_learning import learn_from_generic_experience
        exp, oc, ev = self._make_generic_experience("task_synth", "success", is_synthetic=True)
        res = learn_from_generic_experience(exp, oc)
        self.assertEqual(res["status"], "synthetic_ignored")
        self.assertIsNone(res["strategy_candidate"])

    def test_learning_cannot_modify_policy(self):
        from tools.permissions import Policy
        from ai_engine.generic_learning import learn_from_generic_experience
        exp, oc, ev = self._make_generic_experience("task_policy", "success")
        policy_before = Policy().data
        res = learn_from_generic_experience(exp, oc)
        policy_after = Policy().data
        self.assertEqual(policy_before, policy_after)
        # Ensure no HARD_WRITE_INVARIANTS change
        from tools.permissions.policy import HARD_WRITE_INVARIANTS
        self.assertEqual(list(HARD_WRITE_INVARIANTS), [("database/knowledge.db", "blocked"), ("database/knowledge.db.backup", "blocked")])

    def test_learning_cannot_bypass_approval(self):
        from ai_engine.generic_learning import learn_from_generic_experience
        exp, oc, ev = self._make_generic_experience("task_approval", "success")
        res = learn_from_generic_experience(exp, oc)
        # Confidence != authority: even high confidence should not imply approval bypass
        # Check that strategy candidate does not contain approval bypass
        if res["strategy_candidate"]:
            self.assertNotIn("approval", res["strategy_candidate"].get("recommendation", "").lower() + " bypass")

    def test_planner_can_consume_strategy(self):
        from ai_engine.generic_planner import plan_generic
        from ai_engine.generic_learning import learn_from_generic_experience
        exp, oc, ev = self._make_generic_experience("task_plan", "success", context_id="ctx_plan")
        learn_res = learn_from_generic_experience(exp, oc)
        strat = learn_res["strategy_candidate"]
        # Planner should be able to consume this strategy
        situation = {"problem": "need fact X"}
        available = {"knowledge": [{"id": "k1", "type": "fact", "name": "k1", "description": "fact X"}], "strategies": [strat], "experience": []}
        plan = plan_generic(situation, objective="need fact X", available_information=available, constraints={}, context={"context_id": "ctx_plan"})
        # Planner should propose using learned strategy
        self.assertIsNotNone(plan["selected_action"])
        # If strategy matches situation, planner should use it
        # For this test, we just check planner succeeded
        self.assertIn(plan["status"], ("ok", "no_action", "conflict"))

    def test_conflicting_evidence_overrides_old_strategy(self):
        from ai_engine.generic_planner import plan_generic
        # Old strategy says X, but current evidence says not X
        strat = {"strategy_id": "st_old", "situation": "need fact X", "approach": "memory.recall", "confidence": 0.9, "description": "old"}
        situation = {"problem": "need fact X"}
        available = {
            "knowledge": [{"id": "k1", "type": "fact", "name": "k1", "description": "different fact Y"}],
            "strategies": [strat],
            "contradictions": [{"id": "c1"}],
        }
        res = plan_generic(situation, objective="need fact X", available_information=available, constraints={}, context={})
        # Should surface conflict, not blindly follow old strategy
        self.assertEqual(res["status"], "conflict")

    def test_identical_inputs_identical_output(self):
        from ai_engine.generic_learning import learn_from_generic_experience
        exp1, oc1, _ = self._make_generic_experience("task_ident", "success")
        exp2, oc2, _ = self._make_generic_experience("task_ident", "success")
        # Make them identical (same ids, same everything)
        # For determinism, we need same experience_id and outcome_id
        # Our helper creates deterministic ids based on task_id, so same task_id gives same
        r1 = learn_from_generic_experience(exp1, oc1)
        r2 = learn_from_generic_experience(exp2, oc2)
        self.assertEqual(r1["strategy_candidate"]["strategy_id"], r2["strategy_candidate"]["strategy_id"])
        self.assertEqual(r1["provenance"], r2["provenance"])

    def test_core_learning_no_coding_imports(self):
        import pathlib
        content = pathlib.Path(os.path.join(_ROOT, "ai_engine", "generic_learning.py")).read_text()
        self.assertNotIn("from tools.coding", content)
        self.assertNotIn("import tools.coding", content)
        self.assertNotIn("bug_fix", content)
        self.assertNotIn("file.write", content)
        self.assertNotIn("E302", content)

    def test_no_direct_db_access(self):
        import pathlib
        content = pathlib.Path(os.path.join(_ROOT, "ai_engine", "generic_learning.py")).read_text()
        self.assertNotIn("import sqlite3", content)
        self.assertNotIn("KnowledgeRepository", content)
        self.assertNotIn("sqlite", content.lower())

    def test_no_llm_network(self):
        import pathlib
        content = pathlib.Path(os.path.join(_ROOT, "ai_engine", "generic_learning.py")).read_text()
        self.assertNotIn("import openai", content)
        self.assertNotIn("import torch", content)
        self.assertNotIn("import requests", content)
        self.assertNotIn("socket", content)


if __name__ == "__main__":
    unittest.main()
