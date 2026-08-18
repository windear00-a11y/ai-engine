"""Regression tests for Knowledge Compiler extraction quality fixes.

Covers: procedure classification precision, ``.. testsetup::`` exclusion,
REPL-prompt / dependency normalization, local-module distinction, reference
deduplication and navigation filtering, external-link normalization, raw
evidence preservation, and deterministic repeated extraction.
"""

import json
import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from knowledge_compiler.adapters.rst import RSTAdapter
from knowledge_compiler.extractor import extract_candidates
from knowledge_compiler.pipeline import Pipeline


def parse_text(text):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "doc.rst")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return RSTAdapter().parse(path, "doc.rst", "fixtures")


def cands_of(doc, kind):
    return [c for c in extract_candidates(doc) if c.kind == kind]


class ProcedureFalsePositiveTests(unittest.TestCase):
    """Banned title patterns must never produce procedure candidates."""

    def _assert_no_procedure(self, text):
        doc = parse_text(text)
        self.assertEqual([c.summary for c in cands_of(doc, "procedure")], [],
                         "no procedure candidate expected")

    def test_using_heading_not_a_procedure(self):
        self._assert_no_procedure(
            "Using the Widget Tool\n=====================\n\n"
            ".. code-block:: python\n\n"
            "   widget = Widget()\n")

    def test_more_on_heading_not_a_procedure(self):
        self._assert_no_procedure(
            "Guide\n=====\n\nMore on Lists\n-------------\n\n"
            ".. code-block:: python\n\n"
            "   x = [1, 2]\n   x.append(3)\n")

    def test_more_on_modules_not_a_procedure(self):
        self._assert_no_procedure(
            "Guide\n=====\n\nMore on Modules\n---------------\n\n"
            ".. code-block:: python\n\n   import sys\n")

    def test_an_informal_introduction_not_a_procedure(self):
        self._assert_no_procedure(
            "An Informal Introduction to Python\n===================================\n\n"
            ".. code-block:: python\n\n   print(1 + 1)\n")

    def test_first_steps_not_a_procedure(self):
        self._assert_no_procedure(
            "Guide\n=====\n\nFirst Steps Towards Programming\n-------------------------------\n\n"
            ".. code-block:: python\n\n   print(\"hi\")\n")

    def test_invoking_not_a_procedure(self):
        self._assert_no_procedure(
            "Guide\n=====\n\nInvoking the Interpreter\n------------------------\n\n"
            ".. code-block:: console\n\n   $ python3\n")

    def test_heading_alone_is_never_a_procedure(self):
        # Strong marker but only prose in the body: no procedural structure.
        self._assert_no_procedure(
            "Defining Functions\n==================\n\n"
            "Functions are reusable blocks of code introduced with the def\n"
            "keyword. This paragraph only explains them; there are no steps\n"
            "and no examples in this section.\n")


class ProcedureGenuineTests(unittest.TestCase):
    """Strong markers WITH procedural body evidence stay procedures."""

    def test_defining_functions_with_body(self):
        doc = parse_text(
            "Guide\n=====\n\nDefining Functions\n------------------\n\n"
            "Example:\n\n"
            ".. code-block:: python\n\n"
            "   def f(x):\n       return x + 1\n")
        procs = cands_of(doc, "procedure")
        self.assertEqual([c.summary for c in procs], ["Defining Functions"])

    def test_creating_virtual_environments(self):
        doc = parse_text(
            "Guide\n=====\n\nCreating Virtual Environments\n-----------------------------\n\n"
            "Run:\n\n"
            ".. code-block:: console\n\n"
            "   $ python -m venv tutorial-env\n"
            "   $ source tutorial-env/bin/activate\n")
        procs = cands_of(doc, "procedure")
        self.assertEqual([c.summary for c in procs],
                         ["Creating Virtual Environments"])

    def test_reading_and_writing_files(self):
        doc = parse_text(
            "Guide\n=====\n\nReading and Writing Files\n-------------------------\n\n"
            ".. code-block:: python\n\n"
            "   with open(\"x.txt\") as f:\n       data = f.read()\n")
        procs = cands_of(doc, "procedure")
        self.assertEqual([c.summary for c in procs],
                         ["Reading and Writing Files"])

    def test_procedure_list_body_counts_as_evidence(self):
        doc = parse_text(
            "Guide\n=====\n\nCreating a Widget\n-----------------\n\n"
            "Steps:\n\n#. Open the configuration file.\n#. Set the option.\n")
        procs = cands_of(doc, "procedure")
        self.assertEqual([c.summary for c in procs], ["Creating a Widget"])

    def test_procedure_evidence_is_the_heading(self):
        doc = parse_text(
            "Guide\n=====\n\nDefining Clean-up Actions\n-------------------------\n\n"
            ".. code-block:: python\n\n   try:\n       pass\n")
        procs = cands_of(doc, "procedure")
        self.assertEqual(procs[0].evidence, "Defining Clean-up Actions")


