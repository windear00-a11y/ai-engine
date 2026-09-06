"""Phase 25 — Deterministic Experience Generalization.

Verified semantics:
  A. Repeated successful experiences -> exactly one generalized strategy.
  B. Mixed outcomes -> one strategy with success/failure counts, not universal.
  C. Compatible context variation -> one strategy, context applicability kept.
  D. Materially incompatible context -> strategies remain distinguishable.
  E. Different approaches -> separate strategies.
  F. Order-independent processing -> identical ids/counts/confidence/provenance.
  G. Minimum evidence policy preserved (weak clusters are not promoted).
  H. Raw context_id is not a mandatory part of generalized strategy identity.

All computations are deterministic and advisory: no policy/approval effect.
"""

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _outcome_id(task_id, context_id, classification, evidence_id):
    from intelligence.outcome.schema import derive_outcome_id
    from intelligence.outcome.types import OutcomeClassification
    return derive_outcome_id(
        f"plan_{task_id}", context_id,
        OutcomeClassification(classification), [evidence_id], {},
    )


def _mk_experience(task_id, context_id, outcome_cls, situation="poor_sleep",
                   approach="evening_walk"):
    from intelligence.experience.schema import ExperienceRecord, derive_experience_id
    evidence_id = f"ev_{task_id}"
    oc_id = _outcome_id(task_id, context_id, outcome_cls, evidence_id)
    summary = {
        "situation": {"problem": situation},
        "approach": approach,
        "outcome": outcome_cls,
    }
    exp_id = derive_experience_id(task_id, context_id, oc_id, [evidence_id], None)
    return ExperienceRecord(
        experience_id=exp_id,
        task_id=task_id,
        task_type="generic",
        domain="generic",
        context_id=context_id,
        outcome_id=oc_id,
        evidence_ids=(evidence_id,),
        strategy_id=None,
        summary=summary,
        synthesized_at_epoch=1.0,
    )


def _mk_outcomes(experiences):
    from intelligence.outcome.schema import Outcome
    from intelligence.outcome.types import OutcomeClassification
    result = {}
    for e in experiences:
        summary = e.summary or {}
        cls = OutcomeClassification(str(summary.get("outcome", "")).lower())
        result[e.outcome_id] = Outcome(
            outcome_id=e.outcome_id,
            plan_id=f"plan_{e.task_id}",
            context_id=e.context_id,
            classification=cls,
            verification_evidence_ids=e.evidence_ids,
            created_at_epoch=1.0,
        )
    return result


def _snap(context_id, environment, project=None, source=None):
    from intelligence.context.schema import ContextSnapshot
    return ContextSnapshot(
        environment=environment,
        project=project or {"project_id": "habits"},
        source=source or {"adapter": "manual"},
        actor={},
        spatial={},
        social={},
        affective={},
        temporal={},
        context_id=context_id,
        captured_at_epoch=1.0,
    )


def _compatible_snapshots():
    a = _snap("ctx_habits_mon", {"os": "linux", "device": "local", "session": 1})
    b = _snap("ctx_habits_tue", {"os": "linux", "device": "local", "session": 2})
    c = _snap("ctx_habits_wed", {"os": "linux", "device": "local", "session": 3})
    z = _snap("ctx_other_matrix", {"platform": "win32"}, project={"project_id": "other"}, source={"adapter": "file"})
    return a, b, c, z


class RepeatedSuccessGeneralizationTests(unittest.TestCase):
    """A. Repeated successful experiences -> exactly one generalized strategy."""

    def test_three_repeated_successes_one_strategy(self):
        from ai_engine.generic_learning import generalize_strategies_from_experiences
        a, b, c, z = _compatible_snapshots()
        exps = [_mk_experience(f"t{i}", a.context_id, "success") for i in range(3)]
        outcomes = _mk_outcomes(exps)
        strategies = generalize_strategies_from_experiences(
            exps, outcomes=outcomes, context_snapshots=[a, b, c, z])
        self.assertEqual(len(strategies), 1)
        s = strategies[0]
        self.assertEqual(s["success_count"], 3)
        self.assertEqual(s["failure_count"], 0)
        self.assertEqual(s["sample_count"], 3)
        self.assertEqual(len(s["supporting_experience_ids"]), 3)
        self.assertEqual(len(s["provenance"]["experience_ids"]), 3)
        self.assertTrue(s["universally_successful"])
        self.assertEqual(s["context_restrictions"]["allowed_contexts"], [a.context_id])
        self.assertEqual(sorted(s["supporting_experience_ids"]), sorted(e.experience_id for e in exps))


