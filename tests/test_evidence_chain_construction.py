"""Phase 6: evidence chains are traceable and complete."""

import unittest

from intelligence.reasoning import build_chain
from intelligence.reasoning import EvidenceChain, EvidenceStep


class EvidenceChainConstructionTests(unittest.TestCase):
    def test_chain_tracks_steps(self):
        chain = build_chain([
            EvidenceStep("k1", "knowledge", "X", 0.9),
            EvidenceStep("xp1", "experience", "X", 0.8),
        ])
        self.assertEqual(chain.depth, 2)
        self.assertEqual([s.source_type for s in chain.steps],
                         ["knowledge", "experience"])
        self.assertEqual([s.source_id for s in chain.steps], ["k1", "xp1"])

    def test_chain_quality_increases_with_supports(self):
        c1 = build_chain([EvidenceStep("a", "knowledge", "x", 0.9)])
        c2 = build_chain([EvidenceStep("a", "knowledge", "x", 0.9),
                          EvidenceStep("b", "experience", "x", 0.9)])
        c3 = build_chain([EvidenceStep("a", "knowledge", "x", 0.9),
                          EvidenceStep("b", "experience", "x", 0.9),
                          EvidenceStep("c", "experience", "x", 0.9)])
        self.assertLess(c1.chain_quality, c2.chain_quality)
        self.assertLessEqual(c2.chain_quality, c3.chain_quality)
        self.assertLessEqual(c3.chain_quality, 1.0)

    def test_depth_bounded(self):
        steps = [EvidenceStep(f"s{i}", "experience", "x", 0.9)
                 for i in range(20)]
        chain = build_chain(steps)
        self.assertLessEqual(chain.depth, EvidenceChain.MAX_DEPTH)
        self.assertLessEqual(chain.depth, 5)

    def test_duplicate_source_counts_once(self):
        single = build_chain([EvidenceStep("s1", "knowledge", "x", 0.9)])
        dup = build_chain([EvidenceStep("s1", "knowledge", "x", 0.9),
                           EvidenceStep("s1", "knowledge", "x", 0.9)])
        self.assertEqual(single.chain_quality, dup.chain_quality)

    def test_empty_chain_low_confidence(self):
        chain = build_chain([])
        self.assertEqual(chain.propagated_confidence, 0.0)

    def test_chain_serializable(self):
        chain = build_chain([EvidenceStep("a", "knowledge", "x", 0.9)])
        d = chain.to_dict()
        self.assertEqual(d["depth"], 1)
        self.assertEqual(len(d["steps"]), 1)


if __name__ == "__main__":
    unittest.main()
