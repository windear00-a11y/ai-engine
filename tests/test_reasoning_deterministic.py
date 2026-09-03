"""Phase 6: reasoning is deterministic (same inputs -> same outputs)."""

import unittest

from intelligence.reasoning import reason, ReasoningQuery


class _Exp:
    def __init__(self, task_type, outcome, context_id, cm=0.95):
        self.task_type = task_type
        self.domain = "lint"
        self.context_id = context_id
        self.summary = {"outcome": outcome, "context_match": cm}
        self.experience_id = "xp_" + task_type + "_" + outcome


_BUG = ReasoningQuery(
    question="What strategy should be used for this task?",
    task_type="bug_fix", domain="lint",
    target={"file": "src/utils.py", "error": "E302"})
_CTX = {"context_id": "ctx_linux312"}
_KNOWLEDGE = [{
    "id": "k1", "type": "fact", "name": "E302 fixed by inserting blank line",
    "description": "E302 fixed by inserting blank line", "subject": "e302",
    "lifecycle": {"confidence": 0.85},
}]
_EXPS = [_Exp("bug_fix", "success_verified", "ctx_linux312", cm=0.95)]


class ReasoningDeterministicTests(unittest.TestCase):
    def test_identical_inputs_identical_output(self):
        out1 = reason(_BUG, _CTX, knowledge_nodes=_KNOWLEDGE,
                      experiences=_EXPS)
        out2 = reason(_BUG, _CTX, knowledge_nodes=_KNOWLEDGE,
                      experiences=_EXPS)
        self.assertEqual(out1.reasoning_id, out2.reasoning_id)
        self.assertEqual(len(out1.conclusions), len(out2.conclusions))
        for c1, c2 in zip(out1.conclusions, out2.conclusions):
            self.assertEqual(c1.claim, c2.claim)
            self.assertEqual(c1.confidence, c2.confidence)
            self.assertEqual(c1.evidence_chain.to_dict(),
                             c2.evidence_chain.to_dict())

    def test_reasoning_id_prefix(self):
        out = reason(_BUG, _CTX, knowledge_nodes=_KNOWLEDGE,
                     experiences=_EXPS)
        self.assertTrue(out.reasoning_id.startswith("rs_"))
        self.assertEqual(len(out.reasoning_id), 3 + 64)

    def test_different_context_different_id(self):
        out1 = reason(_BUG, _CTX, knowledge_nodes=_KNOWLEDGE,
                      experiences=_EXPS)
        out2 = reason(_BUG, {"context_id": "ctx_other"},
                      knowledge_nodes=_KNOWLEDGE, experiences=_EXPS)
        self.assertEqual(out1.conclusions[0].confidence,
                         out2.conclusions[0].confidence)
        self.assertNotEqual(out1.reasoning_id, out2.reasoning_id)

    def test_output_serializable(self):
        out = reason(_BUG, _CTX, knowledge_nodes=_KNOWLEDGE,
                     experiences=_EXPS)
        d = out.to_dict()
        self.assertIn("reasoning_id", d)
        self.assertIn("conclusions", d)
        self.assertIn("contradictions", d)
        self.assertIn("insufficient_evidence", d)


if __name__ == "__main__":
    unittest.main()
