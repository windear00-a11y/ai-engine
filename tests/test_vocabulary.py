"""Comprehensive tests for the universal type and relationship vocabulary.

Covers: vocabulary sets, helper functions, free-form type acceptance,
backward compatibility, custom types, empty/None rejection, ingestion
validator integration, and importer verifier integration.
"""

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from retrieval.vocabulary import (
    CORE_NODE_TYPES,
    DOMAIN_NODE_TYPES,
    ALL_RECOMMENDED_NODE_TYPES,
    CORE_RELATIONSHIP_KINDS,
    DOMAIN_RELATIONSHIP_KINDS,
    ALL_RECOMMENDED_RELATIONSHIP_KINDS,
    is_recommended_node_type,
    is_valid_node_type,
    is_empty_node_type,
    is_recommended_relationship_kind,
    is_valid_relationship_kind,
    is_empty_relationship_kind,
)
from retrieval.knowledge import VALID_TYPES, RELATIONSHIP_KINDS
from ingestion.validator import validate_source
from ingestion.source_format import build_source, build_node
from importing.verifier import verify_plan


# -- helpers ----------------------------------------------------------------

def _valid_plan_with_type(ntype):
    return {
        "source": "/tmp/test",
        "read_only_guard": {"sqlite_writes_forbidden": True},
        "preview": {
            "proposed_nodes": [{
                "id": "n1",
                "type": ntype,
                "name": "Node 1",
                "description": "desc",
                "provenance": [{
                    "candidate_id": "c1",
                    "document": "doc.rst",
                    "evidence": "evidence",
                }],
            }],
            "proposed_nodes_count": 1,
            "proposed_relationships": [],
            "proposed_relationships_count": 0,
        },
        "decisions": [],
        "identities": [],
    }


# -- vocabulary set definitions ---------------------------------------------

class CoreNodeTypesTests(unittest.TestCase):
    def test_core_node_types_are_frozenset(self):
        self.assertIsInstance(CORE_NODE_TYPES, frozenset)

    def test_core_contains_original_seven(self):
        expected = {"concept", "technology", "entity", "procedure",
                    "rule", "example", "dependency"}
        self.assertEqual(CORE_NODE_TYPES, expected)

    def test_core_has_seven_members(self):
        self.assertEqual(len(CORE_NODE_TYPES), 7)


class DomainNodeTypesTests(unittest.TestCase):
    def test_domain_node_types_are_frozenset(self):
        self.assertIsInstance(DOMAIN_NODE_TYPES, frozenset)

    def test_domain_contains_eight_types(self):
        expected = {"person", "company", "product", "document",
                    "event", "research_paper", "location", "discipline"}
        self.assertEqual(DOMAIN_NODE_TYPES, expected)

    def test_domain_has_eight_members(self):
        self.assertEqual(len(DOMAIN_NODE_TYPES), 8)


class AllRecommendedNodeTypesTests(unittest.TestCase):
    def test_is_union_of_core_and_domain(self):
        self.assertEqual(ALL_RECOMMENDED_NODE_TYPES,
                         CORE_NODE_TYPES | DOMAIN_NODE_TYPES)

    def test_has_fifteen_members(self):
        self.assertEqual(len(ALL_RECOMMENDED_NODE_TYPES), 15)

    def test_contains_all_core(self):
        for t in CORE_NODE_TYPES:
            self.assertIn(t, ALL_RECOMMENDED_NODE_TYPES)

    def test_contains_all_domain(self):
        for t in DOMAIN_NODE_TYPES:
            self.assertIn(t, ALL_RECOMMENDED_NODE_TYPES)

    def test_core_and_domain_are_disjoint(self):
        self.assertEqual(CORE_NODE_TYPES & DOMAIN_NODE_TYPES, set())


