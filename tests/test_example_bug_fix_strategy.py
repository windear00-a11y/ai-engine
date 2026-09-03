"""Phase 6: Canonical Model concrete scenario (Task 2) - bug fix strategy.

Given knowledge that "E302 fixed by inserting blank line" and prior
experience that blank-line insertion succeeded at context match 0.95,
reasoning concludes the bug_fix is appropriate with confidence ~0.81
(matching the roadmap's Task 2 expectations: confidence 0.81).
"""

import unittest

from intelligence.reasoning import reason, ReasoningQuery


class _Exp:
    def __init__(self, task_type, outcome, context_id, cm):
        self.task_type = task_type
        self.domain = "lint"
        self.context_id = context_id
        self.summary = {"outcome": outcome, "context_match": cm}
        self.experience_id = "xp_task1"


class ExampleBugFixStrategyTests(unittest.TestCase):
    def setUp(self):
        self.query = ReasoningQuery(
            question="What strategy should be used for this task?",
            task_type="bug_fix", domain="lint",
            target={"file": "src/utils.py", "error": "E302"})
        self.context = {"context_id": "ctx_linux312_flake8"}
        self.knowledge = [{
            "id": "k_e302", "type": "fact",
            "name": "E302 fixed by inserting blank line",
            "description": "E302 fixed by inserting blank line",
            "subject": "e302", "lifecycle": {"confidence": 0.85},
        }]
        self.experiences = [_Exp("bug_fix", "success_verified",
                                 "ctx_linux312_flake8", cm=0.95)]

    def test_conclusion_appropriate(self):
        out = reason(self.query, self.context, knowledge_nodes=self.knowledge,
                     experiences=self.experiences)
        self.assertEqual(len(out.conclusions), 1)
        self.assertIn("bug_fix", out.conclusions[0].claim)

    def test_confidence_matches_scenario(self):
        out = reason(self.query, self.context, knowledge_nodes=self.knowledge,
                     experiences=self.experiences)
        confidence = out.conclusions[0].confidence
        # roadmap Task 2: confidence ~0.81
        self.assertAlmostEqual(confidence, 0.8075, places=3)

    def test_evidence_chain_combines_knowledge_and_experience(self):
        out = reason(self.query, self.context, knowledge_nodes=self.knowledge,
                     experiences=self.experiences)
        chain = out.conclusions[0].evidence_chain
        types = {s.source_type for s in chain.steps}
        self.assertIn("knowledge", types)
        self.assertIn("experience", types)
        # two independent supports -> chain quality 0.95
        self.assertEqual(chain.chain_quality, 0.95)
        self.assertEqual(chain.propagated_confidence,
                         out.conclusions[0].confidence)

    def test_reasoning_id_deterministic(self):
        a = reason(self.query, self.context, knowledge_nodes=self.knowledge,
                   experiences=self.experiences)
        b = reason(self.query, self.context, knowledge_nodes=self.knowledge,
                   experiences=self.experiences)
        self.assertEqual(a.reasoning_id, b.reasoning_id)


if __name__ == "__main__":
    unittest.main()