class CapiDeclarationTests(unittest.TestCase):
    def test_c_function_directive_produces_api_declaration(self):
        doc = parse_text(
            "C API\n=====\n\n"
            ".. c:function:: PyObject* PyArena_New(PyArena *arena)\n\n"
            "   Return a new arena.\n")
        api = cands_of(doc, "api_declaration")
        self.assertEqual(len(api), 1)
        self.assertEqual(api[0].meta["directive"], "c:function")
        self.assertEqual(api[0].meta["name"], "PyArena_New")
        self.assertEqual(api[0].summary, "PyObject* PyArena_New(PyArena *arena)")
        self.assertIn(".. c:function::", api[0].evidence)

    def test_c_type_macro_member_and_var_directives(self):
        doc = parse_text(
            "C API\n=====\n\n"
            ".. c:type:: PyObject\n\n"
            ".. c:macro:: Py_RETURN_NONE\n\n"
            ".. c:member:: PyTypeObject.tp_dictoffset\n\n"
            ".. c:var:: Py_RunMain\n\n"
            ".. c:data:: PyBool_Type\n")
        api = cands_of(doc, "api_declaration")
        by_dir = {c.meta["directive"]: c for c in api}
        self.assertEqual(set(by_dir),
                         {"c:type", "c:macro", "c:member", "c:var", "c:data"})
        self.assertEqual(by_dir["c:type"].meta["name"], "PyObject")
        self.assertEqual(by_dir["c:macro"].meta["name"], "Py_RETURN_NONE")
        self.assertEqual(by_dir["c:member"].meta["name"],
                         "PyTypeObject.tp_dictoffset")

    def test_c_function_return_type_not_in_name(self):
        doc = parse_text(
            "C API\n=====\n\n"
            ".. c:function:: struct _typeobject *PyType_FromSpec(PyType_Spec *spec)\n")
        api = cands_of(doc, "api_declaration")
        self.assertEqual(api[0].meta["name"], "PyType_FromSpec")

    def test_ordinary_reference_not_an_api_declaration(self):
        # References to C symbols must NOT become declarations.
        doc = parse_text(
            "Guide\n=====\n\nSee :c:func:`PyArena_New` in the docs.\n")
        api = cands_of(doc, "api_declaration")
        self.assertEqual(api, [])
        refs = cands_of(doc, "reference")
        self.assertTrue(any(c.meta.get("role") == "c:func"
                            for c in refs))


class NonExampleCodeTests(unittest.TestCase):
    def test_test_generated_sources_excluded_from_code_examples(self):
        doc = parse_text(
            "Guide\n=====\n\n"
            ".. testsetup:: python\n\n   hidden = 1\n\n"
            ".. testcode:: python\n\n   hidden = 2\n\n"
            ".. testoutput:: python\n\n   ok\n\n"
            ".. productionlist::\n\n   stmt: simple_stmt\n")
        self.assertEqual(cands_of(doc, "code_example"), [])
        # raw chunks survive in the EXTRACTED layer with their provenance
        chunks = [c for c in doc.chunks if c.kind == "code_block"]
        self.assertTrue(chunks)
        self.assertEqual(len(cands_of(doc, "code_example")), 0)

    def test_normal_examples_still_emitted(self):
        doc = parse_text(
            "Guide\n=====\n\n"
            ".. code-block:: python\n\n   x = 1\n\n"
            ".. doctest::\n\n   >>> 1 + 1\n   2\n")
        examples = cands_of(doc, "code_example")
        sources = {c.meta["source"] for c in examples}
        self.assertIn("code-block", sources)
        self.assertIn("doctest", sources)


class ShellTextCodeTests(unittest.TestCase):
    def test_shell_session_not_a_code_example(self):
        doc = parse_text(
            "Guide\n=====\n\nRun:\n\n"
            ".. code-block:: shell-session\n\n"
            "   $ python -m pip install .\n"
            "   $ python setup.py build\n")
        self.assertEqual(cands_of(doc, "code_example"), [])

    def test_dollar_prompt_literal_not_a_code_example(self):
        doc = parse_text(
            "Guide\n=====\n\nExample session::\n\n"
            "   $ ls\n   file1 file2\n   $ python x.py\n")
        self.assertEqual(cands_of(doc, "code_example"), [])

    def test_text_language_not_a_code_example(self):
        doc = parse_text(
            "Guide\n=====\n\n"
            "Output:\n\n.. code-block:: text\n\n   python3.16\n")
        self.assertEqual(cands_of(doc, "code_example"), [])

    def test_shebang_script_not_a_code_example(self):
        doc = parse_text(
            "Guide\n=====\n\nScript::\n\n   #!/usr/bin/env python3\n")
        self.assertEqual(cands_of(doc, "code_example"), [])

    def test_python_doctest_and_code_still_examples(self):
        doc = parse_text(
            "Guide\n=====\n\n"
            ".. code-block:: python\n\n   def f():\n       return 1\n\n"
            ".. doctest::\n\n   >>> f()\n   1\n")
        self.assertTrue(cands_of(doc, "code_example"))

    def test_shell_example_stays_in_extracted(self):
        doc = parse_text(
            "Guide\n=====\n\nExample::\n\n   $ ls\n")
        chunks = [c for c in doc.chunks
                  if c.kind == "code_block" and c.meta.get("source") == "literal"]
        self.assertEqual(len(chunks), 1)
        self.assertIn("$ ls", chunks[0].content)


