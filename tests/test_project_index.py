"""Phase 1 (Component B) tests: Persistent Project/Code Index — approved slice.

Covers (B1/B2/B3/B6 of the ORIGINAL Phase 1 A->B->C plan):
* B1 storage + project identity: per-project SQLite DB at
  <workspace_root>/.ai-engine/project_index.db (never the production
  knowledge DB); deterministic stable ids; build is all-or-nothing.
* B2 discovery + filtering: hard skips (.git, node_modules, __pycache__,
  .venv, .ai-engine, ...), workspace .gitignore respected, config files
  indexed as data (never executed), binary/oversized/unreadable/syntax
  errors are isolated per file, and the indexer never escapes the root.
* B3 Python AST extraction: imports (absolute + relative), classes, bases,
  functions, methods (with parent linkage), decorators, async, line spans,
  __init__ package identity.
* B6 deterministic query API: find_file, file_manifest, symbols_in_file,
  find_symbol, find_symbols_by_name, imports_of, dependents_of, tests_for,
  inherited_bases, find_references, project_info.

Conventions: only temporary workspaces + temp index DBs. The production
database/knowledge.db and Contract v1 are NEVER touched. No incremental (B7),
no JS/TS structural indexing, no engine wiring, no commit.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                "..")))

from tools.indexer import (  # noqa: E402
    ProjectIndex, IndexQueries, project_id, file_id, symbol_id, stable_id,
)
from tools.indexer import discovery  # noqa: E402


def write_tree(base, files):
    for rel, content in files.items():
        path = os.path.join(base, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


SAMPLE = {
    "pkg/__init__.py": "from .core import MathHelper\n",
    "pkg/core.py": (
        "import os\n"
        "from .utils import helper\n"
        "from ..external import something\n"
        "\n"
        "class MathHelper:\n"
        "    def __init__(self, base=0):\n"
        "        self.base = base\n"
        "    def add(self, x):\n"
        "        return self.base + x\n"
        "\n"
        "    async def total(self, *items):\n"
        "        return sum(items)\n"
        "\n"
        "def top_level(a, b=1, **kw):\n"
        "    return a + b\n"
        "\n"
        "class Child(MathHelper):\n"
        "    pass\n"
    ),
    "pkg/utils.py": "def helper(x):\n    return x * 2\n",
    "tests/test_core.py": (
        "from pkg.core import MathHelper\n"
        "\n"
        "def test_add():\n"
        "    assert MathHelper().add(2) == 2\n"
    ),
    "main.py": "def broken(:\n",
    "pyproject.toml": "[project]\nname = \"demo\"\n",
    "data.bin": b"\x00\x01\x02".decode("latin-1"),
}


class TestProjectIndexBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._tmp.name, "proj")
        os.makedirs(self.root)
        write_tree(self.root, {
            k: v for k, v in SAMPLE.items()
        })
        # gitignore ignoring node_modules; add a node_modules to be skipped
        with open(os.path.join(self.root, ".gitignore"), "w") as f:
            f.write("node_modules/\n*.log\n")
        os.makedirs(os.path.join(self.root, "node_modules"))
        with open(os.path.join(self.root, "node_modules", "dep.js"), "w") as f:
            f.write("// dep\n")
        with open(os.path.join(self.root, "debug.log"), "w") as f:
            f.write("log\n")

    def tearDown(self):
        self._tmp.cleanup()

    def build(self):
        self.ix = ProjectIndex(self.root)
        self.res = self.ix.build()
        self.q = IndexQueries(self.ix.db_path)
        return self.ix, self.q


class TestB1StorageAndIdentity(TestProjectIndexBase):
    def test_db_location_under_ai_engine_dir(self):
        ix = ProjectIndex(self.root)
        self.assertEqual(ix.db_path,
                         os.path.join(self.root, ".ai-engine",
                                      "project_index.db"))
        ix.build()
        self.assertTrue(os.path.exists(ix.db_path))
        # Never touches production knowledge db.
        self.assertNotIn("knowledge.db", os.listdir(self.root))

    def test_project_id_is_deterministic(self):
        p1 = project_id(self.root)
        p2 = project_id(self.root)
        self.assertEqual(p1, p2)
        self.assertEqual(len(p1), 20)
        # different roots -> different ids
        other = self.root + "_x"
        os.makedirs(other, exist_ok=True)
        self.assertNotEqual(p1, project_id(other) if os.path.exists(other)
                            else project_id(self.root + "/sub"))
        os.makedirs(os.path.join(self.root, "sub"), exist_ok=True)
        self.assertNotEqual(project_id(self.root),
                            project_id(os.path.join(self.root, "sub")))

    def test_stable_ids_are_deterministic(self):
        a = stable_id("x", "y", "z")
        b = stable_id("x", "y", "z")
        self.assertEqual(a, b)
        self.assertEqual(file_id("p", "a/b.py"), file_id("p", "a/b.py"))
        self.assertEqual(symbol_id("p", "f.py", "class", "m.C"),
                         symbol_id("p", "f.py", "class", "m.C"))

    def test_build_reports_counts_and_error_isolation(self):
        ix, q = self.build()
        self.assertEqual(ix.db_path, q.store.db_path)
        self.assertIn("main.py", self.res["errors"])   # syntax error isolated
        # .ai-engine itself, node_modules, *.log all excluded
        paths = [f["rel_path"] for f in q.file_manifest()]
        self.assertNotIn("node_modules/dep.js", paths)
        self.assertNotIn("debug.log", paths)
        self.assertNotIn(".ai-engine/project_index.db", paths)
        self.assertIn("pkg/core.py", paths)

    def test_rebuild_is_idempotent_and_deterministic(self):
        ix, q = self.build()
        first_symbols = q.symbols_in_file("pkg/core.py")
        first_info = q.project_info()
        ix.rebuild()
        q2 = IndexQueries(ix.db_path)
        second_symbols = q2.symbols_in_file("pkg/core.py")
        self.assertEqual(first_symbols, second_symbols)
        self.assertEqual(first_info["counts"], q2.project_info()["counts"])


class TestB2Discovery(TestProjectIndexBase):
    def test_ignore_matcher_negation(self):
        m = discovery.IgnoreMatcher(["*.pyc", "!keep.pyc", "build/"])
        self.assertTrue(m.is_ignored("x.pyc", is_dir=False))
        self.assertFalse(m.is_ignored("keep.pyc", is_dir=False))
        self.assertTrue(m.is_ignored("build", is_dir=True))
        self.assertFalse(m.is_ignored("build.py", is_dir=False))

    def test_relative_python_imports_resolved_as_facts(self):
        ix, q = self.build()
        # pkg/__init__ 'from .core import MathHelper' resolves to pkg.core
        init_imports = q.imports_of("pkg/__init__.py")
        facts = [e for e in init_imports if e["certainty"] == "fact"]
        self.assertTrue(any(e["target_name"].endswith("pkg/core.py")
                            for e in facts), init_imports)
        # MEDIUM-1 fix: `from .utils import helper` in pkg/core.py resolves to
        # the sibling pkg.utils (was previously off-by-one -> unresolved).
        core = q.imports_of("pkg/core.py")
        useful_fact = [e for e in core
                       if e["rel_type"] == "imports" and
                       e["certainty"] == "fact" and
                       e["target_name"].endswith("pkg/utils.py")]
        self.assertTrue(useful_fact, core)
        # absolute import remains unresolved (no stdlib module in the index)
        names = {e["target_name"] for e in core}
        self.assertIn("os", names)

    def test_binary_oversized_and_config(self):
        # a binary .py file is detected as unreadable (not parsed)
        with open(os.path.join(self.root, "binmod.py"), "wb") as f:
            f.write(b"\x00\x01\x02\x03")
        ix, q = self.build()
        binrec = q.find_file("binmod.py")
        self.assertEqual(binrec["parse_status"], "unreadable")
        # a binary .bin file has no extractor -> unsupported (not an error)
        binrec2 = q.find_file("data.bin")
        self.assertEqual(binrec2["language"], "other")
        self.assertEqual(binrec2["parse_status"], "unsupported")
        conf = q.find_file("pyproject.toml")
        self.assertEqual(conf["language"], "config")
        self.assertEqual(conf["parse_status"], "ok")

    def test_no_symlink_escape_from_root(self):
        target = os.path.join(self._tmp.name, "outside.txt")
        with open(target, "w") as f:
            f.write("secret")
        link = os.path.join(self.root, "escape.py")
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink not available")
        ix, q = self.build()
        # Build completes; every indexed rel_path stays within the workspace
        # root namespace (never an out-of-root path), and the indexer never
        # executes anything. Reading a symlinked file is read-only and safe.
        for f in q.file_manifest():
            self.assertFalse(os.path.isabs(f["rel_path"]))
            abspath = os.path.join(self.root, f["rel_path"])
            self.assertTrue(os.path.realpath(abspath).startswith(
                os.path.realpath(self.root)))


class TestB3PythonAst(TestProjectIndexBase):
    def test_symbols_extracted_with_line_spans_and_parents(self):
        ix, q = self.build()
        syms = q.symbols_in_file("pkg/core.py")
        kinds = {s["kind"] for s in syms}
        self.assertEqual(kinds, {"module", "class", "function", "method"})
        qnames = [s["qname"] for s in syms]
        self.assertIn("pkg.core", qnames)
        self.assertIn("pkg.core.MathHelper", qnames)
        self.assertIn("pkg.core.MathHelper.add", qnames)
        self.assertIn("pkg.core.top_level", qnames)
        # async method flagged
        total = [s for s in syms if s["qname"] == "pkg.core.MathHelper.total"]
        self.assertEqual(total[0].get("is_async") is True, True)
        # line spans ascending
        spans = [(s["line_start"], s["line_end"]) for s in syms]
        for a, b in zip(spans, spans[1:]):
            self.assertLessEqual(a[0], b[0])

    def test_method_parent_linkage(self):
        ix, q = self.build()
        add = next(s for s in q.symbols_in_file("pkg/core.py")
                   if s["qname"] == "pkg.core.MathHelper.add")
        self.assertTrue(add["parent_id"])

    def test_class_bases_in_detail(self):
        ix, q = self.build()
        child = None
        for s in q.symbols_in_file("pkg/core.py"):
            if s["qname"] == "pkg.core.Child":
                child = s
        self.assertIsNotNone(child)
        import json as _json
        detail = _json.loads(child["detail"]) if "detail" in child else {}
        self.assertIn("MathHelper", detail.get("bases", []))

    def test_inherits_candidate_edges(self):
        ix, q = self.build()
        inh = q.inherited_bases("pkg/core.py", "pkg.core.Child")
        # Child inherits MathHelper -> at least one candidate inherits edge
        self.assertTrue(any(e["rel_type"] == "inherits" for e in inh),
                        inh)

    def test_find_symbol_and_by_name(self):
        ix, q = self.build()
        res = q.find_symbol("pkg.core.MathHelper")
        # returns a list when scanning all kinds
        self.assertTrue(isinstance(res, list))
        self.assertTrue(any(s["kind"] == "class" for s in res))
        by_name = q.find_symbols_by_name("MathHelper")
        self.assertTrue(any(s["qname"] == "pkg.core.MathHelper" for s in
                            by_name))


class TestB6Queries(TestProjectIndexBase):
    def test_dependents_of(self):
        ix, q = self.build()
        deps = q.dependents_of("pkg.core")
        self.assertIn("pkg/__init__.py", deps)
        self.assertIn("tests/test_core.py", deps)

    def test_tests_for_conservative(self):
        ix, q = self.build()
        tests = q.tests_for("pkg/core.py")
        self.assertIn("tests/test_core.py", tests)

    def test_files_by_language(self):
        ix, q = self.build()
        py = q.files_by_language("python")
        self.assertTrue(all(f["language"] == "python" for f in py))
        configs = q.files_by_language("config")
        self.assertTrue(any(f["rel_path"] == "pyproject.toml" for f in configs))

    def test_find_file_none(self):
        ix, q = self.build()
        self.assertIsNone(q.find_file("does/not/exist.py"))

    def test_module_symbol_for_init(self):
        ix, q = self.build()
        mod = q.module_symbol("pkg/__init__.py")
        self.assertIsNotNone(mod)
        self.assertEqual(mod.qname, "pkg")

    def test_project_info(self):
        ix, q = self.build()
        info = q.project_info()
        self.assertGreaterEqual(info["counts"]["files"], 1)
        self.assertEqual(info["index_version"], "1")


class TestMediumFixes(unittest.TestCase):
    """Deterministic coverage for the three audited MEDIUM correctness fixes.

    These tests would FAIL against the pre-fix implementation:
    * ``_resolve_import`` off-by-one (same-package relative import resolution);
    * ``find_references``/``find_callers`` missing resolved module-import facts;
    * extraction-phase parse_status/parse_error not persisted back to `files`.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._tmp.name, "proj")
        os.makedirs(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def build(self):
        self.ix = ProjectIndex(self.root)
        self.res = self.ix.build()
        self.q = IndexQueries(self.ix.db_path)
        return self.res

    # ---- MEDIUM-1: relative import off-by-one ---------------------------

    def test_medium1_sibling_single_dot_resolves_to_same_package(self):
        # pkg.core does `from .utils import helper` -> must resolve to pkg.utils
        write_tree(self.root, {
            "pkg/core.py": "from .utils import helper\n",
            "pkg/utils.py": "def helper(x):\n    return x\n",
            "pkg/__init__.py": "",
        })
        self.build()
        edges = self.q.imports_of("pkg/core.py")
        fact = [e for e in edges if e["rel_type"] == "imports" and
                e["certainty"] == "fact" and
                e["target_name"].endswith("pkg/utils.py")]
        self.assertEqual(len(fact), 1, edges)

    def test_medium1_package_init_resolves_to_child(self):
        # pkg/__init__.py does `from .core import M` -> pkg.core (not "core")
        write_tree(self.root, {
            "pkg/__init__.py": "from .core import M\n",
            "pkg/core.py": "class M:\n    pass\n",
        })
        self.build()
        edges = self.q.imports_of("pkg/__init__.py")
        fact = [e for e in edges if e["rel_type"] == "imports" and
                e["certainty"] == "fact" and
                e["target_name"].endswith("pkg/core.py")]
        self.assertEqual(len(fact), 1, edges)

    def test_medium1_double_dot_ascends_to_parent_package(self):
        # pkg/sub/deep.py `from ..util import u` -> pkg.util
        write_tree(self.root, {
            "pkg/__init__.py": "",
            "pkg/sub/__init__.py": "",
            "pkg/sub/deep.py": "from ..util import u\n",
            "pkg/util.py": "def u():\n    return 1\n",
        })
        self.build()
        edges = self.q.imports_of("pkg/sub/deep.py")
        fact = [e for e in edges if e["rel_type"] == "imports" and
                e["certainty"] == "fact" and
                e["target_name"].endswith("pkg/util.py")]
        self.assertEqual(len(fact), 1, edges)

    def test_medium1_missing_relative_module_stays_candidate(self):
        # A relative import of a module that does NOT exist must stay a
        # candidate (never fabricated as a fact).
        write_tree(self.root, {
            "pkg/core.py": "from .missing import nope\n",
            "pkg/__init__.py": "",
        })
        self.build()
        edges = self.q.imports_of("pkg/core.py")
        self.assertTrue(any(e["rel_type"] == "imports" and
                            e["certainty"] == "candidate" and
                            e["target_name"] == "pkg.missing"
                            for e in edges), edges)
        self.assertFalse(any(e["certainty"] == "fact" for e in edges), edges)

    # ---- MEDIUM-2: find_references / find_callers on resolved imports -----

    def test_medium2_find_references_surfaces_resolved_import(self):
        # a.py imports b -> find_references('b') must return that import fact
        write_tree(self.root, {
            "a.py": "import b\n",
            "b.py": "def go():\n    return 1\n",
        })
        self.build()
        refs = self.q.find_references("b")
        import_facts = [e for e in refs
                        if e["rel_type"] == "imports" and
                        e["certainty"] == "fact"]
        self.assertEqual(len(import_facts), 1, refs)
        # deterministic ordering by edge id
        self.assertEqual(refs, sorted(refs, key=lambda e: e["id"]))

    def test_medium2_find_references_surfaces_package_import(self):
        # main.py `from pkg.core import M` -> find_references('pkg.core')
        write_tree(self.root, {
            "pkg/__init__.py": "",
            "pkg/core.py": "class M:\n    pass\n",
            "main.py": "from pkg.core import M\n",
        })
        self.build()
        refs = self.q.find_references("pkg.core")
        self.assertTrue(any(e["rel_type"] == "imports" and
                            e["certainty"] == "fact" for e in refs), refs)

    def test_medium2_find_callers_excludes_import_facts(self):
        # find_callers filters find_references down to REL_CALLS only: an
        # import fact must NEVER be reported as a call.
        write_tree(self.root, {
            "a.py": "import b\n",
            "b.py": "def go():\n    return 1\n",
        })
        self.build()
        refs = self.q.find_references("b")
        # the import fact IS discoverable after the MEDIUM-2 fix...
        self.assertTrue(any(e["rel_type"] == "imports" and
                            e["certainty"] == "fact" for e in refs), refs)
        # ...but find_callers('b') must not surface it (imports != calls).
        callers = self.q.find_callers("b")
        self.assertTrue(all(e["rel_type"] == "calls" for e in callers),
                        callers)
        self.assertFalse(any(e["rel_type"] == "imports" for e in callers),
                         callers)

    def test_medium2_no_fabrication_for_unreferenced_module(self):
        write_tree(self.root, {
            "a.py": "import b\n",
            "b.py": "def go():\n    return 1\n",
            "c.py": "def hi():\n    return 0\n",
        })
        self.build()
        self.assertEqual(self.q.find_references("c"), [])
        self.assertEqual(self.q.find_callers("c"), [])

    # ---- MEDIUM-3: persist extraction-phase parse_status/parse_error ------

    def test_medium3_syntax_error_is_persisted(self):
        write_tree(self.root, {"broken.py": "def f(:\n"})
        res = self.build()
        rec = self.q.find_file("broken.py")
        self.assertEqual(rec["parse_status"], "syntax_error")
        self.assertIn("broken.py", res["errors"])

    def test_medium3_decode_error_and_parse_aborted_persisted(self):
        # invalid-UTF-8 .py (no NUL, so discovery passes it as `ok` but the
        # extractor decode step fails) -> decode_error; a huge list -> aborted
        with open(os.path.join(self.root, "binmod.py"), "wb") as f:
            f.write(b"\xc3\x28 bad utf-8")  # invalid utf-8, no NUL byte
        # many-element (not deeply nested) list, small enough to pass the
        # discovery size cap but large enough to exceed the AST node guard
        big = ("x = [" + ",".join(["0"] * 200000) + "]\n").encode()
        with open(os.path.join(self.root, "huge2.py"), "wb") as f:
            f.write(big)
        res = self.build()
        self.assertEqual(self.q.find_file("binmod.py")["parse_status"],
                         "decode_error")
        self.assertEqual(self.q.find_file("huge2.py")["parse_status"],
                         "parse_aborted")
        self.assertEqual(sorted(res["errors"]),
                         ["binmod.py", "huge2.py"])

    def test_medium3_file_manifest_status_filter_uses_persisted_status(self):
        weirder = {
            "okmod.py": "def fine():\n    return 1\n",
            "badmod.py": "def oops(:\n",
        }
        write_tree(self.root, weirder)
        self.build()
        synth = [f["rel_path"] for f in
                 self.q.file_manifest(parse_status="syntax_error")]
        self.assertIn("badmod.py", synth)
        self.assertNotIn("okmod.py", synth)


if __name__ == "__main__":
    unittest.main()
