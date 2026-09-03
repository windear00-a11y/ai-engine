"""Phase 8: learning never modifies policy."""

import os
import unittest
import hashlib


class LearningDoesNotModifyPoliciesTests(unittest.TestCase):
    def test_policy_files_unchanged_by_learning(self):
        # Capture hashes of all policy files before learning, run learning,
        # then verify hashes unchanged.
        policies_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "intelligence", "policies")
        files = []
        hashes_before = {}
        for fname in os.listdir(policies_dir):
            if fname.endswith(".json"):
                path = os.path.join(policies_dir, fname)
                with open(path, "rb") as f:
                    hashes_before[fname] = hashlib.sha256(f.read()).hexdigest()
                files.append(fname)
        # Run a learning event (positive learning)
        from intelligence.strategy.schema import Strategy
        from intelligence.strategy.store import StrategyStore
        from intelligence.outcome.schema import Outcome, derive_outcome_id
        from intelligence.outcome.store import OutcomeStore
        from intelligence.outcome.types import OutcomeClassification
        from intelligence.experience.schema import ExperienceRecord, derive_experience_id
        from intelligence.experience.store import ExperienceStore
        from intelligence.learning import learn_from_outcome, LearningStore
        s_store = StrategyStore(":memory:")
        o_store = OutcomeStore(":memory:")
        e_store = ExperienceStore(":memory:")
        l_store = LearningStore(":memory:")
        strat = Strategy(strategy_id="st_pol", name="pol", description="d",
                         problem_class="bug_fix", tool_sequence=["file.read"],
                         confidence=0.5, strategy_type="explicit",
                         created_at_epoch=1.0, updated_at_epoch=1.0)
        s_store.save(strat)
        for i in range(2):
            oc = Outcome(outcome_id=derive_outcome_id(f"plan{i}", "ctx_a",
                          OutcomeClassification.SUCCESS, [f"ev{i}"], {}),
                         plan_id=f"plan{i}", context_id="ctx_a",
                         classification=OutcomeClassification.SUCCESS,
                         verification_evidence_ids=(f"ev{i}",), created_at_epoch=float(i))
            o_store.save(oc)
            exp = ExperienceRecord(experience_id=derive_experience_id(f"t{i}", "ctx_a", oc.outcome_id, [f"ev{i}"], "st_pol"),
                                   task_id=f"t{i}", task_type="bug_fix", domain="lint",
                                   context_id="ctx_a", outcome_id=oc.outcome_id,
                                   evidence_ids=(f"ev{i}",), strategy_id="st_pol",
                                   summary={}, synthesized_at_epoch=float(i))
            e_store.save(exp)
        oc_id = derive_outcome_id("plan1", "ctx_a", OutcomeClassification.SUCCESS, ["ev1"], {})
        learn_from_outcome(oc_id, context_id="ctx_a", strategy_id="st_pol",
                           outcome_store=o_store, strategy_store=s_store,
                           experience_store=e_store, learning_store=l_store,
                           created_at_epoch=10.0)
        # Verify policy files unchanged
        for fname in files:
            path = os.path.join(policies_dir, fname)
            with open(path, "rb") as f:
                after = hashlib.sha256(f.read()).hexdigest()
            self.assertEqual(after, hashes_before[fname],
                             f"policy file {fname} was modified by learning")
        s_store.close(); o_store.close(); e_store.close(); l_store.close()

    def test_learning_package_has_no_policy_write(self):
        # Static check: learning modules do not import or write to policies dir
        import pathlib
        import ast
        for p in pathlib.Path("intelligence/learning").glob("*.py"):
            text = p.read_text()
            self.assertNotIn("policies_dir", text.replace(" ", "")[:1000] if "open" in text and "policies" in text else "",
                             f"{p} should not write to policies")
            # Ensure no open(..., 'w') on a policy path
            if "policies" in text:
                self.assertNotIn("open", text.split("policies")[1][:200] if "policies" in text else "",
                                 f"{p} should not open policy for writing")
