"""Phase 6 — Plugin / Adapter Architecture.

Tests for:
  * Generic AdapterRegistry (Capture/Effect/Verifier/Ranker) — no hardcoded coding adapters
  * Clean registration/resolution, duplicate fail-closed, unknown fail-closed
  * Isolation, generic, no trust bypass
  * Integration with existing CaptureAdapter/Memory/Ranker/runtime (additive)
"""

import os
import sys
import tempfile
import unittest
import sqlite3

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

LEGACY_DB = os.path.join(_ROOT, "database", "knowledge.db")
EXPECTED_SHA = "000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91"

def _sha256(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1<<20), b""):
            h.update(c)
    return h.hexdigest()


class RegistryBasicTests(unittest.TestCase):
    def test_generic_no_coding_hardcode(self):
        from ai_engine.registry import get_default_registry
        reg = get_default_registry()
        # Should contain generic manual and keyword, not file.write etc.
        self.assertIn("manual", reg.list_captures())
        self.assertIn("keyword", reg.list_rankers())
        self.assertNotIn("file.write", reg.list_effects())
        self.assertNotIn("file.write", reg.list_captures())

    def test_capture_registration(self):
        from ai_engine.registry import AdapterRegistry
        from ai_engine.capture import ManualCaptureAdapter, CaptureAdapter
        reg = AdapterRegistry()
        a = ManualCaptureAdapter()
        self.assertTrue(reg.register_capture(a))
        self.assertEqual(reg.get_capture("manual"), a)
        self.assertIn("manual", reg.list_captures())
        # Duplicate same instance is idempotent (returns False)
        self.assertFalse(reg.register_capture(a))
        # Duplicate different instance with same id fails closed
        class Other(CaptureAdapter):
            @property
            def adapter_id(self): return "manual"
            def normalize(self, raw, context_hints=None): return {}
        with self.assertRaises(ValueError):
            reg.register_capture(Other())
        # Unknown returns None (fail closed, not auto-create)
        self.assertIsNone(reg.get_capture("unknown_xyz"))

    def test_effect_registration(self):
        from ai_engine.registry import AdapterRegistry
        reg = AdapterRegistry()
        def handler(x=None): return {"ok": True}
        self.assertTrue(reg.register_effect("custom.tool", handler))
        self.assertEqual(reg.get_effect("custom.tool"), handler)
        self.assertFalse(reg.register_effect("custom.tool", handler))  # idempotent same handler
        with self.assertRaises(ValueError):
            reg.register_effect("custom.tool", lambda: None)  # different handler same name
        with self.assertRaises(ValueError):
            reg.register_effect("../evil", handler)
        self.assertIsNone(reg.get_effect("unknown"))

    def test_verifier_registration(self):
        from ai_engine.registry import AdapterRegistry
        reg = AdapterRegistry()
        def verifier_func(): pass
        self.assertTrue(reg.register_verifier("my_verifier", verifier_func))
        self.assertEqual(reg.get_verifier("my_verifier"), verifier_func)
        # Duplicate different verifier fails
        with self.assertRaises(ValueError):
            reg.register_verifier("my_verifier", lambda: None)
        # Must be callable or have verify
        with self.assertRaises(ValueError):
            reg.register_verifier("bad", "not callable")
        self.assertIsNone(reg.get_verifier("unknown"))

    def test_ranker_registration(self):
        from ai_engine.registry import AdapterRegistry
        from ai_engine.ranker import KeywordRanker
        reg = AdapterRegistry()
        r = KeywordRanker()
        self.assertTrue(reg.register_ranker("keyword", r))
        self.assertEqual(reg.get_ranker("keyword"), r)
        with self.assertRaises(ValueError):
            reg.register_ranker("keyword", KeywordRanker())
        self.assertIsNone(reg.get_ranker("unknown"))
        with self.assertRaises(ValueError):
            reg.register_ranker("bad", "not a ranker")

    def test_unknown_untrusted_fail_closed(self):
        from ai_engine.registry import AdapterRegistry
        reg = AdapterRegistry()
        # Unknown capture via string + registry should fail closed in run_capture
        from ai_engine.capture import run_capture
        with tempfile.TemporaryDirectory() as tmp:
            res = run_capture("test", project_id="default", data_root=tmp, vocabulary_id="diary_v1", adapter="unknown_adapter_xyz", registry=reg)
            self.assertFalse(res["ok"])
            self.assertIn("unknown capture adapter", res["error"])

    def test_isolation(self):
        from ai_engine.registry import AdapterRegistry
        from ai_engine.ranker import KeywordRanker
        reg1 = AdapterRegistry()
        reg2 = AdapterRegistry()
        reg1.register_ranker("keyword", KeywordRanker())
        self.assertIn("keyword", reg1.list_rankers())
        self.assertNotIn("keyword", reg2.list_rankers())
        # Clear
        reg1.clear()
        self.assertTrue(reg1.is_empty())