class CoreRelationshipKindsTests(unittest.TestCase):
    def test_core_relationship_kinds_are_frozenset(self):
        self.assertIsInstance(CORE_RELATIONSHIP_KINDS, frozenset)

    def test_core_contains_original_nine(self):
        expected = {"depends_on", "related_to", "part_of", "instance_of",
                    "implements", "extends", "uses", "example_of", "references"}
        self.assertEqual(CORE_RELATIONSHIP_KINDS, expected)

    def test_core_has_nine_members(self):
        self.assertEqual(len(CORE_RELATIONSHIP_KINDS), 9)


class DomainRelationshipKindsTests(unittest.TestCase):
    def test_domain_relationship_kinds_are_frozenset(self):
        self.assertIsInstance(DOMAIN_RELATIONSHIP_KINDS, frozenset)

    def test_domain_contains_seven_new_kinds(self):
        expected = {"works_at", "founded", "authored", "created",
                    "owns", "located_at", "cites"}
        self.assertEqual(DOMAIN_RELATIONSHIP_KINDS, expected)

    def test_domain_has_seven_members(self):
        self.assertEqual(len(DOMAIN_RELATIONSHIP_KINDS), 7)


class AllRecommendedRelationshipKindsTests(unittest.TestCase):
    def test_is_union_of_core_and_domain(self):
        self.assertEqual(ALL_RECOMMENDED_RELATIONSHIP_KINDS,
                         CORE_RELATIONSHIP_KINDS | DOMAIN_RELATIONSHIP_KINDS)

    def test_has_sixteen_members(self):
        self.assertEqual(len(ALL_RECOMMENDED_RELATIONSHIP_KINDS), 16)

    def test_contains_all_core(self):
        for k in CORE_RELATIONSHIP_KINDS:
            self.assertIn(k, ALL_RECOMMENDED_RELATIONSHIP_KINDS)

    def test_contains_all_domain(self):
        for k in DOMAIN_RELATIONSHIP_KINDS:
            self.assertIn(k, ALL_RECOMMENDED_RELATIONSHIP_KINDS)

    def test_core_and_domain_are_disjoint(self):
        self.assertEqual(CORE_RELATIONSHIP_KINDS & DOMAIN_RELATIONSHIP_KINDS, set())


# -- node type helpers ------------------------------------------------------

class IsRecommendedNodeTypeTests(unittest.TestCase):
    def test_core_types_are_recommended(self):
        for t in CORE_NODE_TYPES:
            self.assertTrue(is_recommended_node_type(t), t)

    def test_domain_types_are_recommended(self):
        for t in DOMAIN_NODE_TYPES:
            self.assertTrue(is_recommended_node_type(t), t)

    def test_custom_type_is_not_recommended(self):
        self.assertFalse(is_recommended_node_type("my_custom_type"))

    def test_empty_string_is_not_recommended(self):
        self.assertFalse(is_recommended_node_type(""))

    def test_none_is_not_recommended(self):
        self.assertFalse(is_recommended_node_type(None))

    def test_non_string_is_not_recommended(self):
        self.assertFalse(is_recommended_node_type(42))
        self.assertFalse(is_recommended_node_type([]))


class IsValidNodeTypeTests(unittest.TestCase):
    def test_core_types_are_valid(self):
        for t in CORE_NODE_TYPES:
            self.assertTrue(is_valid_node_type(t), t)

    def test_domain_types_are_valid(self):
        for t in DOMAIN_NODE_TYPES:
            self.assertTrue(is_valid_node_type(t), t)

    def test_custom_type_is_valid(self):
        self.assertTrue(is_valid_node_type("my_custom_type"))
        self.assertTrue(is_valid_node_type("anything_goes"))

    def test_whitespace_only_is_invalid(self):
        self.assertFalse(is_valid_node_type("   "))
        self.assertFalse(is_valid_node_type("\t"))
        self.assertFalse(is_valid_node_type("\n"))

    def test_empty_string_is_invalid(self):
        self.assertFalse(is_valid_node_type(""))

    def test_none_is_invalid(self):
        self.assertFalse(is_valid_node_type(None))

    def test_non_string_is_invalid(self):
        self.assertFalse(is_valid_node_type(42))
        self.assertFalse(is_valid_node_type([]))
        self.assertFalse(is_valid_node_type({}))

    def test_type_with_surrounding_whitespace_is_valid(self):
        self.assertTrue(is_valid_node_type("  concept  "))