class MixedOutcomeGeneralizationTests(unittest.TestCase):
    """B. success + success + failure -> one strategy, counts, not universal."""

    def test_mixed_outcomes_aggregate(self):
        from ai_engine.generic_learning import generalize_strategies_from_experiences
        from intelligence.strategy.types import candidate_confidence
        a, b, c, z = _compatible_snapshots()
        exps = [
            _mk_experience("mix_ok1", a.context_id, "success"),
            _mk_experience("mix_ok2", a.context_id, "success"),
            _mk_experience("mix_fail", a.context_id, "failure"),
        ]
        outcomes = _mk_outcomes(exps)
        strategies = generalize_strategies_from_experiences(
            exps, outcomes=outcomes, context_snapshots=[a, b, c, z])
        self.assertEqual(len(strategies), 1)
        s = strategies[0]
        self.assertEqual(s["success_count"], 2)
        self.assertEqual(s["failure_count"], 1)
        self.assertFalse(s["universally_successful"])
        self.assertNotEqual(s["success_rate"], 1.0)
        expected_confidence = candidate_confidence(3, 2 / 3, 3)
        self.assertEqual(s["confidence"], expected_confidence)
        self.assertEqual(len(s["provenance"]["experience_ids"]), 3)


class CompatibleContextVariationTests(unittest.TestCase):
    """C. Same S+A across compatible contexts -> one strategy, applicability kept."""

    def test_compatible_contexts_merge(self):
        from ai_engine.generic_learning import generalize_strategies_from_experiences
        a, b, c, z = _compatible_snapshots()
        exps = [
            _mk_experience("comp_1", a.context_id, "success"),
            _mk_experience("comp_2", b.context_id, "success"),
            _mk_experience("comp_3", c.context_id, "success"),
        ]
        outcomes = _mk_outcomes(exps)
        strategies = generalize_strategies_from_experiences(
            exps, outcomes=outcomes, context_snapshots=[a, b, c, z])
        self.assertEqual(len(strategies), 1)
        s = strategies[0]
        self.assertEqual(s["success_count"], 3)
        self.assertEqual(sorted(s["applicable_contexts"]),
                         sorted([a.context_id, b.context_id, c.context_id]))
        self.assertEqual(sorted(s["context_restrictions"]["allowed_contexts"]),
                         sorted([a.context_id, b.context_id, c.context_id]))

    def test_context_applicability_represented_separately(self):
        from ai_engine.generic_learning import generalize_strategies_from_experiences
        a, b, c, z = _compatible_snapshots()
        exps = [
            _mk_experience("sep_1", a.context_id, "success"),
            _mk_experience("sep_2", b.context_id, "success"),
            _mk_experience("sep_3", c.context_id, "success"),
        ]
        outcomes = _mk_outcomes(exps)
        strategies = generalize_strategies_from_experiences(
            exps, outcomes=outcomes, context_snapshots=[a, b, c, z])
        s = strategies[0]
        self.assertEqual(s["situation_pattern"], "poor_sleep")
        self.assertEqual(s["approach_pattern"], "evening_walk")
        self.assertEqual(s["strategy_id"], _expected_id("poor_sleep", "evening_walk", None))


