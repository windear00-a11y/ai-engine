"""Phase 3: retrieval ranks relevant experience by context similarity."""

import os
import tempfile
import unittest

from intelligence.context.schema import ContextSnapshot
from intelligence.context.store import ContextStore
from intelligence.evidence import EvidenceType, record_evidence
from intelligence.evidence.store import EvidenceStore
from intelligence.experience import (
    experience_for_task_type,
    synthesize_experience,
)
from intelligence.experience.store import ExperienceStore
from intelligence.outcome import record_outcome
from intelligence.outcome.store import OutcomeStore
from intelligence.outcome.types import OutcomeClassification


def _ctx(system_os):
    return ContextSnapshot.build(
        system={"os": system_os, "python": "3.11"},
        project={"language": "python"},
        task={"type": "bug_fix"},
        temporal={}, captured_at_epoch=0.0,
    ).context_id


class RetrievalByContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cdb = os.path.join(self.tmp.name, "context.db")
        self.ev_store = EvidenceStore(":memory:")
        self.oc_store = OutcomeStore(":memory:")
        self.xp_store = ExperienceStore(":memory:")
        self.addCleanup(self.ev_store.close)
        self.addCleanup(self.oc_store.close)
        self.addCleanup(self.xp_store.close)
        self.cstore = ContextStore(self.cdb)
        self.addCleanup(self.cstore.close)

    def test_context_ranking_and_resolution(self):
        query_ctx_id = _ctx("linux")
        other_ctx_id = _ctx("windows")
        # save both contexts in context.db
        for cid, os_name in ((query_ctx_id, "linux"), (other_ctx_id, "windows")):
            snap = ContextSnapshot.build(
                system={"os": os_name, "python": "3.11"},
                project={"language": "python"},
                task={"type": "bug_fix"}, temporal={},
                captured_at_epoch=0.0)
            self.cstore.save(snap)

        # two experiences: one in each context
        for cid in (query_ctx_id, other_ctx_id):
            ev = record_evidence("obs", "claim", cid, EvidenceType.FACT,
                                 store=self.ev_store)
            oc = record_outcome("t-" + cid, cid,
                                OutcomeClassification.SUCCESS,
                                [ev.evidence_id], store=self.oc_store)
            synthesize_experience("t-" + cid, cid, oc.outcome_id,
                                  [ev.evidence_id], task_type="bug_fix",
                                  store=self.xp_store)

        ranked = experience_for_task_type(
            "bug_fix", context_id=query_ctx_id, store=self.xp_store,
            context_db_path=self.cdb)
        self.assertEqual(len(ranked), 2)
        # the experience in the matching context ranks first
        self.assertEqual(ranked[0].context_id, query_ctx_id)


if __name__ == "__main__":
    unittest.main()