class IsEmptyNodeTypeTests(unittest.TestCase):
    def test_none_is_empty(self):
        self.assertTrue(is_empty_node_type(None))

    def test_empty_string_is_empty(self):
        self.assertTrue(is_empty_node_type(""))

    def test_whitespace_only_is_empty(self):
        self.assertTrue(is_empty_node_type("   "))
        self.assertTrue(is_empty_node_type("\t"))
        self.assertTrue(is_empty_node_type("\n"))

    def test_nonempty_string_is_not_empty(self):
        self.assertFalse(is_empty_node_type("concept"))
        self.assertFalse(is_empty_node_type("x"))

    def test_non_string_is_not_empty(self):
        self.assertFalse(is_empty_node_type(42))
        self.assertFalse(is_empty_node_type([]))


# -- relationship kind helpers ----------------------------------------------

class IsRecommendedRelationshipKindTests(unittest.TestCase):
    def test_core_kinds_are_recommended(self):
        for k in CORE_RELATIONSHIP_KINDS:
            self.assertTrue(is_recommended_relationship_kind(k), k)

    def test_domain_kinds_are_recommended(self):
        for k in DOMAIN_RELATIONSHIP_KINDS:
            self.assertTrue(is_recommended_relationship_kind(k), k)

    def test_custom_kind_is_not_recommended(self):
        self.assertFalse(is_recommended_relationship_kind("custom_rel"))

    def test_empty_string_is_not_recommended(self):
        self.assertFalse(is_recommended_relationship_kind(""))

    def test_none_is_not_recommended(self):
        self.assertFalse(is_recommended_relationship_kind(None))


class IsValidRelationshipKindTests(unittest.TestCase):
    def test_core_kinds_are_valid(self):
        for k in CORE_RELATIONSHIP_KINDS:
            self.assertTrue(is_valid_relationship_kind(k), k)

    def test_domain_kinds_are_valid(self):
        for k in DOMAIN_RELATIONSHIP_KINDS:
            self.assertTrue(is_valid_relationship_kind(k), k)

    def test_custom_kind_is_valid(self):
        self.assertTrue(is_valid_relationship_kind("my_custom_rel"))

    def test_empty_string_is_invalid(self):
        self.assertFalse(is_valid_relationship_kind(""))

    def test_whitespace_only_is_invalid(self):
        self.assertFalse(is_valid_relationship_kind("   "))

    def test_none_is_invalid(self):
        self.assertFalse(is_valid_relationship_kind(None))


class IsEmptyRelationshipKindTests(unittest.TestCase):
    def test_none_is_empty(self):
        self.assertTrue(is_empty_relationship_kind(None))

    def test_empty_string_is_empty(self):
        self.assertTrue(is_empty_relationship_kind(""))

    def test_whitespace_only_is_empty(self):
        self.assertTrue(is_empty_relationship_kind("   "))

    def test_nonempty_string_is_not_empty(self):
        self.assertFalse(is_empty_relationship_kind("related_to"))


# -- backward compatibility -------------------------------------------------

class BackwardCompatibilityTests(unittest.TestCase):
    def test_valid_types_still_contains_core_types(self):
        for t in CORE_NODE_TYPES:
            self.assertIn(t, VALID_TYPES)

    def test_valid_types_still_contains_domain_types(self):
        for t in DOMAIN_NODE_TYPES:
            self.assertIn(t, VALID_TYPES)

    def test_valid_types_equals_recommended(self):
        self.assertEqual(VALID_TYPES, ALL_RECOMMENDED_NODE_TYPES)

    def test_relationship_kinds_still_contains_core(self):
        for k in CORE_RELATIONSHIP_KINDS:
            self.assertIn(k, RELATIONSHIP_KINDS)

    def test_relationship_kinds_still_contains_domain(self):
        for k in DOMAIN_RELATIONSHIP_KINDS:
            self.assertIn(k, RELATIONSHIP_KINDS)

    def test_relationship_kinds_equals_recommended(self):
        self.assertEqual(RELATIONSHIP_KINDS, ALL_RECOMMENDED_RELATIONSHIP_KINDS)


