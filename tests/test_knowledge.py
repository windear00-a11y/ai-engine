import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from retrieval.knowledge import KnowledgeStore, VALID_TYPES

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
VALID_DIR = os.path.join(FIXTURES, "valid")
INVALID_DIR = os.path.join(FIXTURES, "invalid")
KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), "..", "knowledge")


class TestLoadingKnowledge(unittest.TestCase):
    def test_loads_valid_fixtures(self):
        store = KnowledgeStore().load(VALID_DIR)
        self.assertEqual(len(store.errors), 0)
        self.assertEqual(set(store.nodes), {"valid-concept", "valid-technology"})
        self.assertEqual(store.get("valid-concept")["type"], "concept")

    def test_loads_real_knowledge_dir(self):
        store = KnowledgeStore().load(KNOWLEDGE_DIR)
        self.assertEqual(store.errors, [])
        ids = set(store.nodes)
        self.assertIn("react", ids)
        self.assertIn("react-component", ids)
        self.assertIn("react-component-basic-example", ids)
        self.assertIn("react-depends-react-dom", ids)
        # every loaded node keeps its declared type
        for node in store.all():
            self.assertIn(node["type"], VALID_TYPES)


class TestSearchingConcepts(unittest.TestCase):
    def setUp(self):
        self.store = KnowledgeStore().load(KNOWLEDGE_DIR)

    def test_search_finds_react_component(self):
        results = self.store.search("react component")
        top_ids = [item["id"] for _, item in results]
        self.assertIn("react-component", top_ids)

    def test_search_is_case_insensitive(self):
        results = self.store.search("REACT COMPONENT")
        ids = [item["id"] for _, item in results]
        self.assertIn("react-component", ids)

    def test_search_no_match_returns_empty(self):
        results = self.store.search("zzz-nonexistent-term")
        self.assertEqual(results, [])

    def test_search_results_sorted_by_score(self):
        results = self.store.search("react")
        scores = [score for score, _ in results]
        self.assertEqual(scores, sorted(scores, reverse=True))


class TestFollowingRelationships(unittest.TestCase):
    def setUp(self):
        self.store = KnowledgeStore().load(KNOWLEDGE_DIR)

    def test_relationships_of_returns_structured_objects(self):
        rels = self.store.relationships_of("react-component")
        self.assertTrue(all("type" in r and "target" in r for r in rels))

    def test_follow_resolves_existing_target(self):
        pairs = self.store.follow("react-component", rel_type="instance_of")
        self.assertEqual(len(pairs), 1)
        rel, target = pairs[0]
        self.assertEqual(rel["target"], "react")
        self.assertEqual(target["id"], "react")
        self.assertEqual(target["type"], "technology")

    def test_follow_filters_by_type(self):
        pairs = self.store.follow("react-component", rel_type="depends_on")
        self.assertEqual(pairs, [])

    def test_follow_skips_unresolved_targets(self):
        # react-component references "jsx" which is not a loaded node
        pairs = self.store.follow("react-component")
        resolved_ids = [t["id"] for _, t in pairs]
        self.assertIn("react", resolved_ids)
        self.assertNotIn("jsx", resolved_ids)

    def test_example_links_back_to_concept(self):
        pairs = self.store.follow("react-component-basic-example", rel_type="example_of")
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][1]["id"], "react-component")

    def test_follow_unknown_node_returns_empty(self):
        self.assertEqual(self.store.follow("does-not-exist"), [])


class TestInvalidKnowledgeFiles(unittest.TestCase):
    def test_invalid_files_are_reported_not_loaded(self):
        store = KnowledgeStore().load(INVALID_DIR)
        # 4 invalid fixtures: not_json, missing_id, bad_type, bad_relationship
        self.assertEqual(len(store.errors), 4)
        self.assertEqual(len(store.nodes), 0)

    def test_loader_does_not_crash_on_invalid(self):
        store = KnowledgeStore().load(INVALID_DIR)
        self.assertIsInstance(store.all(), list)

    def test_valid_and_invalid_mixed(self):
        store = KnowledgeStore().load(FIXTURES)
        loaded_ids = set(store.nodes)
        self.assertIn("valid-concept", loaded_ids)
        self.assertIn("valid-technology", loaded_ids)
        self.assertEqual(len(store.errors), 4)


if __name__ == "__main__":
    unittest.main()
