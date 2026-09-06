"""Phase 23 regression tests: security & trust hardening fixes.

Covers the Phase 23 audit fixes:
    - C1 vocabulary-id containment + sanitized "not found" errors
    - C3 contract error sanitization at the public API boundary
    - C4 provenance-authority protection for context_hints
    - M1 closed-form enforcement for python -m unittest/compileall
    - M4 pid-scoped backup tokens (cross-process collision)

Each test asserts fail-closed behavior and stable, path-free messages.
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                "..")))

from ai_engine.vocabulary import Vocabulary, _resolve_vocab_path, _validate_vocab_id
from ai_engine import vocabulary as _V
from ai_engine.capture import run_capture
from ai_engine.paths import (
    get_context_db, get_knowledge_db, get_project_dir, _validate_project_id,
)
from intelligence.context.store import ContextStore


class VocabularyContainmentTests(unittest.TestCase):
    def test_valid_id_resolves_inside_vocab_dirs(self):
        path = os.path.realpath(_resolve_vocab_path("diary_v1"))
        self.assertTrue(os.path.exists(path))
        self.assertTrue(
            path.startswith(os.path.realpath(_V._PKG_VOCAB_DIR) + os.sep)
            or path.startswith(os.path.realpath(_V._VOCAB_DIR) + os.sep))

    def test_invalid_ids_rejected(self):
        for bad in ("../../etc/passwd", "x/../y", "a/b", "..", ".", "a.b",
                    "Uppercase", "a b", "", "   ", "-lead"):
            with self.assertRaises(ValueError) as cm:
                _validate_vocab_id(bad)
            msg = str(cm.exception)
            self.assertTrue("must match" in msg or "non-empty" in msg, msg)
            self.assertNotIn(_V._VOCAB_DIR, msg)

    def test_traversal_load_rejected_without_path(self):
        with self.assertRaises(ValueError) as cm:
            Vocabulary.load("../../etc/passwd")
        msg = str(cm.exception)
        self.assertIn("must match", msg)
        self.assertNotIn(os.path.realpath(_V._VOCAB_DIR), msg)

    def test_missing_vocab_error_has_no_filesystem_path(self):
        with self.assertRaises(ValueError) as cm:
            Vocabulary.load("zzz_no_such_vocab_99")
        self.assertEqual(str(cm.exception),
                         "vocabulary not found: 'zzz_no_such_vocab_99'")

    def test_valid_vocab_loads(self):
        vocab = Vocabulary.load("diary_v1")
        self.assertEqual(vocab.id, "diary_v1")


class ErrorLeakSanitizationTests(unittest.TestCase):
    def _root(self):
        root = tempfile.mkdtemp(prefix="p23_leak_")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            root, ignore_errors=True))
        return root

    def _corrupt_knowledge(self, root):
        kb = get_knowledge_db("default", root)
        os.makedirs(os.path.dirname(kb), exist_ok=True)
        with open(kb, "wb") as f:
            f.write(b"definitely not a sqlite database " * 200)

    def test_remember_internal_error_is_sanitized(self):
        from api.memory_api import MemoryAPI
        from api.errors import KnowledgeArgumentError
        root = self._root()
        self._corrupt_knowledge(root)
        api = MemoryAPI(data_root=root)
        with self.assertRaises(KnowledgeArgumentError) as cm:
            api.remember({"text": "leak probe"})
        msg = str(cm.exception)
        self.assertIn("remember failed", msg)
        self.assertNotIn(root, msg)
        self.assertNotIn("sqlite", msg.lower())

    def test_recall_internal_error_is_sanitized(self):
        from api.memory_api import MemoryAPI
        from api.errors import KnowledgeArgumentError
        root = self._root()
        self._corrupt_knowledge(root)
        api = MemoryAPI(data_root=root)
        with self.assertRaises(KnowledgeArgumentError) as cm:
            api.recall("nothing")
        msg = str(cm.exception)
        self.assertIn("recall failed", msg)
        self.assertNotIn(root, msg)
        self.assertNotIn("sqlite", msg.lower())

    def test_vocab_traversal_via_api_is_arg_error(self):
        from api.memory_api import MemoryAPI
        from api.errors import KnowledgeArgumentError
        api = MemoryAPI(data_root=self._root())
        with self.assertRaises(KnowledgeArgumentError) as cm:
            api.remember({"text": "x"}, vocabulary_id="../../etc/passwd")
        self.assertIn("must match", str(cm.exception))

    def test_project_traversal_via_api_is_arg_error(self):
        from api.memory_api import MemoryAPI
        from api.errors import KnowledgeArgumentError
        api = MemoryAPI(data_root=self._root())
        with self.assertRaises(KnowledgeArgumentError) as cm:
            api.remember({"text": "x"}, project_id="a/../b")
        self.assertIn("invalid project_id", str(cm.exception))


class ProvenanceSpoofingTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="p23_spoof_")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_reserved_provenance_fields_not_overridable(self):
        res = run_capture(
            raw={"text": "provenance spoof probe", "type": "fact"},
            project_id="default", data_root=self.root,
            vocabulary_id="diary_v1", activity_type="manual", source="manual",
            context_hints={
                "project": {"project_id": "evil", "vocabulary_id": "evil_v1",
                            "region": "eu"},
                "source": {"adapter": "evil", "activity_type": "spoofed",
                           "payload_hash": "DEADBEEF", "uri": "https://evil"},
                "temporal": {"captured_at_epoch": 1.0},
            })
        self.assertTrue(res.get("ok"), res)
        store = ContextStore(db_path=get_context_db("default", self.root))
        try:
            snap = store.get(res["context_id"])
        finally:
            store.close()
        self.assertEqual(snap.project["project_id"], "default")
        self.assertEqual(snap.project["vocabulary_id"], "diary_v1")
        self.assertEqual(snap.project.get("region"), "eu")
        self.assertEqual(snap.source["adapter"], "manual")
        self.assertEqual(snap.source["activity_type"], "manual")
        self.assertNotEqual(snap.source.get("payload_hash"), "DEADBEEF")
        self.assertEqual(snap.source.get("uri"), "https://evil")
        self.assertNotEqual(snap.captured_at_epoch, 1.0)
        self.assertGreater(snap.captured_at_epoch, 1700000000)

    def test_authority_override_keys_fail_closed(self):
        for hints in ({"activity_type": "spoof"}, {"adapter": "spoof"}):
            res = run_capture(
                raw={"text": "x", "type": "fact"},
                project_id="default", data_root=self.root,
                vocabulary_id="diary_v1", context_hints=hints)
            self.assertFalse(res.get("ok"), res)
            self.assertEqual(res.get("code"), "invalid_argument")

    def test_legit_context_hints_still_applied(self):
        res = run_capture(
            raw={"text": "hint probe", "type": "fact"},
            project_id="default", data_root=self.root,
            vocabulary_id="diary_v1",
            context_hints={"actor": {"user_id": "alice"},
                           "spatial": {"location": "home"},
                           "affective": {"mood": "calm"}})
        self.assertTrue(res.get("ok"), res)
        store = ContextStore(db_path=get_context_db("default", self.root))
        try:
            snap = store.get(res["context_id"])
        finally:
            store.close()
        self.assertEqual(snap.actor.get("user_id"), "alice")
        self.assertEqual(snap.spatial.get("location"), "home")
        self.assertEqual(snap.affective.get("mood"), "calm")


class CommandArgValidationTests(unittest.TestCase):
    def setUp(self):
        from tools.permissions import Policy
        from tools.permissions.execution import run_checked
        self.pol = Policy()
        self.run_checked = run_checked

    def _denied(self, name, args):
        r = self.run_checked(self.pol, name, args, lambda p: True)
        self.assertFalse(r["success"], (args, r))
        self.assertIsNone(r["exit_code"], (args, r))
        self.assertIn("denied", (r["error"] or "").lower(), (args, r))
        return r

    def test_closed_forms_allowed(self):
        from tools.permissions.execution import check_args
        for name, args in (("python", ["-m", "unittest"]),
                           ("python3", ["-m", "compileall"]),
                           ("python", ["-m", "py_compile", "src/mod.py"])):
            form = check_args(self.pol, name, args)
            self.assertEqual(form["name"], name)

    def test_extra_python_module_args_denied(self):
        cases = (
            ("python", ["-m", "unittest", "attacker_test"]),
            ("python", ["-m", "unittest", "discover", "-s", "."]),
            ("python", ["-m", "compileall", "any_dir"]),
            ("python", ["-m", "compileall", "/etc"]),
            ("python", ["-m", "compileall", "..", ".."]),
        )
        for name, args in cases:
            r = self._denied(name, args)
            self.assertIn("closed form", r["error"])

    def test_unsafe_python_forms_denied(self):
        cases = (
            ("python", ["-m", "evil_module"]),
            ("python", ["-c", "print(1)"]),
            ("python", ["-i"]),
            ("python", []),
            ("python", ["-m"]),
        )
        for name, args in cases:
            self._denied(name, args)

    def test_py_compile_path_rules(self):
        self._denied("python", ["-m", "py_compile"])
        self._denied("python", ["-m", "py_compile", "a.py", "b.py"])
        self._denied("python", ["-m", "py_compile", "../evil.py"])
        self._denied("python", ["-m", "py_compile", "/etc/passwd"])

    def test_py_compile_closed_form_via_runner(self):
        from tools.permissions import Policy
        from tools.permissions.execution import run_checked
        root = tempfile.mkdtemp(prefix="p23_ex_runner_")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            root, ignore_errors=True))
        os.makedirs(os.path.join(root, "src"), exist_ok=True)
        with open(os.path.join(root, "src", "mod.py"), "w") as f:
            f.write("VALUE = 1\n")
        # Generic hardened runner with workspace cwd confinement.
        r = run_checked(Policy(), "python", ["-m", "py_compile", "src/mod.py"],
                        lambda p: True, cwd=root, workspace_root=root)
        self.assertTrue(r["success"], r)
        r = run_checked(Policy(), "python", ["-m", "py_compile", "../evil.py"],
                        lambda p: True, cwd=root, workspace_root=root)
        self.assertFalse(r["success"], r)


class BackupCollisionTests(unittest.TestCase):
    def setUp(self):
        from ai_engine.memory import Memory
        self.root = tempfile.mkdtemp(prefix="p23_backup_")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))
        Memory(project_id="default", data_root=self.root,
               vocabulary_id="diary_v1").remember(
            payload={"text": "backup collision probe", "type": "fact"})

    def test_backup_token_includes_pid(self):
        from ai_engine.persistence import backup_project
        res = backup_project("default", data_root=self.root)
        self.assertTrue(res.get("ok"), res)
        files = list((res.get("backup_files") or {}).values())
        self.assertGreaterEqual(len(files), 1)
        suffix = re.compile(rf"-{os.getpid():x}\.backup$")
        for path in files:
            self.assertRegex(path, suffix)

    def test_sequential_backups_distinct(self):
        from ai_engine.persistence import backup_project
        a = backup_project("default", data_root=self.root)["backup_files"]
        b = backup_project("default", data_root=self.root)["backup_files"]
        self.assertNotEqual(list(a.values()), list(b.values()))


class ProjectIsolationTests(unittest.TestCase):
    def setUp(self):
        from api.memory_api import MemoryAPI
        self.root = tempfile.mkdtemp(prefix="p23_iso_")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))
        self.api = MemoryAPI(data_root=self.root)

    def test_cross_project_isolation(self):
        self.api.remember({"text": "opaquely unique secret fact", "type": "fact"},
                          project_id="proja")
        found = self.api.recall("opaquely unique secret fact",
                                project_id="projb")
        hits = (found.get("knowledge") or [])
        self.assertEqual(hits, [])
        hits = self.api.recall("opaquely unique secret fact",
                               project_id="proja")["knowledge"]
        self.assertGreaterEqual(len(hits), 1)

    def test_path_helpers_reject_traversal(self):
        with self.assertRaises(ValueError) as cm:
            _validate_project_id("a/../b")
        msg = str(cm.exception)
        self.assertTrue("must match" in msg or "path traversal" in msg)
        with self.assertRaises(ValueError):
            get_project_dir("../escape", self.root)


class CliBoundaryTests(unittest.TestCase):
    def test_traversal_project_rejected_on_cli(self):
        root = tempfile.mkdtemp(prefix="p23_cli_")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            root, ignore_errors=True))
        env = dict(os.environ)
        env["AI_ENGINE_DATA_DIR"] = root
        r = subprocess.run(
            [sys.executable, "-m", "ai_engine", "recall", "dummy",
             "--project", "../evil"],
            capture_output=True, text=True, cwd=os.getcwd(), env=env,
            timeout=90)
        out = (r.stdout or "") + (r.stderr or "")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid project_id", out)

    def test_cli_uses_isolated_data_root(self):
        root = tempfile.mkdtemp(prefix="p23_cli_")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            root, ignore_errors=True))
        env = dict(os.environ)
        env["AI_ENGINE_DATA_DIR"] = root
        r = subprocess.run(
            [sys.executable, "-m", "ai_engine", "remember", "cli isolation probe"],
            capture_output=True, text=True, cwd=os.getcwd(), env=env,
            timeout=90)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(root))
        self.assertTrue(os.path.exists(
            os.path.join(root, "default", "knowledge.db")))


if __name__ == "__main__":
    unittest.main()