# -- ingestion validator integration ----------------------------------------

class IngestionValidatorFreeFormTests(unittest.TestCase):
    def test_core_type_accepted(self):
        data = build_source("s", [build_node("a", "concept", "A", "d")])
        self.assertTrue(validate_source(data).valid)

    def test_domain_type_accepted(self):
        data = build_source("s", [build_node("a", "person", "A", "d")])
        self.assertTrue(validate_source(data).valid)

    def test_custom_type_accepted(self):
        data = build_source("s", [build_node("a", "my_custom_type", "A", "d")])
        self.assertTrue(validate_source(data).valid)

    def test_empty_type_rejected(self):
        data = build_source("s", [build_node("a", "", "A", "d")])
        res = validate_source(data)
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "node_type" for e in res.errors))

    def test_none_type_rejected(self):
        data = build_source("s", [build_node("a", None, "A", "d")])
        res = validate_source(data)
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "node_type" for e in res.errors))

    def test_whitespace_type_rejected(self):
        data = build_source("s", [build_node("a", "   ", "A", "d")])
        res = validate_source(data)
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "node_type" for e in res.errors))

    def test_type_lowercased_in_output(self):
        data = build_source("s", [build_node("a", "Person", "A", "d")])
        res = validate_source(data)
        self.assertTrue(res.valid)
        self.assertEqual(res.nodes[0]["type"], "person")

    def test_all_domain_types_valid(self):
        for t in DOMAIN_NODE_TYPES:
            data = build_source("s", [build_node("n", t, "N", "d")])
            self.assertTrue(validate_source(data).valid, f"type {t} should validate")

    def test_all_core_types_still_valid(self):
        for t in CORE_NODE_TYPES:
            data = build_source("s", [build_node("n", t, "N", "d")])
            self.assertTrue(validate_source(data).valid, f"type {t} should validate")


# -- importer verifier integration ------------------------------------------

class VerifierFreeFormTests(unittest.TestCase):
    def test_core_type_passes(self):
        plan = _valid_plan_with_type("concept")
        result = verify_plan(plan)
        self.assertTrue(result.valid)
        self.assertEqual(result.warnings, [])

    def test_domain_type_passes_with_no_warning(self):
        plan = _valid_plan_with_type("person")
        result = verify_plan(plan)
        self.assertTrue(result.valid)
        self.assertEqual(result.warnings, [])

    def test_custom_type_passes_with_warning(self):
        plan = _valid_plan_with_type("my_custom_type")
        result = verify_plan(plan)
        self.assertTrue(result.valid)
        self.assertTrue(any(w.code == "node_type" for w in result.warnings))

    def test_empty_type_fails(self):
        plan = _valid_plan_with_type("")
        result = verify_plan(plan)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "node_type" for e in result.errors))

    def test_none_type_fails(self):
        plan = _valid_plan_with_type(None)
        result = verify_plan(plan)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "node_type" for e in result.errors))

    def test_custom_relationship_produces_warning(self):
        plan = _valid_plan_with_type("concept")
        plan["preview"]["proposed_relationships"] = [{
            "source_node_id": "n1",
            "relationship_type": "custom_rel",
            "target_node_id": "n1",
        }]
        plan["preview"]["proposed_relationships_count"] = 1
        result = verify_plan(plan)
        self.assertTrue(result.valid)
        self.assertTrue(any(w.code == "relationship_kind" for w in result.warnings))


if __name__ == "__main__":
    unittest.main()
