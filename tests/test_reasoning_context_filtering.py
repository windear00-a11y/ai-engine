"""Phase 6: inapplicable knowledge/experience are filtered out."""

import unittest

from intelligence.reasoning import reason, ReasoningQuery

_CTX = {"context_id": "ctx_a"}


class _Exp:
    def __init__(self, task_type, context_id, eid):
        self.task_type = task_type
        self.domain = "lint"
        self.context_id = context_id
        self.summary = {"outcome": "success_verified", "context_match": 0.95}
        self.experience_id = eid


class ReasoningContextFilteringTests(unittest.TestCase):
    def _query(self):
        return ReasoningQuery(question="q", task_type="bug_fix", domain="lint",
                              target={"error": "E302"})

    def test_invalidated_knowledge_excluded(self):
        knowledge = [{
            "id": "k1", "type": "fact",
            "name": "E302 fixed by inserting blank line",
            "subject": "e302",
            "lifecycle": {"confidence": 0.9, "status": "invalidated"},
        }]
        out = reason(self._query(), _CTX, knowledge_nodes=knowledge)
        self.assertEqual(out.conclusions, [])
        self.assertEqual(len(out.insufficient_evidence), 1)

    def test_superseded_knowledge_excluded(self):
        knowledge = [{
            "id": "k1", "type": "fact",
            "name": "E302 fixed by inserting blank line",
            "subject": "e302",
            "lifecycle": {"confidence": 0.9, "status": "superseded",
                          "superseded_by": "k2"},
        }]
        out = reason(self._query(), _CTX, knowledge_nodes=knowledge)
        self.assertEqual(out.conclusions, [])

    def test_context_restricted_knowledge_excluded(self):
        knowledge = [{
            "id": "k1", "type": "fact",
            "name": "E302 fixed by inserting blank line",
            "subject": "e302",
            "lifecycle": {"confidence": 0.9,
                          "context_restrictions": {
                              "allowed_contexts": ["ctx_other"]}},
        }]
        out = reason(self._query(), _CTX, knowledge_nodes=knowledge)
        self.assertEqual(out.conclusions, [])
        # same node is applicable when context matches the restriction
        out_ok = reason(self._query(), {"context_id": "ctx_other"},
                        knowledge_nodes=knowledge)
        self.assertEqual(len(out_ok.conclusions), 1)

    def test_wrong_task_type_experience_excluded(self):
        exps = [_Exp("test_verify", "ctx_a", "xp1")]
        out = reason(self._query(), _CTX, experiences=exps)
        self.assertEqual(out.conclusions, [])


if __name__ == "__main__":
    unittest.main()