class IncompatibleContextTests(unittest.TestCase):
    """D. Materially incompatible context must not be blindly merged."""

    def test_incompatible_contexts_stay_distinct(self):
        from ai_engine.generic_learning import generalize_strategies_from_experiences
        a, b, c, z = _compatible_snapshots()
        in_a = [_mk_experience(f"da_{i}", a.context_id, "success") for i in range(3)]
        in_z = [_mk_experience(f"dz_{i}", z.context_id, "success") for i in range(3)]
        exps = in_a + in_z
        outcomes = _mk_outcomes(exps)
        strategies = generalize_strategies_from_experiences(
            exps, outcomes=outcomes, context_snapshots=[a, b, c, z])
        self.assertEqual(len(strategies), 2)
        for s in strategies:
            self.assertEqual(s["situation_pattern"], "poor_sleep")
            self.assertEqual(s["approach_pattern"], "evening_walk")
            self.assertEqual(s["success_count"], 3)
        allowed_sets = {tuple(sorted(s["context_restrictions"]["allowed_contexts"])) for s in strategies}
        self.assertEqual(len(allowed_sets), 2)
        ids = {s["strategy_id"] for s in strategies}
        self.assertEqual(len(ids), 2)
        self.assertIn((a.context_id,), allowed_sets)
        self.assertIn((z.context_id,), allowed_sets)

    def test_no_blind_merge_without_snapshots(self):
        from ai_engine.generic_learning import generalize_strategies_from_experiences
        exps = [
            _mk_experience("nb_1", "ctx_x1", "success"),
            _mk_experience("nb_2", "ctx_x2", "success"),
            _mk_experience("nb_3", "ctx_x3", "success"),
        ]
        outcomes = _mk_outcomes(exps)
        strategies = generalize_strategies_from_experiences(exps, outcomes=outcomes, context_snapshots=None)
        self.assertEqual(len(strategies), 1)
        s = strategies[0]
        self.assertEqual(sorted(s["applicable_contexts"]), ["ctx_x1", "ctx_x2", "ctx_x3"])
        self.assertEqual(s["sample_count"], 3)


class DifferentApproachTests(unittest.TestCase):
    """E. S+A and S+B -> separate strategies."""

    def test_different_approaches_remain_separate(self):
        from ai_engine.generic_learning import generalize_strategies_from_experiences
        a, b, c, z = _compatible_snapshots()
        exps = ([_mk_experience(f"ea_{i}", a.context_id, "success", approach="evening_walk") for i in range(3)]
                + [_mk_experience(f"eb_{i}", a.context_id, "success", approach="morning_jog") for i in range(3)])
        outcomes = _mk_outcomes(exps)
        strategies = generalize_strategies_from_experiences(
            exps, outcomes=outcomes, context_snapshots=[a, b, c, z])
        self.assertEqual(len(strategies), 2)
        patterns = {s["approach_pattern"] for s in strategies}
        self.assertEqual(patterns, {"evening_walk", "morning_jog"})


class DeterministicOrderIndependenceTests(unittest.TestCase):
    """F. Same experiences in different orders -> identical results."""

    def test_order_independent_results(self):
        from ai_engine.generic_learning import generalize_strategies_from_experiences
        a, b, c, z = _compatible_snapshots()
        exps = [
            _mk_experience("od_1", a.context_id, "success", approach="evening_walk"),
            _mk_experience("od_2", b.context_id, "success", approach="evening_walk"),
            _mk_experience("od_3", c.context_id, "failure", approach="evening_walk"),
            _mk_experience("od_4", a.context_id, "success", approach="morning_jog"),
            _mk_experience("od_5", a.context_id, "success", approach="morning_jog"),
            _mk_experience("od_6", a.context_id, "failure", approach="morning_jog"),
        ]
        outcomes = _mk_outcomes(exps)
        snapshots = [a, b, c, z]
        orderings = [exps[:], list(reversed(exps)), exps[3:] + exps[:3]]
        results = []
        for order in orderings:
            strategies = generalize_strategies_from_experiences(
                order, outcomes=outcomes, context_snapshots=snapshots)
            results.append(strategies)
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])
        base = {s["strategy_id"]: s for s in results[0]}
        self.assertEqual(len(base), len(results[0]))
        # Identical ids, counts, confidence, provenance across orders.
        for r in results[1:]:
            for s in r:
                self.assertIn(s["strategy_id"], base)
                self.assertEqual(s["success_count"], base[s["strategy_id"]]["success_count"])
                self.assertEqual(s["failure_count"], base[s["strategy_id"]]["failure_count"])
                self.assertEqual(s["confidence"], base[s["strategy_id"]]["confidence"])
                self.assertEqual(s["provenance"], base[s["strategy_id"]]["provenance"])