class DependencyNormalizationTests(unittest.TestCase):
    def test_dotted_stdlib_submodule_recognized(self):
        doc = parse_text(
            "Guide\n=====\n\n"
            ".. code-block:: python\n\n"
            "   from urllib.request import urlopen\n"
            "   from collections.abc import Mapping\n"
            "   import concurrent.futures\n")
        deps = {d.meta["module"]: d for d in cands_of(doc, "dependency")}
        self.assertEqual(deps["urllib.request"].confidence, "high")
        self.assertEqual(deps["urllib.request"].meta["origin"], "stdlib")
        self.assertFalse(deps["urllib.request"].meta["local"])
        self.assertEqual(deps["collections.abc"].confidence, "high")
        self.assertEqual(deps["concurrent.futures"].confidence, "high")

    def test_future_import_is_not_a_dependency(self):
        doc = parse_text(
            "Guide\n=====\n\n"
            ".. code-block:: python\n\n"
            "   from __future__ import annotations\n")
        self.assertEqual(cands_of(doc, "dependency"), [])

    def test_local_module_stays_low_confidence(self):
        doc = parse_text(
            "Guide\n=====\n\n"
            ".. code-block:: python\n\n   import emb\n")
        deps = cands_of(doc, "dependency")
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0].confidence, "low")
        self.assertEqual(deps[0].meta["origin"], "unknown")
        self.assertTrue(deps[0].meta["local"])


class ProcedureTitleExclusionTests(unittest.TestCase):
    def test_document_title_never_a_procedure(self):
        # A whole-document how-to title with procedural body is NOT emitted;
        # only sections qualify.
        doc = parse_text(
            "Creating a Widget\n=================\n\n"
            "Steps:\n\n#. Configure options.\n#. Save.\n")
        self.assertEqual(cands_of(doc, "procedure"), [])

    def test_section_heading_still_a_procedure(self):
        doc = parse_text(
            "Guide\n=====\n\nCreating a Widget\n-----------------\n\n"
            "Steps:\n\n#. Configure options.\n#. Save.\n")
        procs = cands_of(doc, "procedure")
        self.assertEqual([c.summary for c in procs], ["Creating a Widget"])


class CodeExampleTests(unittest.TestCase):
    def test_testsetup_excluded_from_code_examples(self):
        doc = parse_text(
            "Guide\n=====\n\nSetup:\n\n.. testsetup:: python\n\n"
            "   import math\n\nExamples:\n\n"
            ".. code-block:: python\n\n   print(math.pi)\n\n"
            ".. doctest::\n\n   >>> 1 + 1\n   2\n")
        examples = cands_of(doc, "code_example")
        # normal examples survive with their provenance
        self.assertTrue(examples)
        sources = {c.meta["source"] for c in examples}
        self.assertIn("code-block", sources)
        self.assertIn("doctest", sources)
        # testsetup never becomes a user-facing example
        self.assertNotIn("testsetup", sources)
        # raw testsetup chunk is preserved in the EXTRACTED layer
        testsetup = [c for c in doc.chunks
                     if c.kind == "code_block"
                     and c.meta.get("source") == "testsetup"]
        self.assertEqual(len(testsetup), 1)
        self.assertIn("import math", testsetup[0].content)
        self.assertIn("import math", testsetup[0].evidence)


