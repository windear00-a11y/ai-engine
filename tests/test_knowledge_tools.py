import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from retrieval.knowledge import KnowledgeStore
from tools.knowledge_tools import KnowledgeTools

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
VALID_DIR = os.path.join(FIXTURES, "valid")
TOOLS_DIR = os.path.join(FIXTURES, "tools")
KNOWLEDGE_DIR = os.path.join(FIXTURES, "knowledge_react")


def tools_from(directory):
    return KnowledgeTools(store=KnowledgeStore().load(directory))


class TestSearchTool(unittest.TestCase):
    def setUp(self):
        self.tools = tools_from(KNOWLEDGE_DIR)

    def test_returns_ranked_structured_results(self):
        res = self.tools.search("react component")
        self.assertEqual(res["query"], "react component")
        self.assertGreater(res["count"], 0)
        self.assertIn("react-component", [r["id"] for r in res["results"]])
        for r in res["results"]:
            self.assertIn("id", r)
            self.assertIn("name", r)
            self.assertIn("type", r)
            self.assertIn("description", r)
            self.assertIn("score", r)

    def test_results_sorted_by_score_descending(self):
        res = self.tools.search("react")
        scores = [r["score"] for r in res["results"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_limit_is_respected(self):
        res = self.tools.search("react", limit=1)
        self.assertEqual(len(res["results"]), 1)

    def test_empty_query_returns_error(self):
        res = self.tools.search("   ")
        self.assertEqual(res["count"], 0)
        self.assertIn("error", res)
        self.assertEqual(res["results"], [])

    def test_non_string_query_returns_error(self):
        res = self.tools.search(123)
        self.assertIn("error", res)

    def test_no_match_returns_empty(self):
        res = self.tools.search("zzz-nonexistent")
        self.assertEqual(res["count"], 0)


class TestGetTool(unittest.TestCase):
    def setUp(self):
        self.tools = tools_from(KNOWLEDGE_DIR)

    def test_get_existing_node(self):
        res = self.tools.get("react")
        self.assertTrue(res["found"])
        self.assertEqual(res["id"], "react")
        self.assertEqual(res["type"], "technology")
        self.assertIn("relationships", res)
        self.assertIn("source", res)

    def test_get_unknown_id(self):
        res = self.tools.get("does-not-exist")
        self.assertFalse(res["found"])
        self.assertIn("error", res)

    def test_get_invalid_id_type(self):
        res = self.tools.get("")
        self.assertFalse(res["found"])
        self.assertIn("error", res)

    def test_get_non_string_id(self):
        res = self.tools.get(None)
        self.assertFalse(res["found"])
        self.assertIn("error", res)


class TestRelatedTool(unittest.TestCase):
    def setUp(self):
        self.tools = tools_from(KNOWLEDGE_DIR)

    def test_related_returns_first_class_relationships(self):
        res = self.tools.related("react-component")
        self.assertTrue(res["found"])
        self.assertGreater(res["count"], 0)
        for rel in res["relationships"]:
            self.assertIn("type", rel)
            self.assertIn("target", rel)

    def test_related_node_with_no_relationships(self):
        res = self.tools.related("react")
        self.assertTrue(res["found"])
        self.assertIsInstance(res["relationships"], list)

    def test_related_unknown_id(self):
        res = self.tools.related("nope")
        self.assertFalse(res["found"])
        self.assertIn("error", res)

    def test_related_invalid_id(self):
        res = self.tools.related(42)
        self.assertFalse(res["found"])
        self.assertIn("error", res)


class TestFollowTool(unittest.TestCase):
    def setUp(self):
        self.tools = tools_from(TOOLS_DIR)

    def test_follow_depth_one_reaches_neighbor(self):
        res = self.tools.follow("tool-a", max_depth=1)
        self.assertIsNone(res["error"])
        ids = {n["id"] for n in res["nodes"]}
        self.assertEqual(ids, {"tool-a", "tool-b"})
        self.assertEqual(res["depth_reached"], 1)
        # one edge a -> b recorded
        self.assertTrue(any(e["from"] == "tool-a" and e["to"] == "tool-b"
                            for e in res["edges"]))

    def test_follow_depth_zero_returns_only_start(self):
        res = self.tools.follow("tool-a", max_depth=0)
        self.assertEqual({n["id"] for n in res["nodes"]}, {"tool-a"})
        self.assertEqual(res["edges"], [])

    def test_follow_prevents_cycles(self):
        # a -> b -> a is a cycle; with large depth it must terminate and
        # visit each node exactly once.
        res = self.tools.follow("tool-a", max_depth=10)
        ids = {n["id"] for n in res["nodes"]}
        self.assertEqual(ids, {"tool-a", "tool-b"})
        self.assertEqual(res["depth_reached"], 1)

    def test_follow_rel_type_filter(self):
        res = self.tools.follow("tool-c", max_depth=2, rel_type="depends_on")
        ids = {n["id"] for n in res["nodes"]}
        self.assertEqual(ids, {"tool-c", "tool-a"})
        # only depends_on edges included
        self.assertTrue(all(e["type"] == "depends_on" for e in res["edges"]))

    def test_follow_rel_type_mismatch_excludes_edges(self):
        res = self.tools.follow("tool-a", max_depth=2, rel_type="depends_on")
        # tool-a only has related_to edges, so nothing is traversed
        self.assertEqual({n["id"] for n in res["nodes"]}, {"tool-a"})

    def test_follow_unknown_node(self):
        res = self.tools.follow("ghost", max_depth=2)
        self.assertIn("error", res)
        self.assertEqual(res["nodes"], [])

    def test_follow_invalid_id(self):
        res = self.tools.follow("", max_depth=2)
        self.assertIn("error", res)

    def test_follow_negative_depth(self):
        res = self.tools.follow("tool-a", max_depth=-1)
        self.assertIn("error", res)

    def test_follow_invalid_rel_type(self):
        res = self.tools.follow("tool-a", max_depth=2, rel_type=5)
        self.assertIn("error", res)


class TestToolsReuseStore(unittest.TestCase):
    def test_tools_delegate_to_injected_store(self):
        store = KnowledgeStore().load(VALID_DIR)
        tools = KnowledgeTools(store=store)
        # the same store instance is used, no re-loading
        self.assertIs(tools.store, store)
        self.assertEqual(tools.get("valid-concept")["found"], True)
        self.assertEqual(tools.get("valid-technology")["found"], True)


if __name__ == "__main__":
    unittest.main()