class MinimumEvidenceTests(unittest.TestCase):
    """G. Minimum-evidence policy preserved: weak clusters are not promoted."""

    def test_single_experience_not_promoted(self):
        from ai_engine.generic_learning import generalize_strategies_from_experiences
        a, b, c, z = _compatible_snapshots()
        exps = [_mk_experience("weak_1", a.context_id, "success")]
        strategies = generalize_strategies_from_experiences(
            exps, outcomes=_mk_outcomes(exps), context_snapshots=[a, b, c, z])
        self.assertEqual(strategies, [])

    def test_two_experiences_not_promoted(self):
        from ai_engine.generic_learning import generalize_strategies_from_experiences
        a, b, c, z = _compatible_snapshots()
        exps = [_mk_experience("weak2_1", a.context_id, "success"),
                _mk_experience("weak2_2", a.context_id, "success")]
        strategies = generalize_strategies_from_experiences(
            exps, outcomes=_mk_outcomes(exps), context_snapshots=[a, b, c, z])
        self.assertEqual(strategies, [])

    def test_three_experiences_promoted(self):
        from ai_engine.generic_learning import generalize_strategies_from_experiences
        a, b, c, z = _compatible_snapshots()
        exps = [_mk_experience("ok_1", a.context_id, "success"),
                _mk_experience("ok_2", a.context_id, "success"),
                _mk_experience("ok_3", a.context_id, "success")]
        strategies = generalize_strategies_from_experiences(
            exps, outcomes=_mk_outcomes(exps), context_snapshots=[a, b, c, z])
        self.assertEqual(len(strategies), 1)


class IdentityIsContextIndependentTests(unittest.TestCase):
    """H. Raw context_id is not a mandatory part of generalized identity."""

    def _expected(self, situation, approach):
        import hashlib
        import json
        def _canon(obj):
            return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        digest = hashlib.sha256(_canon({"situation": situation, "approach": approach}).encode("utf-8")).hexdigest()
        return "st_" + digest[:32]

    def test_learn_single_shot_same_identity_across_compatible_contexts(self):
        from ai_engine.generic_learning import learn_from_generic_experience
        from intelligence.outcome.schema import Outcome
        from intelligence.outcome.types import OutcomeClassification
        a, b, c, z = _compatible_snapshots()
        exps = [
            _mk_experience("id_ctx_a", a.context_id, "success"),
            _mk_experience("id_ctx_b", b.context_id, "success"),
        ]
        ids = []
        for e in exps:
            oc = Outcome(outcome_id=e.outcome_id, plan_id=f"plan_{e.task_id}",
                         context_id=e.context_id,
                         classification=OutcomeClassification.SUCCESS,
                         verification_evidence_ids=e.evidence_ids,
                         created_at_epoch=1.0)
            res = learn_from_generic_experience(e, oc)
            ids.append(res["strategy_candidate"]["strategy_id"])
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(ids[0], self._expected("poor_sleep", "evening_walk"))
        self.assertNotIn(a.context_id, ids[0])

    def test_compatible_context_ids_are_distinct(self):
        a, b, c, z = _compatible_snapshots()
        self.assertEqual(len({a.context_id, b.context_id, c.context_id, z.context_id}), 4)


def _expected_id(situation, approach, disambiguator):
    import hashlib
    import json
    def _canon(obj):
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    payload = {"situation": situation, "approach": approach}
    if disambiguator is not None:
        payload["disambiguator"] = disambiguator
    digest = hashlib.sha256(_canon(payload).encode("utf-8")).hexdigest()
    return "st_" + digest[:32]


if __name__ == "__main__":
    unittest.main()