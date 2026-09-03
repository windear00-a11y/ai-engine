"""Phase 6: knowledge correctly supports conclusions."""

import unittest

from intelligence.reasoning import reason, ReasoningQuery

_CTX = {"context_id": "ctx_a"}


class ReasoningKnowledgeSupportTests(unittest.TestCase):
    def _query(self, task_type="bug_fix", error="E302"):
        return ReasoningQuery(
            question="What strategy?", task_type=task_type, domain="lint",
            target={"file": "src/utils.py", "error": error})

    def test_relevant_knowledge_produces_conclusion(self):
        knowledge = [{
            "id": "k1", "type": "fact",
            "name": "E302 fixed by inserting blank line",
            "description": "E302 fixed by inserting blank line",
            "subject": "e302", "lifecycle": {"confidence": 0.9},
        }]
        out = reason(self._query(), _CTX, knowledge_nodes=knowledge)
        self.assertEqual(len(out.conclusions), 1)
        c = out.conclusions[0]
        self.assertIn("bug_fix", c.claim)
        self.assertEqual(c.source, "knowledge")
        # chain has a knowledge step
        step_types = {s.source_type for s in c.evidence_chain.steps}
        self.assertIn("knowledge", step_types)

    def test_irrelevant_knowledge_excluded(self):
        knowledge = [{
            "id": "k_other", "type": "fact",
            "name": "Tax calculation for accounting module",
            "description": "Tax rules", "subject": "tax",
            "lifecycle": {"confidence": 0.95},
        }]
        out = reason(self._query(), _CTX, knowledge_nodes=knowledge)
        self.assertEqual(out.conclusions, [])
        self.assertEqual(len(out.insufficient_evidence), 1)

    def test_confidence_reflects_knowledge_confidence(self):
        hi = [{"id": "k1", "type": "fact",
               "name": "E302 fixed by inserting blank line",
               "subject": "e302", "lifecycle": {"confidence": 0.9}}]
        lo = [{"id": "k1", "type": "fact",
               "name": "E302 fixed by inserting blank line",
               "subject": "e302", "lifecycle": {"confidence": 0.4}}]
        c_hi = reason(self._query(), _CTX, knowledge_nodes=hi,
                      experiences=[]) .conclusions[0].confidence
        c_lo = reason(self._query(), _CTX, knowledge_nodes=lo,
                      experiences=[]) .conclusions[0].confidence
        self.assertGreater(c_hi, c_lo)


if __name__ == "__main__":
    unittest.main()
