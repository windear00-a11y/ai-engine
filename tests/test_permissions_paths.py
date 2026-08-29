"""Tests for path safety / zone classification (Phase 1A).

Covers: traversal, outside paths, symlink escapes, normalized paths, and
zones (protected / readonly / blocked / workspace_writeable / outside).
All tests operate on temporary directories only, never the production DB.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                "..")))

from tools.coding.fs import PathError
from tools.permissions import PathPolicy
from tools.permissions.pathpolicy import hard_write_guard, Zone
from tools.permissions.decisions import DecisionKind


def _mkws(name):
    d = tempfile.mkdtemp(prefix=f"perm_paths_{name}_")
    os.makedirs(os.path.join(d, "src"), exist_ok=True)
    os.makedirs(os.path.join(d, "database"), exist_ok=True)
    os.makedirs(os.path.join(d, "api"), exist_ok=True)
    os.makedirs(os.path.join(d, ".git"), exist_ok=True)
    os.makedirs(os.path.join(d, "database", "backups"), exist_ok=True)
    with open(os.path.join(d, "src", "main.py"), "w") as f:
        f.write("x = 1\n")
    with open(os.path.join(d, "api", "contract.py"), "w") as f:
        f.write("CONTRACT = 1\n")
    return d


class HardGuardTests(unittest.TestCase):
    def test_knowledge_db_is_hard_guarded(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "database"))
            self.assertTrue(hard_write_guard(
                os.path.join(d, "database", "knowledge.db"), d))
            self.assertTrue(hard_write_guard(
                os.path.join(d, "database", "knowledge.db.backup"), d))

    def test_other_files_not_hard_guarded(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(hard_write_guard(
                os.path.join(d, "src", "main.py"), d))
            self.assertFalse(hard_write_guard(os.path.join(d, "x.txt"), d))


class ZoneClassificationTests(unittest.TestCase):
    def setUp(self):
        self.ws = _mkws("zone")
        self.p = PathPolicy(self.ws)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_workspace_writeable(self):
        info = self.p.write_zone("src/main.py")
        self.assertEqual(info["zone"], Zone.WORKSPACE_WRITEABLE)
        self.assertFalse(info["protected"])

    def test_blocked_knowledge_db(self):
        info = self.p.write_zone("database/knowledge.db")
        self.assertEqual(info["zone"], Zone.BLOCKED)
        self.assertEqual(info["mode"], "deny")

    def test_blocked_knowledge_db_backup(self):
        info = self.p.write_zone("database/knowledge.db.backup")
        self.assertEqual(info["zone"], Zone.BLOCKED)

    def test_blocked_git(self):
        info = self.p.write_zone(".git/config")
        self.assertEqual(info["zone"], Zone.BLOCKED)

    def test_blocked_backups_dir(self):
        info = self.p.write_zone("database/backups/x")
        self.assertEqual(info["zone"], Zone.BLOCKED)

    def test_readonly_contract(self):
        info = self.p.write_zone("api/contract.py")
        self.assertEqual(info["zone"], Zone.WORKSPACE_READONLY)
        self.assertEqual(info["mode"], "read_only")

    def test_protected_secret_files(self):
        for p in (".env",
                  "config/.env",
                  "cert.pem",
                  "certs/priv.key",
                  "secrets/token.txt",
                  "id_rsa"):
            info = self.p.write_zone(p)
            self.assertIn(info["zone"], (Zone.PROTECTED, Zone.BLOCKED),
                          f"{p} -> {info}")

    def test_read_decision_blocks_blocked_path(self):
        d = self.p.read_decision("database/knowledge.db")
        self.assertEqual(d.kind, DecisionKind.DENY)
        self.assertEqual(d.reason_code, "blocked")

    def test_read_decision_allows_normal(self):
        d = self.p.read_decision("src/main.py")
        self.assertEqual(d.kind, DecisionKind.ALLOW)


class PathTraversalTests(unittest.TestCase):
    def setUp(self):
        self.ws = _mkws("trav")
        self.p = PathPolicy(self.ws)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_dotdot_traversal_raises(self):
        with self.assertRaises(PathError):
            self.p.resolve("../escape")

    def test_absolute_outside_raises(self):
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaises(PathError):
                self.p.resolve(os.path.join(other, "x"))

    def test_symlink_escape_outside(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(
            outside, ignore_errors=True))
        target = os.path.join(outside, "secret.txt")
        with open(target, "w") as f:
            f.write("secret")
        link = os.path.join(self.ws, "escape_link")
        os.symlink(target, link)
        with self.assertRaises(PathError):
            self.p.resolve("escape_link")

    def test_symlink_to_protected_resolves_to_zone(self):
        # A symlink inside the workspace pointing at a protected file.
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "ws"))
            os.makedirs(os.path.join(d, "protected_store"))
            secret = os.path.join(d, "protected_store", ".env")
            with open(secret, "w") as f:
                f.write("TOKEN=x")
            link = os.path.join(d, "ws", "env_link")
            os.symlink(secret, link)
            pp = PathPolicy(os.path.join(d, "ws"))
            with self.assertRaises(PathError):
                # .env lives outside the workspace -> escape.
                pp.resolve("env_link")

    def test_normalized_relative_path(self):
        abs_path = self.p.resolve("./src//main.py")
        self.assertEqual(
            os.path.realpath(os.path.join(self.ws, "src", "main.py")),
            abs_path)

    def test_absolute_inside_workspace_allowed(self):
        abs_path = self.p.resolve(os.path.join(self.ws, "src", "main.py"))
        self.assertTrue(abs_path.endswith("src/main.py"))


if __name__ == "__main__":
    unittest.main()
