import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.coding import CodingTools, WriteTools, PathError


def write_raw(root, rel, content, binary=False):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if binary:
        with open(path, "wb") as f:
            f.write(content)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


class WriteToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ct = CodingTools(self.tmp.name)
        self.wt = WriteTools(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    # -- file.write ------------------------------------------------------

    def test_write_creates_file(self):
        res = self.ct.file_write("src/app.py", "print(1)\n")
        self.assertIsNone(res["error"])
        self.assertEqual(res["created_or_updated"], "created")
        self.assertEqual(res["bytes_written"], len("print(1)\n"))
        with open(os.path.join(self.tmp.name, "src/app.py")) as f:
            self.assertEqual(f.read(), "print(1)\n")

    def test_write_creates_parent_dirs(self):
        res = self.ct.file_write("a/b/c/d.txt", "x")
        self.assertIsNone(res["error"])
        self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, "a/b/c/d.txt")))

    def test_write_overwrites(self):
        self.ct.file_write("f.txt", "old")
        res = self.ct.file_write("f.txt", "new", overwrite=True)
        self.assertIsNone(res["error"])
        self.assertEqual(res["created_or_updated"], "updated")
        with open(os.path.join(self.tmp.name, "f.txt")) as f:
            self.assertEqual(f.read(), "new")

    def test_write_refuses_without_overwrite_flag(self):
        self.ct.file_write("f.txt", "old")
        res = self.ct.file_write("f.txt", "new", overwrite=False)
        self.assertIn("error", res)
        with open(os.path.join(self.tmp.name, "f.txt")) as f:
            self.assertEqual(f.read(), "old")

    def test_write_requires_content_string(self):
        res = self.ct.file_write("f.txt", None)
        self.assertIn("error", res)

    def test_write_on_directory_errors(self):
        os.makedirs(os.path.join(self.tmp.name, "d"))
        res = self.ct.file_write("d", "x")
        self.assertIn("error", res)

    def test_write_traversal_rejected(self):
        res = self.ct.file_write("../escape.txt", "x")
        self.assertIn("error", res)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "..", "escape.txt")))

    def test_write_absolute_escape_rejected(self):
        res = self.ct.file_write("/etc/passwd", "x")
        self.assertIn("error", res)

    # -- file.edit -------------------------------------------------------

    def test_edit_replaces_text(self):
        self.ct.file_write("f.txt", "foo bar foo")
        res = self.ct.file_edit("f.txt", "bar", "bazzz")
        self.assertIsNone(res["error"])
        self.assertEqual(res["replacements"], 1)
        self.assertNotEqual(res["previous_size"], res["new_size"])
        with open(os.path.join(self.tmp.name, "f.txt")) as f:
            self.assertEqual(f.read(), "foo bazzz foo")

    def test_edit_missing_file(self):
        res = self.ct.file_edit("nope.txt", "a", "b")
        self.assertIn("error", res)

    def test_edit_binary_rejected(self):
        write_raw(self.tmp.name, "b.bin", b"\x00\x01binary", binary=True)
        res = self.ct.file_edit("b.bin", "bin", "txt")
        self.assertIn("error", res)

    def test_edit_old_text_not_found(self):
        self.ct.file_write("f.txt", "hello")
        res = self.ct.file_edit("f.txt", "zzz", "y")
        self.assertIn("error", res)

    def test_edit_ambiguous_requires_replace_all(self):
        self.ct.file_write("f.txt", "x y x y x")
        res = self.ct.file_edit("f.txt", "x", "z")
        self.assertIn("error", res)
        self.assertIn("ambiguous", res["error"])

    def test_edit_replace_all(self):
        self.ct.file_write("f.txt", "x y x y x")
        res = self.ct.file_edit("f.txt", "x", "z", replace_all=True)
        self.assertIsNone(res["error"])
        self.assertEqual(res["replacements"], 3)
        with open(os.path.join(self.tmp.name, "f.txt")) as f:
            self.assertEqual(f.read(), "z y z y z")

    def test_edit_empty_new_text_deletes(self):
        self.ct.file_write("f.txt", "keep DELETE keep")
        res = self.ct.file_edit("f.txt", "DELETE ", "")
        self.assertIsNone(res["error"])
        with open(os.path.join(self.tmp.name, "f.txt")) as f:
            self.assertEqual(f.read(), "keep keep")

    def test_edit_traversal_rejected(self):
        res = self.ct.file_edit("../x.txt", "a", "b")
        self.assertIn("error", res)

    # -- file.mkdir ------------------------------------------------------

    def test_mkdir_creates(self):
        res = self.ct.file_mkdir("newdir/sub")
        self.assertIsNone(res["error"])
        self.assertTrue(res["created"])
        self.assertTrue(os.path.isdir(os.path.join(self.tmp.name, "newdir/sub")))

    def test_mkdir_idempotent(self):
        self.ct.file_mkdir("d")
        res = self.ct.file_mkdir("d")
        self.assertIsNone(res["error"])
        self.assertFalse(res["created"])

    def test_mkdir_traversal_rejected(self):
        res = self.ct.file_mkdir("../escape")
        self.assertIn("error", res)

    # -- file.diff -------------------------------------------------------

    def test_diff_detects_changes(self):
        self.ct.file_write("f.txt", "line1\nline2\n")
        res = self.ct.file_diff("f.txt", "line1\nCHANGED\n")
        self.assertIsNone(res["error"])
        self.assertTrue(res["changed"])
        self.assertGreater(res["lines_added"], 0)
        self.assertGreater(res["lines_removed"], 0)
        self.assertIn("+CHANGED", res["diff"])
        # diff must not modify the file
        with open(os.path.join(self.tmp.name, "f.txt")) as f:
            self.assertEqual(f.read(), "line1\nline2\n")

    def test_diff_no_change(self):
        self.ct.file_write("f.txt", "same\n")
        res = self.ct.file_diff("f.txt", "same\n")
        self.assertIsNone(res["error"])
        self.assertFalse(res["changed"])
        self.assertEqual(res["lines_added"], 0)
        self.assertEqual(res["lines_removed"], 0)

    def test_diff_against_missing_file(self):
        res = self.ct.file_diff("new.txt", "hello\n")
        self.assertIsNone(res["error"])
        self.assertFalse(res["exists"])
        self.assertTrue(res["changed"])
        # must not create the file
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "new.txt")))

    def test_diff_traversal_rejected(self):
        res = self.ct.file_diff("../x.txt", "x")
        self.assertIn("error", res)


class SymlinkEscapeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.outside = tempfile.TemporaryDirectory()
        self.ct = CodingTools(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        self.outside.cleanup()

    def test_symlink_escape_rejected(self):
        link = os.path.join(self.tmp.name, "evil")
        os.symlink(self.outside.name, link)
        res = self.ct.file_write("evil/secret.txt", "x")
        self.assertIn("error", res)
        # nothing written outside the workspace
        self.assertEqual(os.listdir(self.outside.name), [])


if __name__ == "__main__":
    unittest.main()