class DependencyTests(unittest.TestCase):
    def test_repl_prompt_normalized(self):
        doc = parse_text(
            "Guide\n=====\n\n.. doctest::\n\n   >>> import fibo\n")
        deps = cands_of(doc, "dependency")
        self.assertEqual(len(deps), 1)
        d = deps[0]
        self.assertEqual(d.summary, "import fibo")
        self.assertEqual(d.meta["import"], "import fibo")
        self.assertEqual(d.meta["module"], "fibo")
        self.assertNotIn(">>>", d.summary)

    def test_local_example_module_not_trusted_external(self):
        doc = parse_text(
            "Guide\n=====\n\n"
            ".. code-block:: python\n\n   import math\n\n"
            ".. doctest::\n\n   >>> import fibo\n")
        deps = {d.meta["module"]: d for d in cands_of(doc, "dependency")}
        fibo = deps["fibo"]
        # an example/local module is NOT automatically a trusted dependency
        self.assertEqual(fibo.confidence, "low")
        self.assertEqual(fibo.meta["origin"], "unknown")
        self.assertTrue(fibo.meta["local"])
        math = deps["math"]
        self.assertEqual(math.confidence, "high")
        self.assertEqual(math.meta["origin"], "stdlib")
        self.assertFalse(math.meta["local"])

    def test_dependency_evidence_preserved(self):
        doc = parse_text(
            "Guide\n=====\n\n.. doctest::\n\n   >>> import fibo\n")
        d = cands_of(doc, "dependency")[0]
        self.assertIn(">>> import fibo", d.evidence)


class ReferenceTests(unittest.TestCase):
    def test_reference_deduplication(self):
        doc = parse_text(
            "Guide\n=====\n\n"
            "Use :func:`len` here and :func:`len` again, plus :func:`range`.\n")
        refs = cands_of(doc, "reference")
        funcs = [c for c in refs if c.meta.get("role") == "func"]
        self.assertEqual(len(funcs), 2)
        by_target = [c.meta["target"] for c in funcs]
        self.assertEqual(sorted(by_target), ["len", "range"])

    def test_navigation_and_ui_roles_filtered(self):
        doc = parse_text(
            "Guide\n=====\n\n"
            "See :ref:`tut-private` and :file:`/etc/hosts` and :kbd:`Ctrl-C`\n"
            "and :option:`-n` and :func:`len`.\n")
        refs = cands_of(doc, "reference")
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].meta["role"], "func")
        self.assertEqual(refs[0].meta["target"], "len")

    def test_raw_reference_evidence_preserved_in_extracted_layer(self):
        doc = parse_text(
            "Guide\n=====\n\n"
            "See :ref:`tut-private` and :func:`len`.\n")
        chunks = [c for c in doc.chunks if c.kind == "reference"]
        self.assertEqual(len(chunks), 2)
        evidence = {c.evidence for c in chunks}
        self.assertIn(":ref:`tut-private`", evidence)
        self.assertIn(":func:`len`", evidence)


class LinkTests(unittest.TestCase):
    def test_external_link_normalization(self):
        doc = parse_text(
            "Guide\n=====\n\n"
            "Read `The Perils of Floating Point\n<https://example.com/perils>`_.\n")
        links = [c for c in cands_of(doc, "reference")
                 if c.meta.get("kind") == "link"]
        self.assertEqual(len(links), 1)
        lnk = links[0]
        self.assertEqual(lnk.summary, "The Perils of Floating Point")
        self.assertEqual(lnk.meta["url"], "https://example.com/perils")
        self.assertEqual(lnk.meta["text"], "The Perils of Floating Point")
        self.assertNotIn("example.com", lnk.summary)
        # raw multi-line evidence is preserved verbatim
        self.assertIn("\n", lnk.evidence)

    def test_link_text_whitespace_collapsed(self):
        doc = parse_text(
            "Guide\n=====\n\n"
            "Read `A  wide   link <https://example.com/wide>`_.\n")
        links = [c for c in cands_of(doc, "reference")
                 if c.meta.get("kind") == "link"]
        self.assertEqual(links[0].summary, "A wide link")


class DeterministicReextractionTests(unittest.TestCase):
    def test_repeated_extraction_is_identical(self):
        corpus = {
            "a.rst": (
                "Creating a Widget\n=================\n\n"
                "Steps:\n\n#. Configure options.\n#. Save.\n\n"
                ".. code-block:: python\n\n   import math\n   w = Widget()\n"
                "See :func:`len` and :ref:`guide`.\n"),
            "b.rst": (
                "More on Lists\n=============\n\n"
                ".. testsetup::\n\n   import math\n\n"
                ".. doctest::\n\n   >>> import fibo\n"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src")
            os.makedirs(src)
            for name, text in corpus.items():
                with open(os.path.join(src, name), "w") as f:
                    f.write(text)
            outs = [os.path.join(tmp, "o1"), os.path.join(tmp, "o2")]
            for out in outs:
                results = Pipeline(output_root=out).candidates(src)
                self.assertTrue(results)
                for r in results:
                    self.assertTrue(all(v.valid for v in r["records"]))
            def load(out):
                data = {}
                base = os.path.join(out, "candidates")
                for name in os.listdir(base):
                    with open(os.path.join(base, name)) as f:
                        data[name] = json.load(f)
                return data
            self.assertEqual(load(outs[0]), load(outs[1]))


if __name__ == "__main__":
    unittest.main()
