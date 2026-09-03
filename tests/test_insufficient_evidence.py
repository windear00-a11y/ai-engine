"""Phase 6: questions without enough evidence are flagged."""

import unittest

from intelligence.reasoning import reason, ReasoningQuery, InsufficientEvidence

_CTX = {"context_id": "ctx_a"}


class InsufficientEvidenceTests(unittest.TestCase):
    def _query(self):
        return ReasoningQuery(question="unfamiliar task",
                              task_type="exotic", domain="unknown",
                              target={})

    def test_no_inputs_flags_insufficiency(self):
        out = reason(self._query(), _CTX, knowledge_nodes=[],
                     experiences=[])
        self.assertEqual(out.conclusions, [])
        self.assertEqual(len(out.insufficient_evidence), 1)
        flag = out.insufficient_evidence[0]
        self.assertIsInstance(flag, InsufficientEvidence)
        self.assertIn("knowledge", flag.missing)
        self.assertIn("experience", flag.missing)

    def test_irrelevant_knowledge_only_flags_experience(self):
        knowledge = [{
            "id": "k", "type": "fact", "name": "unrelated rule",
            "subject": "other", "lifecycle": {"confidence": 0.9},
        }]
        out = reason(self._query(), _CTX, knowledge_nodes=knowledge)
        # irrelevant knowledge is not usable support, so no conclusion and
        # both knowledge and experience are flagged as missing support.
        self.assertEqual(out.conclusions, [])
        self.assertEqual(len(out.insufficient_evidence), 1)
        flag = out.insufficient_evidence[0]
        self.assertIn("knowledge", flag.missing)
        self.assertIn("experience", flag.missing)

    def test_reported_not_error(self):
        # Insufficiency is reported in the output structure, not an exception.
        out = reason(self._query(), _CTX)
        self.assertEqual(out.contradictions, [])
        self.assertGreaterEqual(len(out.insufficient_evidence), 1)


if __name__ == "__main__":
    unittest.main()
