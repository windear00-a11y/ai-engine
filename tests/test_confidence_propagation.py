"""Phase 6: confidence propagates through chains per the Canonical Model (R2)."""

import unittest

from intelligence.reasoning import build_chain
from intelligence.reasoning import EvidenceStep


class ConfidencePropagationTests(unittest.TestCase):
    def test_rule_r2_bound(self):
        # conclusion confidence <= min(supporting confidence) * chain_quality
        chain = build_chain([
            EvidenceStep("a", "knowledge", "x", 0.9),
            EvidenceStep("b", "experience", "x", 0.8),
        ])
        min_conf = chain.min_support_confidence
        self.assertTrue(chain.propagated_confidence <=
                        chain.chain_quality)
        self.assertTrue(chain.propagated_confidence <= min_conf)
        self.assertEqual(chain.propagated_confidence,
                         round(min_conf * chain.chain_quality, 6))

    def test_weakest_link_bounds(self):
        strong = build_chain([EvidenceStep("a", "knowledge", "x", 0.9),
                              EvidenceStep("b", "experience", "x", 0.9)])
        weak = build_chain([EvidenceStep("a", "knowledge", "x", 0.9),
                            EvidenceStep("b", "experience", "x", 0.3)])
        # same number of supports -> same quality, but weaker min -> lower conf
        self.assertEqual(strong.chain_quality, weak.chain_quality)
        self.assertGreater(strong.propagated_confidence,
                           weak.propagated_confidence)

    def test_single_knowledge_support(self):
        chain = build_chain([EvidenceStep("a", "knowledge", "x", 0.85)])
        self.assertEqual(chain.chain_quality, 0.80)
        self.assertEqual(chain.propagated_confidence,
                         round(0.85 * 0.80, 6))

    def test_double_support_quality(self):
        chain = build_chain([EvidenceStep("a", "knowledge", "x", 0.5),
                             EvidenceStep("b", "experience", "x", 0.5)])
        self.assertEqual(chain.chain_quality, 0.95)
        self.assertEqual(chain.propagated_confidence,
                         round(0.5 * 0.95, 6))

    def test_pure_function(self):
        steps = [EvidenceStep("a", "knowledge", "x", 0.8),
                 EvidenceStep("b", "experience", "x", 0.9)]
        c1 = build_chain(steps)
        c2 = build_chain(list(steps))
        self.assertEqual(c1.propagated_confidence, c2.propagated_confidence)


if __name__ == "__main__":
    unittest.main()