class RegistryIntegrationTests(unittest.TestCase):
    def test_capture_via_registry(self):
        from ai_engine.registry import AdapterRegistry, get_default_registry
        from ai_engine.capture import run_capture
        with tempfile.TemporaryDirectory() as tmp:
            reg = get_default_registry()
            # run_capture with adapter string should resolve via registry
            res = run_capture("registry capture test", project_id="default", data_root=tmp, vocabulary_id="diary_v1", adapter="manual", registry=reg)
            self.assertTrue(res["ok"], res)
            # Direct adapter instance still works without registry
            from ai_engine.capture import ManualCaptureAdapter
            res2 = run_capture("registry capture test 2", project_id="default", data_root=tmp, vocabulary_id="diary_v1", adapter=ManualCaptureAdapter())
            self.assertTrue(res2["ok"], res2)

    def test_memory_ranker_via_registry(self):
        from ai_engine.memory import Memory
        from ai_engine.registry import AdapterRegistry
        from ai_engine.ranker import KeywordRanker
        with tempfile.TemporaryDirectory() as tmp:
            reg = AdapterRegistry()
            custom_ranker = KeywordRanker(context_strict=True)
            reg.register_ranker("keyword", custom_ranker)
            mem = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1", registry=reg)
            # Should have used registry's ranker
            self.assertIs(mem.ranker, custom_ranker)
            # Remember and recall should still work
            r = mem.remember(text="registry ranker test", type="fact")
            self.assertTrue(r["ok"], r)
            out = mem.recall(query="registry ranker test")
            self.assertTrue(out["ok"])
            self.assertGreaterEqual(len(out["result"]["knowledge"]), 1)

    def test_runtime_effect_via_registry(self):
        from ai_engine.registry import AdapterRegistry
        from ai_engine.runtime import make_memory_tools, attach_memory_tools
        with tempfile.TemporaryDirectory() as tmp:
            reg = AdapterRegistry()
            def custom_effect(x=None):
                return {"ok": True, "custom": "value", "x": x}
            reg.register_effect("custom.echo", custom_effect)
            tools = {}
            attach_memory_tools(tools, None)
            self.assertIn("memory.recall", tools)
            res = reg.get_effect("custom.echo")(x="hello")
            self.assertTrue(res["ok"])
            self.assertEqual(res["custom"], "value")
            # Custom effect surfaces in the registry
            self.assertIn("custom.echo", reg.list_effects())
            # Unknown effect still fails closed
            self.assertIsNone(reg.get_effect("unknown.tool"))

    def test_runtime_memory_plus_registry(self):
        from ai_engine.memory import Memory
        from ai_engine.registry import AdapterRegistry
        from ai_engine.runtime import make_memory_tools
        with tempfile.TemporaryDirectory() as tmp:
            data_root = os.path.join(tmp, "data")
            mem = Memory(project_id="default", data_root=data_root, vocabulary_id="diary_v1")
            mem.remember(text="runtime registry combined", type="fact")
            reg = AdapterRegistry()
            def echo(x=None): return {"ok": True, "echo": x}
            reg.register_effect("custom.echo2", echo)
            tools = make_memory_tools(mem)
            # memory tools work
            recall = tools["memory.recall"](query="runtime registry combined")
            self.assertTrue(recall["ok"])
            self.assertGreaterEqual(len(recall["result"]["knowledge"]), 1)
            # registry custom effect works
            self.assertEqual(reg.get_effect("custom.echo2")(x="hello")["echo"], "hello")

    def test_runtime_preserves_approval(self):
        from ai_engine.registry import AdapterRegistry
        from ai_engine.runtime import make_memory_tools
        from ai_engine.memory import Memory
        from tools.permissions.policy import Policy
        from tools.permissions.execution import run_checked
        with tempfile.TemporaryDirectory() as tmp:
            reg = AdapterRegistry()
            def write_effect(path=None, content=None):
                return {"ok": True}
            reg.register_effect("custom.write", write_effect)
            tools = make_memory_tools(None)
            # The generic runtime layer exposes no unrestricted mutation and
            # no arbitrary command execution.
            self.assertNotIn("file.write", tools)
            self.assertNotIn("git", tools)
            r = run_checked(Policy(), "git", ["commit", "-m", "x"],
                            approval_of=lambda p: False, workspace_root=tmp)
            self.assertFalse(r["success"])
            self.assertIn("denied", r["error"])

    def test_no_unrestricted_execution_via_registry(self):
        from ai_engine.registry import AdapterRegistry
        # Attempt to register effect with path traversal should fail
        reg = AdapterRegistry()
        with self.assertRaises(ValueError):
            reg.register_effect("../evil", lambda: None)
        with self.assertRaises(ValueError):
            reg.register_effect("a/b", lambda: None)
        # Attempt to register non-callable should fail
        with self.assertRaises(ValueError):
            reg.register_effect("bad", "not callable")

    def test_invariants_still_hold(self):
        from api.contract import CONTRACT_VERSION, OPERATIONS
        from tools.permissions.policy import HARD_WRITE_INVARIANTS
        import intelligence
        self.assertEqual(CONTRACT_VERSION, "1")
        self.assertEqual(tuple(OPERATIONS), ("search", "get", "related", "follow", "provenance", "inspect"))
        self.assertEqual(list(HARD_WRITE_INVARIANTS), [("database/knowledge.db", "blocked"), ("database/knowledge.db.backup", "blocked")])
        self.assertEqual(intelligence.__version__, "0")
        self.assertEqual(_sha256(LEGACY_DB), EXPECTED_SHA)
        con = sqlite3.connect(f"file:{LEGACY_DB}?mode=ro", uri=True)
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        con.close()
        # Ensure no network/LLM
        import sys
        for mod in sys.modules:
            self.assertNotIn("torch", mod)
            self.assertNotIn("openai", mod)


if __name__ == "__main__":
    unittest.main()
